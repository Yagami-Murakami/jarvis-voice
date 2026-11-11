#!/usr/bin/env python3
"""
Test Harness do Controlador Operacional Jarvis com Suporte a Microfone Duplo (IPC).

Utilizado EXCLUSIVAMENTE para validação controlada em simulação.
NÃO altera comandos reais nem substitui o serviço de produção antes de aprovação.
"""
import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

# Adiciona caminhos do painel e do gateway
sys.path.insert(0, "/home/tuninho/homelab-panel")
sys.path.insert(0, "/opt/jarvis-voice")

try:
    import app as painel
except ImportError:
    painel = None

from gateway.ipc_bus import DEFAULT_IPC_SOCKET, IpcEventListener

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [JarvisOperacionalDual] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("JarvisOperacionalDual")

COMANDOS = {
    "ligar fusion": ("ligar", "fusion"),
    "ligar o fusion": ("ligar", "fusion"),
    "desligar fusion": ("desligar", "fusion"),
    "desligar o fusion": ("desligar", "fusion"),
    "ligar computador dois": ("ligar", "ananuke"),
    "ligar ana no que": ("ligar", "ananuke"),
    "ligar o computador dois": ("ligar", "ananuke"),
    "desligar computador dois": ("desligar", "ananuke"),
    "desligar ana no que": ("desligar", "ananuke"),
    "desligar o computador dois": ("desligar", "ananuke"),
}

estado = "espera"
limite = 0.0
pendente = None
ultima_ativacao = 0.0
fonte_ativa = None

# Fila unificada de intenções para ambos os microfones
fila_intencoes = queue.Queue(maxsize=32)

# Cache de idempotência / deduplicação de ações
acoes_recentes = {}  # (acao, alvo) -> timestamp
lock_acoes = threading.Lock()

# Histórico para validação dos testes automatizados
historico_processamento = []


def registrar_acao(acao: str, alvo: str) -> None:
    with lock_acoes:
        acoes_recentes[(acao, alvo)] = time.monotonic()


def ja_executado_recentemente(acao: str, alvo: str, janela: float = 3.5) -> bool:
    with lock_acoes:
        ultimo = acoes_recentes.get((acao, alvo), 0.0)
        return (time.monotonic() - ultimo) <= janela


