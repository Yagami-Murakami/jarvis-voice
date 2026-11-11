#!/usr/bin/env python3
"""Gateway de Áudio e Árbitro de Voz para Microfone Duplo (ME6S + LG-X230DSV).

Opera de forma independente e isolada dos serviços de produção:
- Recebe stream PCM 16kHz mono do LG-X230DSV via TCP (porta 8088)
- Monitora eventos do microfone ME6S (via journalctl de jarvis-voice)
- Executa reconhecimento Vosk de teste para a fonte LG
- Árbitro de Sessão: deduplica ativações simultâneas e unifica comandos
- Modo estrito de SIMULAÇÃO: não executa comandos WoL ou SSH reais
"""
from __future__ import annotations

import argparse
import json
import hmac
import logging
import math
import os
import queue
import re
import socket
import struct
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from gateway.ipc_bus import DEFAULT_IPC_SOCKET, IpcEventBroadcaster
except ImportError:
    from ipc_bus import DEFAULT_IPC_SOCKET, IpcEventBroadcaster

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [JarvisGateway] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("JarvisGateway")

MAGIC_HANDSHAKE = b"JMIC"
MAGIC_DATA = b"DATA"
DEFAULT_TOKEN = ""  # Sem segredo embutido; configure JARVIS_AUDIO_TOKEN localmente
DEFAULT_MODEL = "/opt/jarvis-voice/vosk-model-small-pt-0.3"

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

GRAMATICA = [
    "assistente",
    "confirmar",
    "confirmar desligar",
    "confirmar desligamento",
    "cancelar",
    *COMANDOS.keys(),
    "[unk]",
]


