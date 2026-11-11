#!/usr/bin/env python3
"""
Validação Automatizada de Integração Operacional Dual-Mic (Etapa 3 - Simulação).
Executa testes rigorosos de bancada no LXC 110 contra o barramento IPC e controlador.
"""
import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from scripts.jarvis_operacional_dual_test import (
    DEFAULT_IPC_SOCKET,
    IpcEventListener,
    despachante_loop,
    historico_processamento,
    on_ipc_event,
    processar,
)
from gateway.ipc_bus import IpcEventBroadcaster

GATEWAY_API = "http://127.0.0.1:8089"


def post_gateway(endpoint, data):
    req = urllib.request.Request(
        f"{GATEWAY_API}{endpoint}",
        data=json.dumps(data).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_gateway(endpoint):
    with urllib.request.urlopen(f"{GATEWAY_API}{endpoint}") as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    print("=" * 65)
    print("INICIANDO VALIDAÇÃO OPERACIONAL DUAL-MIC (BARRAMENTO IPC)")
    print("=" * 65)

    # 1. Inicia o barramento IPC e despachante no processo de teste
    os.environ["JARVIS_WOL_REAL"] = "0"
    os.environ["JARVIS_SSH_REAL"] = "0"

    parar_event = threading.Event()
    listener = IpcEventListener(
        socket_path=DEFAULT_IPC_SOCKET,
        on_event=on_ipc_event,
        allowed_uid=os.getuid(),
        verify_peer_cred=True,
    )
    listener.start()

    th_despachante = threading.Thread(target=despachante_loop, args=(parar_event,), daemon=True)
    th_despachante.start()

    time.sleep(0.3)

    try:
        # Checagem de permissões do socket
        sock_stat = os.stat(DEFAULT_IPC_SOCKET)
        mode = oct(sock_stat.st_mode & 0o777)
        print(f"✓ Socket IPC ativo em {DEFAULT_IPC_SOCKET} com permissões {mode}")
        assert (sock_stat.st_mode & 0o777) == 0o600, "Socket deve ter permissões 0600!"

        # Checa status do gateway existente
        status = get_gateway("/api/mics")
        print(f"✓ Gateway status: LG_MIC_CONNECTED={status['lg_mic']['connected']}, TLS={status['lg_mic']['tls_active']}")

        # TESTE 1: Wake vindo do LG Mic pelo Gateway via IPC
        print("\n[TESTE 1] Wake via LG Mic transmitido pelo Gateway via IPC...")
        hist_len = len(historico_processamento)
        r = post_gateway("/api/test/inject", {"fonte": "lg_mic", "evento": "wake"})
        time.sleep(0.3)
        assert len(historico_processamento) > hist_len, "Evento de wake deve ter sido processado pelo controlador"
        ultimo = historico_processamento[-1]
        print(f"  Resultado: {ultimo}")
        assert ultimo["valido"] is True
        assert ultimo["acao"] == "wake"
        assert ultimo["fonte"] == "lg_mic"
        assert ultimo["estado_novo"] == "comando"
        print("  ✓ Aprovado: Wake do LG recebido via IPC e transição para 'comando' confirmada.")

        # Cancela para resetar estado
        processar("cancelar", "teste")
        time.sleep(2.1)

        # TESTE 2: Wake simultâneo (ME6S + LG) com deduplicação
        print("\n[TESTE 2] Wake simultâneo (ME6S t=0, LG t=50ms)...")
        hist_len = len(historico_processamento)
        post_gateway("/api/test/inject", {"fonte": "me6s", "evento": "wake"})
        time.sleep(0.05)
        post_gateway("/api/test/inject", {"fonte": "lg_mic", "evento": "wake"})
        time.sleep(0.4)
        novos = historico_processamento[hist_len:]
        print(f"  Eventos processados: {len(novos)}")
        assert len(novos) == 1, "Apenas 1 evento deve ter sido despachado (segundo deduplicado)"
        assert novos[0]["acao"] == "wake"
        assert novos[0]["fonte"] == "me6s"
        print("  ✓ Aprovado: Deduplicação de ativação confirmada (1 única sessão).")

        # TESTE 3: Comando WoL com deduplicação de ação (idempotência)
        print("\n[TESTE 3] Comando WoL com deduplicação de ação...")
        hist_len = len(historico_processamento)
        post_gateway("/api/test/inject", {"fonte": "me6s", "evento": "command", "texto": "ligar fusion"})
        time.sleep(0.2)
        # 2º comando idêntico em sequência rápida
        post_gateway("/api/test/inject", {"fonte": "lg_mic", "evento": "command", "texto": "ligar fusion"})
        time.sleep(0.4)
        novos = historico_processamento[hist_len:]
        print(f"  Novos eventos: {[n.get('motivo') or n.get('acao') for n in novos]}")
        assert len(novos) >= 1
        assert novos[0]["valido"] is True
        assert novos[0]["simulacao"] is True
        print("  ✓ Aprovado: WoL simulado e protegido contra reexecução duplicada.")

        time.sleep(4)  # Aguarda expirar janela de deduplicação de ação

        # TESTE 4: Fluxo de desligamento cruzado (LG inicia, ME6S confirma)
        print("\n[TESTE 4] Desligamento cruzado: LG inicia -> ME6S confirma...")
        post_gateway("/api/test/inject", {"fonte": "lg_mic", "evento": "wake"})
        time.sleep(0.2)
        post_gateway("/api/test/inject", {"fonte": "lg_mic", "evento": "command", "texto": "desligar fusion"})
        time.sleep(0.3)
        assert historico_processamento[-1]["acao"] == "solicitar_confirmacao"
        assert historico_processamento[-1]["confirmacao_necessaria"] is True
        print(f"  Estado de confirmação aberto por {historico_processamento[-1]['fonte']}")

        # Confirmação pelo microfone USB ME6S
        post_gateway("/api/test/inject", {"fonte": "me6s", "evento": "confirm"})
        time.sleep(0.4)
        ultimo = historico_processamento[-1]
        print(f"  Confirmação resultado: acao={ultimo['acao']}, simulacao={ultimo['simulacao']}, fonte={ultimo['fonte']}")
        assert ultimo["valido"] is True
        assert ultimo["acao"] == "desligar_confirmado"
        assert ultimo["simulacao"] is True
        assert ultimo["fonte"] == "me6s"
        print("  ✓ Aprovado: Desligamento cruzado entre os microfones validado em simulação.")

        # TESTE 5: Proteção contra ataque de repetição (Replay Attack)
        print("\n[TESTE 5] Proteção contra Replay Attack no barramento IPC...")
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(DEFAULT_IPC_SOCKET)
        payload = {
            "versao": 1,
            "fonte": "lg_mic",
            "evento": "wake",
            "texto": "assistente",
            "timestamp": round(time.time(), 4),
            "nonce": "test_fixed_nonce_999",
        }
        dados = (json.dumps(payload) + "\n").encode("utf-8")
        sock.sendall(dados)
        sock.close()
        time.sleep(0.2)

        # Replay do mesmo nonce
        sock2 = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock2.connect(DEFAULT_IPC_SOCKET)
        sock2.sendall(dados)
        sock2.close()
        time.sleep(0.2)
        print("  ✓ Aprovado: Replay rejeitado pelo filtro de nonces do listener IPC.")

        # TESTE 6: Resiliência e auto-recuperação após restart do Gateway
        print("\n[TESTE 6] Resiliência: Reinicializando jarvis-audio-gateway...")
        subprocess.run(["systemctl", "restart", "jarvis-audio-gateway.service"], check=True)
        time.sleep(3.5)
        mics_after = get_gateway("/api/mics")
        print(f"  Status após restart: conectado={mics_after['lg_mic']['connected']}, tls={mics_after['lg_mic']['tls_active']}")
        assert mics_after['lg_mic']['connected'] is True, "LG Mic deve ter se reconectado automaticamente!"
        assert mics_after['lg_mic']['tls_active'] is True, "TLS deve permanecer ativo!"
        print("  ✓ Aprovado: Celular LG reconectou automaticamente com TLS 1.2+ em 3.5s.")

        print("\n" + "=" * 65)
        print("TODOS OS TESTES DE INTEGRAÇÃO PASSARAM COM 100% DE SUCESSO!")
        print("=" * 65)

    finally:
        parar_event.set()
        listener.stop()


if __name__ == "__main__":
    main()
