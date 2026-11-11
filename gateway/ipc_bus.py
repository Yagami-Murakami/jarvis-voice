#!/usr/bin/env python3
"""
Barramento IPC Local por UNIX Domain Socket com Autenticação SO_PEERCRED
e Proteção contra Replay para o Jarvis Voice Dual-Mic.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import socket
import struct
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger("JarvisIPC")

DEFAULT_IPC_SOCKET = os.getenv("JARVIS_IPC_SOCKET")
if not DEFAULT_IPC_SOCKET:
    if os.getuid() == 0 or os.path.exists("/run/jarvis"):
        DEFAULT_IPC_SOCKET = "/run/jarvis/voice-event.sock"
    else:
        DEFAULT_IPC_SOCKET = f"/tmp/jarvis_voice_{os.getuid()}.sock"
MAX_TIMESTAMP_DRIFT_S = 2.0
NONCE_EXPIRY_S = 10.0


class IpcEventBroadcaster:
    """Emissor seguro de eventos para o barramento IPC local por UNIX Domain Socket."""

    def __init__(self, socket_path: str = DEFAULT_IPC_SOCKET, timeout: float = 2.0) -> None:
        self.socket_path = socket_path
        self.timeout = timeout
        self._seq = 0
        self._lock = threading.Lock()
        self.last_ack: Optional[Dict[str, Any]] = None
        self.last_error: Optional[str] = None

    def emit_event(
        self,
        source: str,
        event_type: str,
        text: str,
        session_id: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
        event_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Transmite evento validado para o barramento local UNIX e aguarda confirmação (ACK).

        Retorna o dicionário de ACK em caso de sucesso confirmado, ou None em caso de
        falha, timeout, ACK ausente ou rejeição pelo listener.
        """
        if not self.socket_path:
            return None

        try:
            if not os.path.exists(self.socket_path):
                return None
        except OSError:
            return None

        ev_id = event_id or f"ev-{int(time.time() * 1000)}-{secrets.token_hex(4)}"
        nonce = f"nonce_{ev_id}"

        with self._lock:
            self._seq += 1
            payload = {
                "versao": 1,
                "event_id": ev_id,
                "fonte": source,
                "evento": event_type,
                "texto": text,
                "session_id": session_id,
                "timestamp": round(time.time(), 4),
                "seq": self._seq,
                "nonce": nonce,
            }
            if extra:
                payload["extra"] = extra

        dados = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(self.socket_path)
            sock.sendall(dados)

            resp = b""
            while b"\n" not in resp:
                chunk = sock.recv(1024)
                if not chunk:
                    break
                resp += chunk
                if len(resp) > 4096:
                    break

            if not resp:
                logger.warning("Falha IPC: Conexão fechada pelo listener sem envio de ACK")
                self.last_error = "ack_ausente"
                self.last_ack = None
                return None

            try:
                ack = json.loads(resp.decode("utf-8").strip())
            except Exception as e:
                logger.warning("Falha IPC: ACK inválido recebido (JSON inválido: %s)", e)
                self.last_error = "ack_invalido"
                self.last_ack = None
                return None

            if not isinstance(ack, dict):
                logger.warning("Falha IPC: ACK com formato incorreto (não é objeto JSON)")
                self.last_error = "ack_formato_incorreto"
                self.last_ack = None
                return None

            # 1. Validação obrigatória de correspondência do event_id
            ack_event_id = ack.get("event_id") or ack.get("nonce")
            if not ack_event_id or not isinstance(ack_event_id, str):
                logger.warning("Falha IPC: ACK rejeitado - event_id ausente ou vazio")
                self.last_error = "event_id_ausente"
                self.last_ack = ack
                return None

            expected_event_id = ev_id
            if ack_event_id != expected_event_id:
                logger.warning(
                    "Falha IPC: ACK rejeitado - event_id divergente (esperado=%s, recebido=%s)",
                    expected_event_id,
                    ack_event_id,
                )
                self.last_error = "event_id_divergente"
                self.last_ack = ack
                return None

            # 2. Validação obrigatória do campo 'sucesso' (deve ser estritamente True)
            if "sucesso" not in ack:
                logger.warning("Falha IPC: ACK rejeitado - campo 'sucesso' ausente")
                self.last_error = "sucesso_ausente"
                self.last_ack = ack
                return None

            if ack.get("sucesso") is not True:
                self.last_error = str(ack.get("erro") or ack.get("motivo") or "rejeitado")
                logger.warning("Falha IPC: Listener rejeitou o evento: %s", self.last_error)
                self.last_ack = ack
                return None

            # 3. Validação obrigatória do campo 'estado'
            estado_ack = ack.get("estado")
            if not estado_ack or estado_ack not in ("espera", "comando", "confirmacao"):
                logger.warning("Falha IPC: ACK rejeitado - campo 'estado' ausente ou inválido (%s)", estado_ack)
                self.last_error = "estado_invalido"
                self.last_ack = ack
                return None

            # 4. Validação obrigatória de confirmação real do processamento
            # Exige 'processado': True OU status válido ('aceito', 'em_processamento', 'concluido')
            processado = ack.get("processado")
            status_ack = ack.get("status")
            confirmacao_valida = (processado is True) or (status_ack in ("aceito", "em_processamento", "concluido"))
            if not confirmacao_valida:
                logger.warning(
                    "Falha IPC: ACK rejeitado - confirmação real do processamento ausente (processado=%s, status=%s)",
                    processado,
                    status_ack,
                )
                self.last_error = "confirmacao_processamento_ausente"
                self.last_ack = ack
                return None

            self.last_ack = ack
            self.last_error = None
            return ack
        except (socket.timeout, TimeoutError):
            logger.warning("Falha IPC: Timeout (%.2fs) aguardando ACK em %s", self.timeout, self.socket_path)
            self.last_error = "timeout_ack"
            self.last_ack = None
            return None
        except (socket.error, OSError) as e:
            logger.debug("Falha ao emitir evento IPC em %s: %s", self.socket_path, e)
            self.last_error = str(e)
            self.last_ack = None
            return None
        finally:
            try:
                sock.close()
            except Exception:
                pass