class SessionArbitrator:
    """Controla sessões unificadas entre os dois microfones com deduplicação rigorosa."""

    def __init__(self, dedup_window_s: float = 2.0, action_dedup_window_s: float = 3.5):
        self._lock = threading.RLock()
        self.dedup_window_s = dedup_window_s
        self.action_dedup_window_s = action_dedup_window_s

        self.session_id: Optional[str] = None
        self.state: str = "espera"  # espera, comando, confirmacao
        self.primary_source: Optional[str] = None  # "me6s" ou "lg_mic"
        self.target_computer: Optional[str] = None
        self.target_action: Optional[str] = None

        self.session_start_time: float = 0.0
        self.last_trigger_time: float = 0.0
        self.last_trigger_source: Optional[str] = None
        self.expires_at: float = 0.0

        self.total_activations: int = 0
        self.deduplicated_count: int = 0
        self.history: List[Dict[str, Any]] = []
        self._recent_actions: Dict[Tuple[str, str], float] = {}

    def report_wake(self, source: str, session_id: Optional[str] = None) -> Tuple[bool, str]:
        """Reporta palavra de ativação 'assistente' detectada por uma fonte.

        Retorna: (is_new_session, session_id)
        """
        now = time.monotonic()
        with self._lock:
            # 1. Se já estiver em sessão ativa (comando ou confirmacao) e não expirada
            if self.state in ("comando", "confirmacao"):
                if now <= self.expires_at:
                    delta = now - self.last_trigger_time
                    self.deduplicated_count += 1
                    logger.info(
                        "Ativação recebida durante sessão ativa (ID: %s, estado: %s, fonte: %s, última: %s há %.2fs). Mantendo sessão existente.",
                        self.session_id,
                        self.state,
                        source,
                        self.last_trigger_source,
                        delta,
                    )
                    self.last_trigger_time = now
                    # Se vier com session_id explícito (ex: autoridade do controlador), sincroniza
                    if session_id and session_id != self.session_id:
                        self.session_id = session_id
                    self._registrar_evento("wake_deduplicated", source, delta=delta, session_id=self.session_id)
                    return False, self.session_id or ""
                else:
                    self.state = "espera"
                    self.session_id = None

            # 2. Abre nova sessão
            self.total_activations += 1
            if session_id:
                self.session_id = session_id
            else:
                self.session_id = f"sess-{int(time.time())}-{self.total_activations}"
            self.state = "comando"
            self.primary_source = source
            self.last_trigger_source = source
            self.session_start_time = now
            self.last_trigger_time = now
            self.expires_at = now + 12.0
            self.target_computer = None
            self.target_action = None

            logger.info("Nova sessão de comando iniciada: ID=%s (Fonte: %s)", self.session_id, source)
            self._registrar_evento("wake_accepted", source, session_id=self.session_id)
            return True, self.session_id

    def sync_session_id(self, official_session_id: Optional[str]) -> None:
        """Sincroniza o session_id local com a autoridade única (controlador)."""
        if not official_session_id:
            return
        with self._lock:
            if self.session_id != official_session_id:
                logger.info(
                    "Sincronização de session_id com autoridade central: %s -> %s",
                    self.session_id,
                    official_session_id,
                )
                self.session_id = official_session_id
                self._registrar_evento("session_id_synced", "controller", session_id=official_session_id)

    def report_command(self, source: str, raw_text: str) -> Dict[str, Any]:
        """Processa comando de voz recebido de uma das fontes."""
        now = time.monotonic()
        with self._lock:
            texto = raw_text.strip().lower()
            if self.state != "comando":
                if self.state == "confirmacao":
                    if texto == "cancelar":
                        return self.report_cancellation(source)
                    if texto in ("confirmar", "confirmar desligar", "confirmar desligamento") or "confirmar" in texto:
                        return self.report_confirmation(source)
                    if texto in COMANDOS:
                        acao_cand, alvo_cand = COMANDOS[texto]
                        if acao_cand == "desligar" and (alvo_cand == self.target_computer or not self.target_computer):
                            self.deduplicated_count += 1
                            logger.info(
                                "Deduplicação de comando: '%s %s' via %s descartado (confirmação já pendente)",
                                acao_cand,
                                alvo_cand,
                                source,
                            )
                            return {
                                "valido": False,
                                "acao": acao_cand,
                                "alvo": alvo_cand,
                                "deduplicado": True,
                                "status": "confirmacao_pendente",
                                "session_id": self.session_id,
                                "motivo": f"Confirmação de desligamento para '{alvo_cand}' já pendente",
                            }
                # Se não estiver no modo comando, verifica se é comando duplicado do outro microfone
                if texto in COMANDOS:
                    acao, alvo = COMANDOS[texto]
                    delta_action = now - self._recent_actions.get((acao, alvo), 0.0)
                    if delta_action <= self.action_dedup_window_s:
                        self.deduplicated_count += 1
                        logger.info(
                            "Deduplicação de comando: '%s %s' via %s descartado (ação idêntica executada há %.2fs)",
                            acao,
                            alvo,
                            source,
                            delta_action,
                        )
                        return {
                            "valido": False,
                            "acao": acao,
                            "alvo": alvo,
                            "deduplicado": True,
                            "session_id": self.session_id,
                            "motivo": f"Ação '{acao} {alvo}' recentemente executada (idempotência)",
                        }
                return {"valido": False, "motivo": f"Estado atual não aceita comandos ({self.state})"}

            if now > self.expires_at:
                self.state = "espera"
                self.session_id = None
                return {"valido": False, "motivo": "Sessão expirada por inatividade"}

            if texto == "cancelar":
                return self.report_cancellation(source)

            if texto not in COMANDOS:
                return {"valido": False, "motivo": f"Comando não reconhecido na gramática: '{texto}'"}

            acao, alvo = COMANDOS[texto]
            self.target_action = acao
            self.target_computer = alvo

            if acao == "ligar":
                # Deduplicação na camada de ação (idempotência no acionamento)
                delta_action = now - self._recent_actions.get((acao, alvo), 0.0)
                if delta_action <= self.action_dedup_window_s:
                    self.deduplicated_count += 1
                    logger.info(
                        "Deduplicação de ação: WoL para '%s' descartado (ação idêntica executada há %.2fs)",
                        alvo,
                        delta_action,
                    )
                    sid = self.session_id
                    self.state = "espera"
                    self.session_id = None
                    self._registrar_evento("action_deduplicated", source, acao=acao, alvo=alvo, delta=delta_action)
                    return {
                        "valido": False,
                        "motivo": f"Ação '{acao} {alvo}' recentemente executada (idempotência)",
                        "session_id": sid,
                        "deduplicado": True,
                        "acao": acao,
                        "alvo": alvo,
                    }
                self._recent_actions[(acao, alvo)] = now

                # Conclui sessão com reconhecimento local
                sid = self.session_id
                self.state = "espera"
                self.session_id = None
                self._registrar_evento("command_recognized_local", source, acao=acao, alvo=alvo, session_id=sid)
                logger.info(
                    "Comando reconhecido localmente: %s para '%s' via %s (sessão %s)",
                    acao,
                    alvo,
                    source,
                    sid,
                )
                return {
                    "valido": True,
                    "acao": "ligar",
                    "alvo": alvo,
                    "session_id": sid,
                    "simulacao": True,
                }

            if acao == "desligar":
                # Entra em confirmação obrigatória
                self.state = "confirmacao"
                self.expires_at = now + 20.0
                sid = self.session_id
                self._registrar_evento("command_shutdown_pending", source, alvo=alvo, session_id=sid)
                logger.info(
                    "Confirmação de desligamento necessária para '%s' via %s (sessão %s)",
                    alvo,
                    source,
                    sid,
                )
                return {
                    "valido": True,
                    "acao": "desligar",
                    "alvo": alvo,
                    "confirmacao_necessaria": True,
                    "status": "confirmacao_pendente",
                    "session_id": sid,
                }

            return {"valido": False, "motivo": "Ação desconhecida"}

    def report_cancellation(self, source: str) -> Dict[str, Any]:
        """Processa cancelamento explícito de comando ou confirmação pendente."""
        now = time.monotonic()
        with self._lock:
            old_sid = self.session_id
            old_alvo = self.target_computer
            old_state = self.state
            self.state = "espera"
            self.session_id = None
            self.target_computer = None
            self.target_action = None
            self.expires_at = 0.0
            self._registrar_evento("command_cancelled", source, session_id=old_sid, estado_anterior=old_state)
            logger.info("Operação cancelada pelo usuário via %s (sessão %s, estado anterior %s)", source, old_sid, old_state)
            return {
                "valido": True,
                "acao": "cancelar",
                "alvo": old_alvo,
                "status": "cancelado",
                "session_id": old_sid,
            }

    def report_confirmation(self, source: str) -> Dict[str, Any]:
        """Processa confirmação de desligamento seguro."""
        now = time.monotonic()
        with self._lock:
            if self.state != "confirmacao":
                # Verifica se confirmação já foi realizada recentemente (deduplicação entre microfones)
                for alvo_cand in (self.target_computer, "fusion", "ananuke"):
                    if alvo_cand:
                        delta_action = now - self._recent_actions.get(("desligar", alvo_cand), 0.0)
                        if delta_action <= self.action_dedup_window_s:
                            self.deduplicated_count += 1
                            logger.info(
                                "Deduplicação de confirmação: 'desligar %s' via %s descartada (ação idêntica confirmada há %.2fs)",
                                alvo_cand,
                                source,
                                delta_action,
                            )
                            return {
                                "valido": False,
                                "acao": "desligar_confirmado",
                                "alvo": alvo_cand,
                                "deduplicado": True,
                                "session_id": self.session_id,
                                "motivo": f"Ação 'desligar {alvo_cand}' recentemente confirmada (idempotência)",
                            }
                return {"valido": False, "motivo": "Nenhuma confirmação pendente"}

            if now > self.expires_at:
                self.state = "espera"
                self.session_id = None
                return {"valido": False, "motivo": "Tempo de confirmação esgotado"}

            sid = self.session_id
            alvo = self.target_computer

            # Deduplicação na camada de ação (idempotência no desligamento)
            delta_action = now - self._recent_actions.get(("desligar", alvo), 0.0)
            if delta_action <= self.action_dedup_window_s:
                self.deduplicated_count += 1
                logger.info(
                    "Deduplicação de ação: Desligamento de '%s' descartado (ação idêntica confirmada há %.2fs)",
                    alvo,
                    delta_action,
                )
                self.state = "espera"
                self.session_id = None
                self._registrar_evento("action_deduplicated", source, acao="desligar", alvo=alvo, delta=delta_action)
                return {
                    "valido": False,
                    "motivo": f"Ação 'desligar {alvo}' recentemente confirmada (idempotência)",
                    "session_id": sid,
                    "deduplicado": True,
                }
            self._recent_actions[("desligar", alvo)] = now

            self.state = "espera"
            self.session_id = None
            self._registrar_evento("confirmation_recognized_local", source, alvo=alvo, session_id=sid)
            logger.info(
                "Confirmação de desligamento reconhecida localmente para '%s' via %s (sessão %s)",
                alvo,
                source,
                sid,
            )
            return {
                "valido": True,
                "acao": "desligar_confirmado",
                "alvo": alvo,
                "session_id": sid,
                "status": "concluido",
                "simulacao": True,
            }

    def _registrar_evento(self, tipo: str, fonte: str, **kwargs: Any) -> None:
        item = {
            "tempo": time.time(),
            "tipo": tipo,
            "fonte": fonte,
            **kwargs,
        }
        self.history.append(item)
        if len(self.history) > 100:
            self.history.pop(0)

    def obter_status(self) -> Dict[str, Any]:
        with self._lock:
            agora = time.monotonic()
            if self.state != "espera" and agora > self.expires_at:
                self.state = "espera"
                self.session_id = None

            return {
                "state": self.state,
                "session_id": self.session_id,
                "primary_source": self.primary_source,
                "target_computer": self.target_computer,
                "target_action": self.target_action,
                "expires_in_s": max(0.0, round(self.expires_at - agora, 1)) if self.expires_at > agora else 0.0,
                "total_activations": self.total_activations,
                "deduplicated_count": self.deduplicated_count,
            }


