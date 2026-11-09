#!/usr/bin/env python3
"""Jarvis Voice: implementação de referência independente do painel Flask.

ATENÇÃO: não é uma exportação exata do script operacional do laboratório.
Por padrão, todas as ações de energia ficam em SIMULAÇÃO.
"""
from __future__ import annotations

import json
import os
import queue
import socket
import subprocess
import threading
import time
from pathlib import Path

from vosk import KaldiRecognizer, Model, SetLogLevel


BASE = Path(__file__).resolve().parent
CONFIG_FILE = Path(os.getenv("JARVIS_CONFIG", str(BASE / "config.json")))
WOL_REAL = os.getenv("JARVIS_WOL_REAL", "0") == "1"
SSH_REAL = os.getenv("JARVIS_SSH_REAL", "0") == "1"
TEMPO_COMANDO = 12
TEMPO_CONFIRMACAO = 20
MAX_ATRASO_AUDIO = 1.5


def carregar_configuracao():
    if not CONFIG_FILE.is_file():
        raise SystemExit(
            f"Configuração não encontrada: {CONFIG_FILE}. "
            "Copie config.example.json para config.json e ajuste seus dados."
        )
    with CONFIG_FILE.open(encoding="utf-8") as arquivo:
        config = json.load(arquivo)
    caminho_modelo = Path(config["model_path"])
    if not caminho_modelo.is_dir():
        raise SystemExit(f"Modelo Vosk não encontrado: {caminho_modelo}")
    if not config.get("computers"):
        raise SystemExit("Nenhum computador configurado.")
    if not config.get("audio_device"):
        raise SystemExit("Configure audio_device.")
    return config


def montar_comandos(config):
    comandos = {}
    for computador in config["computers"]:
        for apelido in computador["aliases"]:
            nome = apelido.strip().lower()
            for acao in ("ligar", "desligar"):
                frase = f"{acao} {nome}"
                if frase in comandos:
                    raise ValueError(f"Comando duplicado: {frase}")
                comandos[frase] = (acao, computador)
    return comandos


def acessivel(computador):
    """Uma resposta de rede indica acesso; ausência não prova desligamento."""
    ip = computador["ip"]
    for porta in computador.get("probe_ports", [22]):
        try:
            with socket.create_connection((ip, int(porta)), timeout=0.7):
                return True
        except (OSError, ValueError):
            continue
    try:
        teste = subprocess.run(
            ["ping", "-n", "-c", "1", "-W", "1", ip],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
            check=False,
        )
        return teste.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def acordar(computador):
    mac = computador["mac"].replace(":", "").replace("-", "").strip()
    if len(mac) != 12:
        raise ValueError("Endereço MAC inválido.")
    try:
        endereco = bytes.fromhex(mac)
    except ValueError as erro:
        raise ValueError("Endereço MAC inválido.") from erro
    pacote = b"\xff" * 6 + endereco * 16
    destino = computador.get("broadcast", "255.255.255.255")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(pacote, (destino, 9))


def desligar_por_ssh(computador):
    alvo = computador["ssh_target"].strip()
    # Impede que uma configuração inválida vire argumento/opção de SSH.
    if alvo.startswith("-") or " " in alvo or "\n" in alvo or "@" not in alvo:
        raise ValueError("ssh_target inválido.")
    comando = computador["shutdown_command"].strip()
    if not comando:
        raise ValueError("shutdown_command vazio.")
    retorno = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
         alvo, comando],
        capture_output=True,
        text=True,
        timeout=25,
        check=False,
    )
    print("SSH: código de retorno", retorno.returncode, flush=True)
    if retorno.returncode:
        print(
            "SSH não confirmou sucesso. Verifique o computador. "
            + retorno.stderr.strip()[:400],
            flush=True,
        )


