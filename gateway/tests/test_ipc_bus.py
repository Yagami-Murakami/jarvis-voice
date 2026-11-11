#!/usr/bin/env python3
"""Testes unitários para o Barramento IPC Local por UNIX Domain Socket."""
import json
import os
import shutil
import socket
import struct
import tempfile
import time
import unittest
from pathlib import Path

from gateway.ipc_bus import (
    DEFAULT_IPC_SOCKET,
    IpcEventBroadcaster,
    IpcEventListener,
    MAX_TIMESTAMP_DRIFT_S,
)


class TestIpcBus(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.sock_path = os.path.join(self.temp_dir, "test-event.sock")
        self.received_events = []

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _event_callback(self, evento):
        self.received_events.append(evento)

    def test_emit_and_receive_event(self):
        listener = IpcEventListener(
            socket_path=self.sock_path,
            on_event=self._event_callback,
            allowed_uid=os.getuid(),
            verify_peer_cred=True,
        )
        listener.start()
        try:
            broadcaster = IpcEventBroadcaster(socket_path=self.sock_path)
            ok = broadcaster.emit_event(
                source="lg_mic",
                event_type="wake",
                text="assistente",
                session_id="sess-test-1",
            )
            self.assertTrue(ok)
            time.sleep(0.1)

            self.assertEqual(len(self.received_events), 1)
            ev = self.received_events[0]
            self.assertEqual(ev["fonte"], "lg_mic")
            self.assertEqual(ev["evento"], "wake")
            self.assertEqual(ev["texto"], "assistente")
            self.assertEqual(ev["session_id"], "sess-test-1")
            self.assertTrue(ev.get("nonce"))
        finally:
            listener.stop()

    def test_replay_attack_rejected(self):
        listener = IpcEventListener(
            socket_path=self.sock_path,
            on_event=self._event_callback,
            allowed_uid=os.getuid(),
            verify_peer_cred=True,
        )
        listener.start()
        try:
            # Envia mensagem manual com nonce fixo
            payload = {
                "versao": 1,
                "fonte": "lg_mic",
                "evento": "comando",
                "texto": "ligar fusion",
                "timestamp": round(time.time(), 4),
                "nonce": "fixed_nonce_12345",
            }
            dados = (json.dumps(payload) + "\n").encode("utf-8")

            # 1º envio: deve ser aceito
            sock1 = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock1.connect(self.sock_path)
            sock1.sendall(dados)
            sock1.close()
            time.sleep(0.05)
            self.assertEqual(len(self.received_events), 1)

            # 2º envio (replay com mesmo nonce): deve ser rejeitado
            sock2 = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock2.connect(self.sock_path)
            sock2.sendall(dados)
            sock2.close()
            time.sleep(0.05)
            self.assertEqual(len(self.received_events), 1)
        finally:
            listener.stop()

    def test_expired_timestamp_rejected(self):
        listener = IpcEventListener(
            socket_path=self.sock_path,
            on_event=self._event_callback,
            allowed_uid=os.getuid(),
            verify_peer_cred=True,
        )
        listener.start()
        try:
            payload = {
                "versao": 1,
                "fonte": "lg_mic",
                "evento": "comando",
                "texto": "ligar fusion",
                "timestamp": time.time() - 10.0,  # 10 segundos no passado (> MAX_TIMESTAMP_DRIFT_S)
                "nonce": "nonce_old_timestamp",
            }
            dados = (json.dumps(payload) + "\n").encode("utf-8")

            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.connect(self.sock_path)
            sock.sendall(dados)
            sock.close()
            time.sleep(0.05)
            self.assertEqual(len(self.received_events), 0)
        finally:
            listener.stop()

    def test_nonexistent_socket_fails_gracefully(self):
        broadcaster = IpcEventBroadcaster(socket_path="/tmp/nonexistent_socket_test.sock")
        ok = broadcaster.emit_event(source="lg_mic", event_type="wake", text="assistente")
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