def _registrar_log_ack_gateway(
    tipo_acao: str,
    alvo: Optional[str],
    fonte: str,
    session_id: Optional[str],
    ack: Any,
    erro_fallback: str = "falha_controlador",
) -> None:
    """Registra log auditável do Gateway diferenciando com precisão:
    - [COMANDO ACEITO]
    - [AGUARDANDO CONFIRMAÇÃO]
    - [EM PROCESSAMENTO]
    - [CONCLUÍDO]
    - [CANCELADO]
    - [FALHA]
    """
    nome_acao = "WoL" if tipo_acao == "ligar" else ("Desligamento SSH" if tipo_acao in ("desligar", "confirmar") else tipo_acao)
    alvo_str = alvo or "indeterminado"
    sid_str = session_id or "sem_sessao"

    if ack is None:
        logger.error(
            "[FALHA] %s para '%s' via %s falhou: timeout ou barramento IPC inacessível (sessão %s)",
            nome_acao, alvo_str, fonte, sid_str
        )
        return

    if isinstance(ack, dict):
        if not ack.get("sucesso"):
            motivo = ack.get("erro") or erro_fallback
            logger.error(
                "[FALHA] %s para '%s' via %s rejeitado pelo controlador: %s (sessão %s, status=falha)",
                nome_acao, alvo_str, fonte, motivo, sid_str
            )
            return

        st = ack.get("status")
        if not st:
            if ack.get("estado") == "confirmacao" or tipo_acao == "desligar" or ack.get("acao") == "desligar_pendente":
                st = "confirmacao_pendente"
            elif ack.get("acao") == "cancelar" or tipo_acao == "cancelar":
                st = "cancelado"
            elif ack.get("sucesso") and tipo_acao in ("confirmar", "ligar"):
                st = "concluido"
            else:
                st = "aceito"

        if st == "confirmacao_pendente" or (st != "concluido" and ack.get("estado") == "confirmacao" and tipo_acao == "desligar"):
            logger.info(
                "[AGUARDANDO CONFIRMAÇÃO] [SIMULAÇÃO] Desligamento SSH para '%s' via %s aguardando confirmação do operador (sessão %s, status=confirmacao_pendente)",
                alvo_str, fonte, sid_str
            )
        elif st == "cancelado" or tipo_acao == "cancelar":
            logger.info(
                "[CANCELADO] [SIMULAÇÃO] Operação '%s' cancelada pelo usuário via %s (sessão %s, status=cancelado)",
                alvo_str, fonte, sid_str
            )
        elif st == "aceito":
            logger.info(
                "[COMANDO ACEITO] [SIMULAÇÃO] %s para '%s' via %s aceito pelo controlador (sessão %s, status=aceito)",
                nome_acao, alvo_str, fonte, sid_str
            )
        elif st == "em_processamento":
            logger.info(
                "[EM PROCESSAMENTO] [SIMULAÇÃO] %s para '%s' via %s em andamento no controlador (sessão %s, status=em_processamento)",
                nome_acao, alvo_str, fonte, sid_str
            )
        elif st == "concluido":
            if tipo_acao == "desligar" and ack.get("acao") == "desligar_pendente":
                logger.info(
                    "[AGUARDANDO CONFIRMAÇÃO] [SIMULAÇÃO] Desligamento SSH para '%s' via %s aguardando confirmação do operador (sessão %s, status=confirmacao_pendente)",
                    alvo_str, fonte, sid_str
                )
            else:
                logger.info(
                    "[CONCLUÍDO] [SIMULAÇÃO] %s confirmado pelo controlador para '%s' via %s (sessão %s, status=concluido)",
                    nome_acao, alvo_str, fonte, sid_str
                )
        elif st == "falha":
            logger.error(
                "[FALHA] %s para '%s' via %s falhou durante execução: %s (sessão %s, status=falha)",
                nome_acao, alvo_str, fonte, ack.get("erro", "falha_execucao"), sid_str
            )
        else:
            logger.info(
                "[STATUS=%s] [SIMULAÇÃO] %s para '%s' via %s (sessão %s)",
                st, nome_acao, alvo_str, fonte, sid_str
            )
    else:
        logger.error(
            "[FALHA] %s para '%s' via %s recebeu ACK em formato inválido: %s (sessão %s)",
            nome_acao, alvo_str, fonte, type(ack).__name__, sid_str
        )