def processar(texto: str, fonte: str = "me6s") -> dict:
    global estado, limite, pendente, ultima_ativacao, fonte_ativa

    agora = time.monotonic()

    if estado != "espera" and agora > limite:
        logger.info("Tempo esgotado. Retornando ao estado de espera.")
        estado = "espera"
        pendente = None
        fonte_ativa = None

    logger.info("Intenção recebida [%s]: '%s' (Estado atual: %s)", fonte, texto, estado)

    resultado = {
        "fonte": fonte,
        "texto": texto,
        "estado_anterior": estado,
        "valido": False,
        "acao": None,
        "alvo": None,
        "simulacao": True,
    }

    if texto == "cancelar":
        estado = "espera"
        pendente = None
        fonte_ativa = None
        ultima_ativacao = 0.0
        logger.info("Operação cancelada pelo usuário via %s.", fonte)
        resultado["valido"] = True
        resultado["acao"] = "cancelar"
        resultado["estado_novo"] = estado
        historico_processamento.append(resultado)
        return resultado

    if estado == "espera":
        if texto == "assistente":
            # Deduplicação de ativação (se ambos os microfones captarem a palavra de ativação quase juntos)
            if (agora - ultima_ativacao) <= 2.0:
                logger.info("Deduplicação: Ativação via %s ignorada (já ativado há %.2fs via %s)", fonte, agora - ultima_ativacao, fonte_ativa)
                resultado["motivo"] = "ativacao_deduplicada"
                historico_processamento.append(resultado)
                return resultado

            estado = "comando"
            limite = agora + 12.0
            ultima_ativacao = agora
            fonte_ativa = fonte
            logger.info("ATIVADO via %s! Aguardando comando.", fonte)
            resultado["valido"] = True
            resultado["acao"] = "wake"
            resultado["estado_novo"] = estado
            historico_processamento.append(resultado)
        return resultado

    if estado == "comando":
        if texto == "assistente":
            logger.info("Já ativado via %s. Aguardando comando.", fonte_ativa)
            resultado["motivo"] = "ja_ativado"
            historico_processamento.append(resultado)
            return resultado

        # Normalização de comando
        if texto == "dois ligar fusion":
            texto = "ligar fusion"

        if texto not in COMANDOS:
            logger.warning("Comando não reconhecido na gramática: '%s'", texto)
            resultado["motivo"] = "comando_invalido"
            historico_processamento.append(resultado)
            return resultado

        acao, alvo = COMANDOS[texto]
        resultado["acao"] = acao
        resultado["alvo"] = alvo

        # Simulação ou leitura do painel
        srv = painel.SERVIDORES.get(alvo) if painel else {"nome": alvo.capitalize(), "mac": "00:11:22:33:44:55"}
        logger.info("Computador alvo: %s (Ação: %s, Fonte: %s)", srv.get("nome", alvo), acao, fonte)

        acessivel = painel.host_ligado(srv) if painel else False

        if acao == "ligar":
            if acessivel:
                logger.info("Computador %s já está acessível.", srv.get("nome", alvo))
                resultado["motivo"] = "ja_acessivel"
            else:
                # Deduplicação de ação (idempotência)
                if ja_executado_recentemente("ligar", alvo):
                    logger.info("Deduplicação de ação: WoL para '%s' descartado (ação idêntica executada recentemente)", alvo)
                    resultado["motivo"] = "acao_deduplicada"
                    resultado["deduplicado"] = True
                    estado = "espera"
                    resultado["estado_novo"] = estado
                    historico_processamento.append(resultado)
                    return resultado

                registrar_acao("ligar", alvo)
                if os.getenv("JARVIS_WOL_REAL") == "1":
                    logger.warning("[MODO REAL] Enviando Wake-on-LAN para %s...", alvo)
                else:
                    logger.info("[SIMULAÇÃO] Wake-on-LAN para %s via %s (Nenhum pacote real enviado)", srv.get("nome", alvo), fonte)
                resultado["valido"] = True
                resultado["simulacao"] = (os.getenv("JARVIS_WOL_REAL") != "1")

            estado = "espera"
            fonte_ativa = None
            resultado["estado_novo"] = estado
            historico_processamento.append(resultado)
            return resultado

        # Fluxo de desligamento
        if painel and not acessivel:
            logger.warning("DESLIGAMENTO BLOQUEADO: computador %s não acessível.", srv.get("nome", alvo))
            estado = "espera"
            fonte_ativa = None
            resultado["motivo"] = "computador_inacessivel"
            resultado["estado_novo"] = estado
            historico_processamento.append(resultado)
            return resultado

        estado = "confirmacao"
        pendente = alvo
        limite = time.monotonic() + 20.0
        logger.info("CONFIRMAR desligamento de %s? Diga confirmar ou cancelar.", srv.get("nome", alvo))
        resultado["valido"] = True
        resultado["acao"] = "solicitar_confirmacao"
        resultado["confirmacao_necessaria"] = True
        resultado["estado_novo"] = estado
        historico_processamento.append(resultado)
        return resultado

    if estado == "confirmacao":
        if texto in ("confirmar", "confirmar desligar", "confirmar desligamento"):
            srv = painel.SERVIDORES.get(pendente) if painel else {"nome": str(pendente).capitalize()}
            alvo = pendente

            # Deduplicação de ação de desligamento
            if ja_executado_recentemente("desligar", alvo):
                logger.info("Deduplicação de ação: Desligamento de '%s' descartado (já confirmado recentemente)", alvo)
                resultado["motivo"] = "acao_deduplicada"
                resultado["deduplicado"] = True
                estado = "espera"
                pendente = None
                fonte_ativa = None
                resultado["estado_novo"] = estado
                historico_processamento.append(resultado)
                return resultado

            registrar_acao("desligar", alvo)
            if os.getenv("JARVIS_SSH_REAL") == "1":
                if texto != "confirmar desligamento":
                    logger.warning("Confirmação insuficiente para modo real. Pedido cancelado.")
                    estado = "espera"
                    pendente = None
                    fonte_ativa = None
                    resultado["motivo"] = "confirmacao_insuficiente"
                    resultado["estado_novo"] = estado
                    historico_processamento.append(resultado)
                    return resultado
                logger.warning("[MODO REAL] Enviando comando SSH de desligamento para %s...", alvo)
            else:
                logger.info("[SIMULAÇÃO] Desligamento SSH confirmado para %s via %s (Nenhum comando SSH enviado)", srv.get("nome", alvo), fonte)

            resultado["valido"] = True
            resultado["acao"] = "desligar_confirmado"
            resultado["alvo"] = alvo
            resultado["simulacao"] = (os.getenv("JARVIS_SSH_REAL") != "1")

            estado = "espera"
            pendente = None
            fonte_ativa = None
            resultado["estado_novo"] = estado
            historico_processamento.append(resultado)
            return resultado

    historico_processamento.append(resultado)
    return resultado


def on_ipc_event(evento: dict) -> None:
    fonte = evento.get("fonte", "lg_mic")
    texto = evento.get("texto", "").strip()
    if texto:
        try:
            fila_intencoes.put_nowait((fonte, texto))
        except queue.Full:
            logger.warning("Fila de intenções cheia. Descartando intenção de %s.", fonte)


def despachante_loop(parar_event: threading.Event) -> None:
    while not parar_event.is_set():
        try:
            fonte, texto = fila_intencoes.get(timeout=0.2)
            processar(texto, fonte=fonte)
        except queue.Empty:
            # Checa expiração de timeout da máquina de estados
            global estado, limite, pendente, fonte_ativa
            if estado != "espera" and time.monotonic() > limite:
                logger.info("Timeout expirado no despachante. Retornando para espera.")
                estado = "espera"
                pendente = None
                fonte_ativa = None


def main():
    print("=== JARVIS OPERACIONAL DUAL-MIC (TEST HARNESS) ===", flush=True)
    print("MODO SIMULAÇÃO ESTREITO - NENHUM COMANDO REAL SERÁ ENVIADO", flush=True)

    socket_path = os.getenv("JARVIS_IPC_SOCKET", DEFAULT_IPC_SOCKET)
    listener = IpcEventListener(
        socket_path=socket_path,
        on_event=on_ipc_event,
        allowed_uid=0,
        verify_peer_cred=True,
    )
    listener.start()

    parar_event = threading.Event()
    th_despachante = threading.Thread(target=despachante_loop, args=(parar_event,), daemon=True)
    th_despachante.start()

    logger.info("Controlador de testes ouvindo no barramento IPC: %s", socket_path)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nEncerrando teste...", flush=True)
    finally:
        parar_event.set()
        listener.stop()


if __name__ == "__main__":
    main()
