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

DEFAULT_IPC_SOCKET = "/run/jarvis/voice-event.sock"
MAX_TIMESTAMP_DRIFT_S = 2.0
NONCE_EXPIRY_S = 10.0


class IpcEventBroadcaster:
    """Emissor seguro de eventos para o barramento IPC local por UNIX Domain Socket."""

    def __init__(self, socket_path: str = DEFAULT_IPC_SOCKET) -> None:
        self.socket_path = socket_path
        self._seq = 0
        self._lock = threading.Lock()

    def emit_event(
        self,
        source: str,
        event_type: str,
        text: str,
        session_id: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Transmite evento validado para o barramento local UNIX se o listener estiver ativo."""
        if not self.socket_path:
            return False

        try:
            if not os.path.exists(self.socket_path):
                return False
        except OSError:
            return False

        with self._lock:
            self._seq += 1
            payload = {
                "versao": 1,
                "fonte": source,
                "evento": event_type,
                "texto": text,
                "session_id": session_id,
                "timestamp": round(time.time(), 4),
                "seq": self._seq,
                "nonce": secrets.token_hex(8),
            }
            if extra:
                payload["extra"] = extra

        dados = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(0.5)
        try:
            sock.connect(self.socket_path)
            sock.sendall(dados)
            return True
        except (socket.error, OSError) as e:
            logger.debug("Falha ao emitir evento IPC em %s: %s", self.socket_path, e)
            return False
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
            return

        # 3. Proteção contra Replay e Timestamp Drift
        now = time.time()
        ts = evento.get("timestamp", 0.0)
        if abs(now - ts) > MAX_TIMESTAMP_DRIFT_S:
            logger.warning("Mensagem IPC descartada: timestamp expirado ou dessincronizado (delta=%.2fs)", abs(now - ts))
            return

        nonce = evento.get("nonce")
        if not nonce:
            logger.warning("Mensagem IPC sem nonce descartada")
            return

        with self._lock:
            limite = now - NONCE_EXPIRY_S
            self._seen_nonces = {n: t for n, t in self._seen_nonces.items() if t > limite}

            if nonce in self._seen_nonces:
                logger.warning("Mensagem IPC descartada: tentativa de replay detectada (nonce=%s)", nonce)
                return
            self._seen_nonces[nonce] = now

        # 4. Despacho do evento para callback
        if self.on_event:
            try:
                self.on_event(evento)
            except Exception as e:
                logger.error("Erro no callback do evento IPC: %s", e)