class LgMicServer(threading.Thread):
    """Servidor TCP de recepção de áudio bruto PCM do smartphone LG-X230DSV."""

    def __init__(
        self,
        host: str,
        port: int,
        auth_token: str,
        arbitrator: SessionArbitrator,
        model_path: Optional[str] = None,
        tls_cert: Optional[str] = None,
        tls_key: Optional[str] = None,
        ipc_broadcaster: Optional[IpcEventBroadcaster] = None,
    ):
        super().__init__(name="LgMicServer", daemon=True)
        self.host = host
        self.port = port
        self.auth_token = auth_token
        self.arbitrator = arbitrator
        self.model_path = model_path
        self.tls_cert = tls_cert
        self.tls_key = tls_key
        self.ipc_broadcaster = ipc_broadcaster

        self._running = True
        self.server_socket: Optional[socket.socket] = None
        self.ssl_context = None

        if self.tls_cert and self.tls_key and Path(self.tls_cert).is_file() and Path(self.tls_key).is_file():
            import ssl
            self.ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            self.ssl_context.load_cert_chain(certfile=self.tls_cert, keyfile=self.tls_key)
            if hasattr(ssl, "TLSVersion"):
                self.ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2
            logger.info("TLS 1.2+ ativado no Gateway com certificado: %s", self.tls_cert)

        # Métricas do microfone LG
        self.connected = False
        self.client_ip: Optional[str] = None
        self.last_packet_time: float = 0.0
        self.current_rms: float = 0.0
        self.packets_received: int = 0
        self.packets_dropped: int = 0
        self.echo_guard_active: bool = False
        self.last_transcript: str = ""

        # Instância Vosk se disponível
        self.vosk_model = None
        self.vosk_rec = None
        self._inicializar_vosk()

    def _inicializar_vosk(self) -> None:
        if not self.model_path or not Path(self.model_path).is_dir():
            logger.warning("Modelo Vosk não encontrado em '%s'. Reconhecimento LG operará em modo simulação.", self.model_path)
            return

        try:
            from vosk import KaldiRecognizer, Model, SetLogLevel

            SetLogLevel(-1)
            self.vosk_model = Model(str(self.model_path))
            self.vosk_rec = KaldiRecognizer(self.vosk_model, 16000, json.dumps(GRAMATICA, ensure_ascii=False))
            logger.info("Instância Vosk de teste para LG Mic carregada com sucesso a partir de %s", self.model_path)
        except Exception as e:
            logger.warning("Falha ao inicializar Vosk para LG Mic: %s", e)

    def run(self) -> None:
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind((self.host, self.port))
        self.server_socket.listen(2)
        logger.info("Servidor TCP LG Mic ouvindo em %s:%d (TLS=%s)", self.host, self.port, bool(self.ssl_context))

        while self._running:
            try:
                client_sock, client_addr = self.server_socket.accept()
                logger.info("Cliente LG conectado de %s:%d", client_addr[0], client_addr[1])
                self._atender_cliente(client_sock, client_addr[0])
            except Exception as e:
                if self._running:
                    logger.warning("Exceção no loop accept: %s", e)

    def _atender_cliente(self, sock: socket.socket, ip: str) -> None:
        sock.settimeout(12.0)
        if self.ssl_context:
            try:
                sock = self.ssl_context.wrap_socket(sock, server_side=True)
            except Exception as e:
                logger.warning("Falha no handshake TLS com cliente %s: %s", ip, e)
                try:
                    sock.close()
                except Exception:
                    pass
                return

        self.connected = True
        self.client_ip = ip

        try:
            # 1. Validação de Handshake
            magic = self._ler_exato(sock, 4)
            if magic != MAGIC_HANDSHAKE:
                logger.warning("Handshake inválido do cliente %s (magic: %s)", ip, magic)
                sock.close()
                return

            versao = struct.unpack(">B", self._ler_exato(sock, 1))[0]
            token_len = struct.unpack(">H", self._ler_exato(sock, 2))[0]
            if token_len < 32 or token_len > 256:
                logger.warning("Handshake rejeitado: comprimento de token inválido para %s", ip)
                return
            token_bytes = self._ler_exato(sock, token_len)
            # Comparação de bytes em tempo constante; rejeita tokens inválidos.
            if not hmac.compare_digest(token_bytes, self.auth_token.encode("utf-8")):
                logger.warning("Autenticação rejeitada para cliente %s: token incorreto", ip)
                sock.close()
                return

            sample_rate = struct.unpack(">I", self._ler_exato(sock, 4))[0]
            channels = struct.unpack(">B", self._ler_exato(sock, 1))[0]
            bit_depth = struct.unpack(">B", self._ler_exato(sock, 1))[0]

            if versao != 1 or sample_rate != 16000 or channels != 1 or bit_depth != 16:
                logger.warning("Formato de áudio não suportado pelo cliente %s", ip)
                return

            logger.info(
                "Handshake autenticado de %s: v%d, %d Hz, %d ch, %d bits",
                ip,
                versao,
                sample_rate,
                channels,
                bit_depth,
            )

            # 2. Loop de recepção de frames
            last_seq = -1
            while self._running:
                data_magic = self._ler_exato(sock, 4)
                if not data_magic:
                    break
                if data_magic != MAGIC_DATA:
                    logger.warning("Frame corrompido ou fora de alinhamento: %s", data_magic)
                    break

                seq = struct.unpack(">I", self._ler_exato(sock, 4))[0]
                timestamp_ms = struct.unpack(">Q", self._ler_exato(sock, 8))[0]
                flags = struct.unpack(">B", self._ler_exato(sock, 1))[0]
                payload_len = struct.unpack(">H", self._ler_exato(sock, 2))[0]
                if payload_len != 1280:
                    logger.warning("Frame com tamanho inesperado recebido de %s", ip)
                    break

                payload = self._ler_exato(sock, payload_len)
                if len(payload) < payload_len:
                    break

                # Diagnóstico de pacotes
                if last_seq >= 0 and seq != last_seq + 1:
                    delta_loss = seq - (last_seq + 1)
                    if delta_loss > 0:
                        self.packets_dropped += delta_loss
                last_seq = seq
                self.packets_received += 1
                self.last_packet_time = time.time()

                echo_guard = (flags & 0x01) != 0
                self.echo_guard_active = echo_guard

                # Cálculo de RMS para métricas
                self.current_rms = self._calcular_rms(payload)

                # Processa áudio no Vosk somente se Echo Guard NÃO estiver ativo
                if not echo_guard and self.vosk_rec is not None:
                    if self.vosk_rec.AcceptWaveform(payload):
                        res = json.loads(self.vosk_rec.Result())
                        texto = res.get("text", "").strip()
                        if texto:
                            self._processar_texto_reconhecido(texto)

        except (socket.timeout, socket.error, EOFError) as e:
            logger.info("Cliente LG desconectado (%s): %s", ip, e)
        finally:
            if not self.echo_guard_active and self.vosk_rec is not None:
                try:
                    res = json.loads(self.vosk_rec.FinalResult())
                    texto = res.get("text", "").strip()
                    if texto:
                        self._processar_texto_reconhecido(texto)
                except Exception:
                    pass
            self.connected = False
            self.client_ip = None
            try:
                sock.close()
            except Exception:
                pass

    def _processar_texto_reconhecido(self, texto: str) -> None:
        logger.info("[Vosk-LG] Transcrição detectada: '%s'", texto)
        self.last_transcript = texto

        if "assistente" in texto:
            is_new, sid = self.arbitrator.report_wake("lg_mic")
            if not is_new:
                logger.info("[Vosk-LG] Palavra 'assistente' deduplicada na sessão %s", sid)
            # Emite wake ao barramento IPC e reconcilia session_id com a resposta oficial do controlador
            if self.ipc_broadcaster:
                ack = self.ipc_broadcaster.emit_event(
                    source="lg_mic",
                    event_type="wake",
                    text="assistente",
                    session_id=sid,
                )
                if isinstance(ack, dict) and ack.get("active_session_id"):
                    official_sid = ack["active_session_id"]
                    if official_sid != self.arbitrator.session_id:
                        logger.info(
                            "[Vosk-LG] Sincronizando session_id com autoridade central: %s -> %s",
                            self.arbitrator.session_id,
                            official_sid,
                        )
                        self.arbitrator.sync_session_id(official_sid)
            return

        if texto == "cancelar":
            res = self.arbitrator.report_cancellation("lg_mic")
            if self.ipc_broadcaster:
                ack = self.ipc_broadcaster.emit_event(
                    source="lg_mic",
                    event_type="cancelar",
                    text="cancelar",
                    session_id=res.get("session_id"),
                )
                _registrar_log_ack_gateway(
                    tipo_acao="cancelar",
                    alvo=res.get("alvo"),
                    fonte="lg_mic",
                    session_id=res.get("session_id"),
                    ack=ack,
                )
            return

        if self.arbitrator.state == "comando":
            res = self.arbitrator.report_command("lg_mic", texto)
            logger.info("[Vosk-LG] Resultado do comando: %s", res)
            if self.ipc_broadcaster:
                if res.get("valido"):
                    ack = self.ipc_broadcaster.emit_event(
                        source="lg_mic",
                        event_type="comando",
                        text=texto,
                        session_id=res.get("session_id"),
                        extra=res,
                    )
                    if isinstance(ack, dict) and ack.get("active_session_id"):
                        self.arbitrator.sync_session_id(ack["active_session_id"])
                    _registrar_log_ack_gateway(
                        tipo_acao=res.get("acao", "comando"),
                        alvo=res.get("alvo"),
                        fonte="lg_mic",
                        session_id=res.get("session_id"),
                        ack=ack,
                    )
                elif res.get("deduplicado") and res.get("session_id"):
                    ack = self.ipc_broadcaster.emit_event(
                        source="lg_mic",
                        event_type="comando",
                        text=texto,
                        session_id=res.get("session_id"),
                        extra=res,
                    )
                    if isinstance(ack, dict) and ack.get("active_session_id"):
                        self.arbitrator.sync_session_id(ack["active_session_id"])
        elif self.arbitrator.state == "confirmacao":
            if "cancelar" in texto:
                res = self.arbitrator.report_cancellation("lg_mic")
                logger.info("[Vosk-LG] Cancelamento solicitado: %s", res)
                if self.ipc_broadcaster:
                    ack = self.ipc_broadcaster.emit_event(
                        source="lg_mic",
                        event_type="cancelar",
                        text="cancelar",
                        session_id=res.get("session_id"),
                    )
                    _registrar_log_ack_gateway(
                        tipo_acao="cancelar",
                        alvo=res.get("alvo"),
                        fonte="lg_mic",
                        session_id=res.get("session_id"),
                        ack=ack,
                    )
                return
            elif "confirmar" in texto:
                res = self.arbitrator.report_confirmation("lg_mic")
                logger.info("[Vosk-LG] Resultado da confirmação: %s", res)
                if res.get("valido") and self.ipc_broadcaster:
                    if res.get("deduplicado"):
                        logger.info("[Vosk-LG] Confirmação deduplicada localmente, nenhum evento IPC emitido.")
                        return
                    ack = self.ipc_broadcaster.emit_event(
                        source="lg_mic",
                        event_type="confirmar",
                        text=texto,
                        session_id=res.get("session_id"),
                        extra=res,
                    )
                    if isinstance(ack, dict) and ack.get("active_session_id"):
                        self.arbitrator.sync_session_id(ack["active_session_id"])
                    _registrar_log_ack_gateway(
                        tipo_acao="confirmar",
                        alvo=res.get("alvo"),
                        fonte="lg_mic",
                        session_id=res.get("session_id"),
                        ack=ack,
                    )

    def _calcular_rms(self, pcm_bytes: bytes) -> float:
        if len(pcm_bytes) < 2:
            return 0.0
        n_samples = len(pcm_bytes) // 2
        sum_sq = 0.0
        for i in range(0, len(pcm_bytes) - 1, 2):
            sample = struct.unpack("<h", pcm_bytes[i : i + 2])[0]
            val = sample / 32768.0
            sum_sq += val * val
        mean = sum_sq / n_samples
        return min(1.0, math.sqrt(mean))

    def _ler_exato(self, sock: socket.socket, n: int) -> bytes:
        dados = bytearray()
        while len(dados) < n:
            chunk = sock.recv(n - len(dados))
            if not chunk:
                raise EOFError("Conexão encerrada prematuramente")
            dados.extend(chunk)
        return bytes(dados)

    def stop(self) -> None:
        self._running = False
        if self.server_socket:
            try:
                self.server_socket.close()
            except Exception:
                pass

    def obter_status(self) -> Dict[str, Any]:
        now = time.time()
        is_active = self.connected and (now - self.last_packet_time < 3.0)
        return {
            "connected": is_active,
            "tls_active": bool(self.ssl_context),
            "client_ip": self.client_ip if is_active else None,
            "current_rms": round(self.current_rms, 3) if is_active else 0.0,
            "packets_received": self.packets_received,
            "packets_dropped": self.packets_dropped,
            "echo_guard_active": self.echo_guard_active,
            "last_transcript": self.last_transcript,
            "sample_rate": 16000,
        }