class IpcEventListener:
    """Servidor UNIX Domain Socket para recepção de eventos autenticados."""

    def __init__(
        self,
        socket_path: str = DEFAULT_IPC_SOCKET,
        on_event: Optional[Callable[[Dict[str, Any]], None]] = None,
        allowed_uid: int = 0,
        verify_peer_cred: bool = True,
    ) -> None:
        self.socket_path = socket_path
        self.on_event = on_event
        self.allowed_uid = allowed_uid
        self.verify_peer_cred = verify_peer_cred
        self._running = False
        self._server_sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._seen_nonces: Dict[str, float] = {}
        self._processed_events: Dict[str, Tuple[Dict[str, Any], float]] = {}
        self._lock = threading.Lock()

    def start(self) -> None:
        if self._running:
            return

        sock_path = Path(self.socket_path)
        sock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(str(sock_path.parent), 0o750)
        except OSError:
            pass

        if sock_path.exists():
            try:
                sock_path.unlink()
            except OSError:
                pass

        self._server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server_sock.bind(str(sock_path))
        try:
            os.chmod(str(sock_path), 0o600)
        except OSError:
            pass

        self._server_sock.listen(8)
        self._server_sock.settimeout(1.0)
        self._running = True

        self._thread = threading.Thread(target=self._accept_loop, name="JarvisIpcListener", daemon=True)
        self._thread.start()
        logger.info("Barramento IPC ouvindo em %s (Permissões 0600, UID=%d)", self.socket_path, self.allowed_uid)

    def stop(self) -> None:
        self._running = False
        if self._server_sock:
            try:
                self._server_sock.close()
            except Exception:
                pass
            self._server_sock = None

        if Path(self.socket_path).exists():
            try:
                Path(self.socket_path).unlink()
            except OSError:
                pass

    def _accept_loop(self) -> None:
        while self._running:
            try:
                client_sock, _ = self._server_sock.accept()
            except socket.timeout:
                continue
            except (socket.error, OSError):
                break

            try:
                self._handle_client(client_sock)
            except Exception as e:
                logger.warning("Erro ao processar mensagem IPC: %s", e)
            finally:
                try:
                    client_sock.close()
                except Exception:
                    pass

    def _handle_client(self, client_sock: socket.socket) -> None:
        # 1. Autenticação do processo Linux via SO_PEERCRED
        if self.verify_peer_cred and hasattr(socket, "SO_PEERCRED"):
            try:
                creds = client_sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
                pid, uid, gid = struct.unpack("3i", creds)
                if uid != self.allowed_uid:
                    logger.warning("Conexão IPC rejeitada: PID %d com UID %d (esperado %d)", pid, uid, self.allowed_uid)
                    return
            except OSError as e:
                logger.warning("Falha ao obter SO_PEERCRED: %s", e)
                return

        # 2. Leitura da mensagem JSON (max 4KB)
        client_sock.settimeout(1.0)
        buffer = b""
        while len(buffer) < 4096:
            chunk = client_sock.recv(1024)
            if not chunk:
                break
            buffer += chunk
            if b"\n" in buffer:
                break

        linha = buffer.decode("utf-8", errors="replace").strip()
        if not linha:
            return

        try:
            evento = json.loads(linha)
        except json.JSONDecodeError:
            logger.warning("Mensagem IPC com JSON inválido descartada")
            resposta = {"sucesso": False, "erro": "json_invalido", "processado": False}
            try:
                client_sock.sendall((json.dumps(resposta) + "\n").encode("utf-8"))
            except Exception:
                pass
            return

        # 3. Proteção contra Replay e Timestamp Drift
        now = time.time()
        ts = evento.get("timestamp", 0.0)
        if abs(now - ts) > MAX_TIMESTAMP_DRIFT_S:
            logger.warning("Mensagem IPC descartada: timestamp expirado ou dessincronizado (delta=%.2fs)", abs(now - ts))
            resposta = {
                "sucesso": False,
                "erro": "timestamp_expirado",
                "delta": abs(now - ts),
                "processado": False,
            }
            try:
                client_sock.sendall((json.dumps(resposta) + "\n").encode("utf-8"))
            except Exception:
                pass
            return

        nonce = evento.get("nonce")
        if not nonce:
            logger.warning("Mensagem IPC sem nonce descartada")
            resposta = {"sucesso": False, "erro": "nonce_ausente", "processado": False}
            try:
                client_sock.sendall((json.dumps(resposta) + "\n").encode("utf-8"))
            except Exception:
                pass
            return

        event_id = evento.get("event_id") or nonce

        with self._lock:
            limite = now - NONCE_EXPIRY_S
            self._seen_nonces = {n: t for n, t in self._seen_nonces.items() if t > limite}
            self._processed_events = {n: (r, t) for n, (r, t) in self._processed_events.items() if t > limite}

            # Retransmissão: verifica se este event_id ou nonce já foi processado
            lookup_key = event_id if event_id in self._processed_events else (nonce if nonce in self._processed_events else None)
            if lookup_key:
                cached_resp, _ = self._processed_events[lookup_key]
                logger.info("Retransmissão de evento detectada (%s). Retornando resposta em cache.", lookup_key)
                cached_copy = dict(cached_resp)
                cached_copy["retransmissao"] = True
                try:
                    dados_resposta = (json.dumps(cached_copy, ensure_ascii=False) + "\n").encode("utf-8")
                    client_sock.sendall(dados_resposta)
                except Exception:
                    pass
                return

            if nonce in self._seen_nonces or (event_id and event_id in self._seen_nonces):
                logger.warning("Mensagem IPC descartada: tentativa de replay em processamento (nonce=%s, event_id=%s)", nonce, event_id)
                resposta = {"sucesso": False, "erro": "replay_detectado", "nonce": nonce, "event_id": event_id, "processado": False}
                try:
                    client_sock.sendall((json.dumps(resposta) + "\n").encode("utf-8"))
                except Exception:
                    pass
                return

            self._seen_nonces[nonce] = now
            if event_id:
                self._seen_nonces[event_id] = now

        # 4. Despacho do evento para callback com suporte a resposta / confirmação
        resposta = {
            "sucesso": True,
            "active_session_id": None,
            "event_id": event_id,
            "estado": "espera",
            "status": "concluido",
            "processado": True,
        }
        if self.on_event:
            try:
                res = self.on_event(evento)
                if isinstance(res, dict):
                    resposta = res
                elif res is not None:
                    resposta = {"sucesso": bool(res), "processado": bool(res), "estado": "espera", "status": "concluido"}
            except Exception as e:
                logger.error("Erro no callback do evento IPC: %s", e)
                resposta = {"sucesso": False, "erro": str(e), "processado": False, "estado": "espera", "status": "falha"}

        if "event_id" not in resposta:
            resposta["event_id"] = event_id
        if "estado" not in resposta:
            resposta["estado"] = "espera"
        if "processado" not in resposta:
            resposta["processado"] = True
        if "status" not in resposta:
            resposta["status"] = "concluido" if resposta.get("sucesso") else "falha"

        # Cacheia a resposta para tolerar retransmissões do mesmo evento sem duplicar ações
        with self._lock:
            self._processed_events[nonce] = (resposta, now)
            if event_id:
                self._processed_events[event_id] = (resposta, now)

        try:
            dados_resposta = (json.dumps(resposta, ensure_ascii=False) + "\n").encode("utf-8")
            client_sock.sendall(dados_resposta)
        except Exception:
            pass