def main():
    config = carregar_configuracao()
    comandos = montar_comandos(config)
    SetLogLevel(-1)
    modelo = Model(config["model_path"])
    gramatica = [
        "assistente",
        "confirmar",
        "confirmar desligar",
        "confirmar desligamento",
        "cancelar",
        *comandos.keys(),
        "[unk]",
    ]
    reconhecedor = KaldiRecognizer(
        modelo, 16000, json.dumps(gramatica, ensure_ascii=False)
    )

    processo = subprocess.Popen(
        [
            "arecord", "-q", "-D", config["audio_device"],
            "-f", "S16_LE", "-r", "16000", "-c", "1", "-t", "raw",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    fila = queue.Queue(maxsize=16)
    estado = "espera"
    limite = 0.0
    pendente = None

    def esvaziar_fila():
        while True:
            try:
                fila.get_nowait()
            except queue.Empty:
                break
        reconhecedor.Reset()

    def capturar():
        while True:
            bloco = processo.stdout.read(4000)
            if not bloco:
                break
            item = (time.monotonic(), bloco)
            try:
                fila.put_nowait(item)
            except queue.Full:
                try:
                    fila.get_nowait()
                except queue.Empty:
                    pass
                try:
                    fila.put_nowait(item)
                except queue.Full:
                    pass
        try:
            fila.put_nowait(None)
        except queue.Full:
            pass

    threading.Thread(target=capturar, name="captura-alsa", daemon=True).start()

    def limpar_estado():
        nonlocal estado, limite, pendente
        estado = "espera"
        limite = 0.0
        pendente = None

    def processar(texto):
        nonlocal estado, limite, pendente
        agora = time.monotonic()

        if estado != "espera" and agora > limite:
            limpar_estado()
            print("Tempo esgotado; pedido cancelado.", flush=True)
            return

        print("Ouvi:", texto, flush=True)
        if texto == "cancelar":
            limpar_estado()
            print("Cancelado.", flush=True)
            return

        if estado == "espera":
            if texto == "assistente":
                estado = "comando"
                limite = agora + TEMPO_COMANDO
                print("ATIVADO! Aguardando comando.", flush=True)
            return

        if estado == "comando":
            if texto == "assistente":
                print("Já ativado. Aguardando comando.", flush=True)
                return
            if texto not in comandos:
                print("Comando ignorado.", flush=True)
                return

            acao, computador = comandos[texto]
            print("Computador:", computador["name"], flush=True)
            esta_acessivel = acessivel(computador)
            esvaziar_fila()

            if acao == "ligar":
                limpar_estado()
                if esta_acessivel:
                    print("Computador já está acessível.", flush=True)
                elif WOL_REAL:
                    try:
                        acordar(computador)
                        print("Pacote Wake-on-LAN enviado.", flush=True)
                    except Exception as erro:
                        print("Erro no Wake-on-LAN:", erro, flush=True)
                else:
                    print("SIMULAÇÃO: enviaria Wake-on-LAN.", flush=True)
                return

            if not esta_acessivel:
                limpar_estado()
                print("Desligamento bloqueado: host não acessível.", flush=True)
                return

            estado = "confirmacao"
            pendente = computador
            limite = time.monotonic() + TEMPO_CONFIRMACAO
            print(
                'Confirme desligamento de '
                + computador["name"]
                + ': diga "confirmar desligamento" ou "cancelar".',
                flush=True,
            )
            return

        if estado == "confirmacao":
            confirmacoes = {
                "confirmar", "confirmar desligar", "confirmar desligamento"
            }
            if texto not in confirmacoes:
                print("Aguardando confirmação válida.", flush=True)
                return
            computador = pendente
            limpar_estado()

            if SSH_REAL and texto != "confirmar desligamento":
                print("Confirmação insuficiente. Pedido cancelado.", flush=True)
                return

            if not acessivel(computador):
                print("Cancelado: computador não está acessível.", flush=True)
                esvaziar_fila()
                return

            esvaziar_fila()
            if not SSH_REAL:
                print("SIMULAÇÃO: enviaria desligamento SSH.", flush=True)
                return
            try:
                desligar_por_ssh(computador)
            except Exception as erro:
                print("Erro no SSH:", erro, flush=True)
            finally:
                esvaziar_fila()

    print("=== JARVIS VOICE ===", flush=True)
    print(
        f"Wake-on-LAN: {'REAL' if WOL_REAL else 'SIMULAÇÃO'} | "
        f"SSH: {'REAL' if SSH_REAL else 'SIMULAÇÃO'}",
        flush=True,
    )
    print("Diga 'assistente', pause, depois o comando.", flush=True)

    try:
        while True:
            try:
                item = fila.get(timeout=1)
            except queue.Empty:
                if processo.poll() is not None:
                    print("Captura de áudio encerrada.", flush=True)
                    break
                if estado != "espera" and time.monotonic() > limite:
                    limpar_estado()
                    print("Tempo esgotado; pedido cancelado.", flush=True)
                continue
            if item is None:
                break
            instante, audio = item
            if time.monotonic() - instante > MAX_ATRASO_AUDIO:
                reconhecedor.Reset()
                limpar_estado()
                print("Áudio atrasado descartado.", flush=True)
                continue
            if reconhecedor.AcceptWaveform(audio):
                texto = json.loads(reconhecedor.Result()).get("text", "").strip()
                if texto and texto != "[unk]":
                    processar(texto)
    except KeyboardInterrupt:
        print("Jarvis interrompido.", flush=True)
    finally:
        if processo.poll() is None:
            processo.terminate()
        try:
            processo.wait(timeout=3)
        except subprocess.TimeoutExpired:
            processo.kill()
            processo.wait()


if __name__ == "__main__":
    main()