class Me6sJournalMonitor(threading.Thread):
    """Monitor somente-leitura do journalctl do serviço jarvis-voice (ME6S USB)."""

    def __init__(self, arbitrator: SessionArbitrator):
        super().__init__(name="Me6sJournalMonitor", daemon=True)
        self.arbitrator = arbitrator
        self._running = True
        self.last_event_time: float = 0.0
        self.last_line: str = ""
        self.service_active: bool = False
        self.current_origin: str = "me6s"
        self.last_origin_time: float = 0.0
        self.pending_session_id: Optional[str] = None

    def run(self) -> None:
        import subprocess

        cmd = ["journalctl", "-u", "jarvis-voice.service", "-f", "-o", "cat", "-n", "0"]
        logger.info("Iniciando monitoramento passivo do journal ME6S: %s", " ".join(cmd))

        while self._running:
            proc = None
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    bufsize=1,
                )
                self.service_active = True

                for linha in iter(proc.stdout.readline, ""):
                    if not self._running:
                        break
                    linha = linha.strip()
                    if linha:
                        self._processar_linha(linha)
                proc.stdout.close()
                proc.wait()
            except Exception as e:
                self.service_active = False
                if self._running:
                    logger.debug("Journalctl ME6S pausado: %s. Tentando em 4s...", e)
                    time.sleep(4)

    def _processar_linha(self, linha: str) -> None:
        agora = time.time()
        self.last_event_time = agora
        self.last_line = linha

        # Identifica declaração de origem explícita emitida pelo controlador
        if linha.startswith("[Origem:"):
            origem = linha.replace("[Origem:", "").replace("]", "").strip()
            self.current_origin = origem
            self.last_origin_time = agora
            return

        # Captura session_id coerente emitido pelo controlador para o ME6S
        if linha.startswith("[Sessao:"):
            sid = linha.replace("[Sessao:", "").replace("]", "").strip()
            self.pending_session_id = sid
            return

        # Sincroniza expiração e cancelamento emitidos pelo controlador central
        if "Tempo esgotado" in linha or "Cancelado." in linha:
            logger.info("[Me6sJournalMonitor] Resetando árbitro por expiração/cancelamento do controlador")
            self.arbitrator.report_cancellation("controller")
            self.current_origin = "me6s"
            self.pending_session_id = None
            return

        # Reseta origem por expiração se passou da janela temporal
        if self.current_origin != "me6s" and (agora - self.last_origin_time) > 2.0:
            self.current_origin = "me6s"

        # Se a origem atual for declarada como não sendo me6s (ex: lg_mic),
        # ignora os eventos espelhados pelo jarvis-operacional.py
        if self.current_origin != "me6s":
            if "ATIVADO!" in linha:
                logger.debug("[Me6sJournalMonitor] Ignorando ativação espelhada da fonte '%s'", self.current_origin)
                self.current_origin = "me6s"
                self.pending_session_id = None
                return
            if linha.startswith("Ouvi:"):
                texto = linha.replace("Ouvi:", "").strip()
                logger.debug("[Me6sJournalMonitor] Ignorando evento espelhado '%s' da fonte '%s'", texto, self.current_origin)
                if texto != "assistente":
                    self.current_origin = "me6s"
                return
            return

        # Ativação do ME6S: consumada estritamente em 'ATIVADO! Aguardando comando.'
        # Evita ativação duplicada entre 'Ouvi: assistente' e 'ATIVADO!' (Problema 2)
        if "ATIVADO! Aguardando comando." in linha:
            is_new, sid = self.arbitrator.report_wake("me6s", session_id=self.pending_session_id)
            logger.info("[ME6S] Ativação reconhecida pelo Vosk principal (sessão %s)", sid)
            self.pending_session_id = None
            return

        if linha.startswith("Ouvi:"):
            texto = linha.replace("Ouvi:", "").strip()
            # Ignora 'assistente' para evitar ativação duplicada com 'ATIVADO!' (Problema 2)
            if texto == "assistente":
                return
            if self.arbitrator.state == "comando":
                self.arbitrator.report_command("me6s", texto)
            elif self.arbitrator.state == "confirmacao" and "confirmar" in texto:
                self.arbitrator.report_confirmation("me6s")

    def stop(self) -> None:
        self._running = False

    def obter_status(self) -> Dict[str, Any]:
        return {
            "active": self.service_active,
            "last_event_time": self.last_event_time,
            "last_line": self.last_line,
        }


class GatewayApiHandler(BaseHTTPRequestHandler):
    """API HTTP para diagnóstico dos dois microfones e estado de arbitragem."""

    server_version = "JarvisAudioGateway/1.0"

    def do_GET(self) -> None:
        if self.path == "/api/mics":
            lg_status = self.server.lg_mic_server.obter_status()
            # Higienização de telemetria para a LAN: não expor dados sensíveis ou memória bruta
            sanitized_lg = {
                "connected": lg_status.get("connected", False),
                "tls_active": lg_status.get("tls_active", False),
                "packets_received": lg_status.get("packets_received", 0),
                "packets_dropped": lg_status.get("packets_dropped", 0),
                "current_rms": lg_status.get("current_rms", 0.0),
                "echo_guard_active": lg_status.get("echo_guard_active", False),
                "sample_rate": lg_status.get("sample_rate", 16000),
            }
            data = {
                "me6s": self.server.me6s_monitor.obter_status(),
                "lg_mic": sanitized_lg,
                "arbitrator": self.server.arbitrator.obter_status(),
                "timestamp": time.time(),
            }
            self._responder_json(data)
        elif self.path == "/api/mics/history":
            if self.client_address[0] not in ("127.0.0.1", "::1"):
                self.send_error(HTTPStatus.FORBIDDEN, "Acesso ao histórico restrito ao host local")
                return
            data = {
                "history": self.server.arbitrator.history,
                "timestamp": time.time(),
            }
            self._responder_json(data)
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Recurso não encontrado")

    def do_POST(self) -> None:
        if (self.path == "/api/test/inject"
                and os.environ.get("JARVIS_GATEWAY_TEST_MODE") == "1"
                and self.client_address[0] in ("127.0.0.1", "::1")):
            # Endpoint seguro para testes automatizados em simulação
            content_len = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_len).decode("utf-8") if content_len > 0 else "{}"
            try:
                dados = json.loads(body)
            except Exception:
                dados = {}

            fonte = dados.get("fonte", "test")
            evento = dados.get("evento", "wake")
            texto = dados.get("texto", "")

            resultado = {}
            if evento == "wake":
                is_new, sid = self.server.arbitrator.report_wake(fonte)
                resultado = {"sucesso": True, "is_new_session": is_new, "session_id": sid}
                if is_new and getattr(self.server, "ipc_broadcaster", None):
                    ack = self.server.ipc_broadcaster.emit_event(fonte, "wake", "assistente", session_id=sid)
                    resultado["ack"] = ack
                    if isinstance(ack, dict):
                        if not ack.get("sucesso"):
                            resultado["sucesso"] = False
                            resultado["erro"] = ack.get("erro", "falha_controlador")
                        elif ack.get("active_session_id"):
                            official_sid = ack["active_session_id"]
                            if official_sid != self.server.arbitrator.session_id:
                                self.server.arbitrator.sync_session_id(official_sid)
                                resultado["session_id"] = official_sid
            elif evento == "command":
                res = self.server.arbitrator.report_command(fonte, texto)
                resultado = {"sucesso": True, "resultado": res}
                if getattr(self.server, "ipc_broadcaster", None):
                    if res.get("valido"):
                        tipo_ipc = "confirmar" if res.get("acao") == "desligar_confirmado" else "comando"
                        texto_ipc = texto if texto else ("confirmar desligamento" if tipo_ipc == "confirmar" else "")
                        ack = self.server.ipc_broadcaster.emit_event(
                            fonte, tipo_ipc, texto_ipc, session_id=res.get("session_id"), extra=res
                        )
                        resultado["ack"] = ack
                        if ack is None:
                            resultado["sucesso"] = False
                            resultado["erro"] = getattr(self.server.ipc_broadcaster, "last_error", "falha_controlador")
                        elif isinstance(ack, dict):
                            resultado["status"] = ack.get("status")
                            if not ack.get("sucesso"):
                                resultado["sucesso"] = False
                                resultado["erro"] = ack.get("erro", "falha_controlador")
                            elif ack.get("active_session_id"):
                                self.server.arbitrator.sync_session_id(ack["active_session_id"])
                        _registrar_log_ack_gateway(
                            tipo_acao=res.get("acao", tipo_ipc),
                            alvo=res.get("alvo"),
                            fonte=fonte,
                            session_id=res.get("session_id"),
                            ack=ack,
                            erro_fallback=resultado.get("erro", "falha_controlador"),
                        )
                    elif res.get("deduplicado") and res.get("session_id"):
                        tipo_ipc = "confirmar" if res.get("acao") == "desligar_confirmado" else "comando"
                        texto_ipc = texto if texto else ("confirmar desligamento" if tipo_ipc == "confirmar" else "")
                        ack = self.server.ipc_broadcaster.emit_event(
                            fonte, tipo_ipc, texto_ipc, session_id=res.get("session_id"), extra=res
                        )
                        resultado["ack"] = ack
                        resultado["status"] = "deduplicado"
                        if isinstance(ack, dict) and ack.get("active_session_id"):
                            self.server.arbitrator.sync_session_id(ack["active_session_id"])
                    else:
                        resultado["status"] = res.get("status", "deduplicado")
            elif evento == "confirm":
                res = self.server.arbitrator.report_confirmation(fonte)
                resultado = {"sucesso": True, "resultado": res}
                if res.get("valido") and getattr(self.server, "ipc_broadcaster", None):
                    if not res.get("deduplicado"):
                        texto_conf = texto if texto else "confirmar desligamento"
                        ack = self.server.ipc_broadcaster.emit_event(
                            fonte, "confirmar", texto_conf, session_id=res.get("session_id"), extra=res
                        )
                        resultado["ack"] = ack
                        if ack is None:
                            resultado["sucesso"] = False
                            resultado["erro"] = getattr(self.server.ipc_broadcaster, "last_error", "falha_controlador")
                        elif isinstance(ack, dict):
                            resultado["status"] = ack.get("status")
                            if not ack.get("sucesso"):
                                resultado["sucesso"] = False
                                resultado["erro"] = ack.get("erro", "falha_controlador")
                        _registrar_log_ack_gateway(
                            tipo_acao="confirmar",
                            alvo=res.get("alvo"),
                            fonte=fonte,
                            session_id=res.get("session_id"),
                            ack=ack,
                            erro_fallback=resultado.get("erro", "falha_controlador"),
                        )
                    else:
                        resultado["status"] = res.get("status", "deduplicado")
            elif evento in ("cancel", "cancelar"):
                res = self.server.arbitrator.report_cancellation(fonte)
                resultado = {"sucesso": True, "resultado": res}
                if res.get("valido") and getattr(self.server, "ipc_broadcaster", None):
                    ack = self.server.ipc_broadcaster.emit_event(
                        fonte, "cancelar", "cancelar", session_id=res.get("session_id"), extra=res
                    )
                    resultado["ack"] = ack
                    if ack is None:
                        resultado["sucesso"] = False
                        resultado["erro"] = getattr(self.server.ipc_broadcaster, "last_error", "falha_controlador")
                    elif isinstance(ack, dict):
                        resultado["status"] = ack.get("status", "cancelado")
                        if not ack.get("sucesso"):
                            resultado["sucesso"] = False
                            resultado["erro"] = ack.get("erro", "falha_controlador")
                    else:
                        resultado["status"] = "cancelado"
                    _registrar_log_ack_gateway(
                        tipo_acao="cancelar",
                        alvo=res.get("alvo"),
                        fonte=fonte,
                        session_id=res.get("session_id"),
                        ack=ack,
                        erro_fallback=resultado.get("erro", "falha_controlador"),
                    )

            self._responder_json(resultado)
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Endpoint não encontrado")

    def _responder_json(self, payload: Dict[str, Any], status: int = 200) -> None:
        corpo = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def log_message(self, format: str, *args: Any) -> None:
        # Suprime logs de requisições repetitivas da API
        pass


class AudioGatewayApp:
    """Aplicação integrada do Gateway de Áudio e Árbitro de Microfone Duplo."""

    def __init__(
        self,
        tcp_port: int = 8088,
        http_port: int = 8089,
        bind_host: str = "0.0.0.0",
        auth_token: str = DEFAULT_TOKEN,
        model_path: Optional[str] = DEFAULT_MODEL,
        tls_cert: Optional[str] = None,
        tls_key: Optional[str] = None,
        ipc_socket: str = DEFAULT_IPC_SOCKET,
    ):
        self.tcp_port = tcp_port
        self.http_port = http_port
        self.bind_host = bind_host
        self.auth_token = auth_token
        self.model_path = model_path
        self.tls_cert = tls_cert
        self.tls_key = tls_key
        self.ipc_socket = ipc_socket

        self.ipc_broadcaster = IpcEventBroadcaster(socket_path=ipc_socket) if ipc_socket else None
        self.arbitrator = SessionArbitrator(dedup_window_s=2.0)
        self.lg_mic_server = LgMicServer(
            host=bind_host,
            port=tcp_port,
            auth_token=auth_token,
            arbitrator=self.arbitrator,
            model_path=model_path,
            tls_cert=tls_cert,
            tls_key=tls_key,
            ipc_broadcaster=self.ipc_broadcaster,
        )
        self.me6s_monitor = Me6sJournalMonitor(arbitrator=self.arbitrator)

        self.http_server = ThreadingHTTPServer((bind_host, http_port), GatewayApiHandler)
        self.http_server.arbitrator = self.arbitrator
        self.http_server.lg_mic_server = self.lg_mic_server
        self.http_server.me6s_monitor = self.me6s_monitor
        self.http_server.ipc_broadcaster = self.ipc_broadcaster

    def start(self) -> None:
        logger.info("=== INICIANDO JARVIS DUAL-MIC AUDIO GATEWAY ===")
        logger.info("Porta TCP de streaming LG: %d (TLS=%s)", self.tcp_port, bool(self.lg_mic_server.ssl_context))
        logger.info("Porta HTTP de diagnóstico: %d", self.http_port)
        logger.info("Barramento IPC configurado em: %s", self.ipc_socket)
        logger.info("Modo operacional: SIMULAÇÃO SEGURA (WoL e SSH desativados)")

        self.lg_mic_server.start()
        self.me6s_monitor.start()

        http_thread = threading.Thread(target=self.http_server.serve_forever, daemon=True)
        http_thread.start()
        logger.info("Gateway iniciado com sucesso.")

    def stop(self) -> None:
        logger.info("Encerrando Gateway...")
        self.lg_mic_server.stop()
        self.me6s_monitor.stop()
        self.http_server.shutdown()
        logger.info("Gateway encerrado.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Jarvis Dual-Mic Audio Gateway")
    parser.add_argument("--tcp-port", type=int, default=8088, help="Porta TCP do áudio LG (padrão: 8088)")
    parser.add_argument("--http-port", type=int, default=8089, help="Porta HTTP da API de diagnóstico (padrão: 8089)")
    parser.add_argument("--host", default="0.0.0.0", help="Endereço de bind (padrão: 0.0.0.0)")
    parser.add_argument("--tls-cert", default=os.environ.get("JARVIS_TLS_CERT", ""), help="Caminho do certificado TLS")
    parser.add_argument("--tls-key", default=os.environ.get("JARVIS_TLS_KEY", ""), help="Caminho da chave privada TLS")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Caminho do modelo Vosk")
    parser.add_argument("--ipc-socket", default=os.environ.get("JARVIS_IPC_SOCKET", DEFAULT_IPC_SOCKET), help="Caminho do socket UNIX IPC")

    args = parser.parse_args()
    token = os.environ.get("JARVIS_AUDIO_TOKEN", "")
    if len(token) < 32 or len(token) > 256 or token == "jarvis-dual-mic-2026":
        parser.error("Configure JARVIS_AUDIO_TOKEN (32 a 256 caracteres aleatórios) em arquivo privado.")
    app = AudioGatewayApp(
        tcp_port=args.tcp_port,
        http_port=args.http_port,
        bind_host=args.host,
        auth_token=token,
        model_path=args.model,
        tls_cert=args.tls_cert if args.tls_cert else None,
        tls_key=args.tls_key if args.tls_key else None,
        ipc_socket=args.ipc_socket,
    )
    app.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        app.stop()


if __name__ == "__main__":
    main()
