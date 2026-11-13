#!/usr/bin/env python3
"""Testes rigorosos de concorrência, reconexão e TLS para o Gateway Multi-Mic.

Cenários cobertos:
1. Conexão e streaming concorrente de LG (Handshake v1) e Mobile (Handshake v2) com TLS 1.2+.
2. Resiliência de reconexão: queda abrupta do LG enquanto Mobile continua transmitindo.
3. Resiliência de reconexão: queda abrupta do Mobile enquanto LG continua transmitindo.
4. Deduplicação de ativações e comandos simultâneos entre os múltiplos aparelhos.
5. Tentativa de conexão não autorizada descartada sem afetar clientes legítimos.
6. Cliente móvel em modo PTT mantendo conexão estável sem timeout indevido.
7. Compatibilidade do parser de tokens com ambos formatos JSON (token->dev e dev->token).
"""
from __future__ import annotations

import json
import math
import os
import socket
import ssl
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "gateway"))
sys.path.insert(0, str(ROOT))

from audio_gateway import (
    MAGIC_DATA,
    MAGIC_HANDSHAKE,
    LgMicServer,
    SessionArbitrator,
    parse_allowed_tokens,
)


class TestGatewayConcurrencyAndReconnection(unittest.TestCase):
    """Suíte avançada de concorrência e tolerância a falhas do Gateway."""

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.cert_path = Path(cls.temp_dir.name) / "test_multiclient.crt"
        cls.key_path = Path(cls.temp_dir.name) / "test_multiclient.key"

        # Gera certificado auto-assinado RSA 2048 para o teste TLS
        cmd = [
            "openssl", "req", "-x509", "-newkey", "rsa:2048",
            "-keyout", str(cls.key_path),
            "-out", str(cls.cert_path),
            "-days", "1", "-nodes",
            "-subj", "/CN=127.0.0.1",
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        cls.port = 18099
        cls.lg_token = "token-padrao-lg-x230dsv-compat-32c"
        cls.mobile_token = "token-secreto-mobile-pixel-7-32chars"
        cls.allowed_tokens = {
            cls.mobile_token: "celular_mobile",
        }

        cls.arbitrator = SessionArbitrator(dedup_window_s=1.5, action_dedup_window_s=3.0)
        cls.server = LgMicServer(
            host="127.0.0.1",
            port=cls.port,
            auth_token=cls.lg_token,
            arbitrator=cls.arbitrator,
            model_path=None,
            tls_cert=str(cls.cert_path),
            tls_key=str(cls.key_path),
            allowed_tokens=cls.allowed_tokens,
        )
        cls.server.start()
        time.sleep(0.3)

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()
        cls.temp_dir.cleanup()

    def _conectar_tls(self) -> ssl.SSLSocket:
        raw_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        raw_sock.connect(("127.0.0.1", self.port))
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx.wrap_socket(raw_sock, server_hostname="127.0.0.1")

    def _handshake_v1_lg(self, sock: ssl.SSLSocket, token: str) -> None:
        tok_bytes = token.encode("utf-8")
        sock.sendall(MAGIC_HANDSHAKE)
        sock.sendall(struct.pack(">B", 1))
        sock.sendall(struct.pack(">H", len(tok_bytes)))
        sock.sendall(tok_bytes)
        sock.sendall(struct.pack(">IBB", 16000, 1, 16))

    def _handshake_v2_mobile(self, sock: ssl.SSLSocket, device_id: str, token: str) -> None:
        dev_bytes = device_id.encode("utf-8")
        tok_bytes = token.encode("utf-8")
        sock.sendall(MAGIC_HANDSHAKE)
        sock.sendall(struct.pack(">BB", 2, len(dev_bytes)))
        sock.sendall(dev_bytes)
        sock.sendall(struct.pack(">H", len(tok_bytes)))
        sock.sendall(tok_bytes)
        sock.sendall(struct.pack(">IBB", 16000, 1, 16))

    def _enviar_frames(self, sock: ssl.SSLSocket, count: int, flags: int = 0) -> None:
        pcm = b"\x00" * 1280
        for seq in range(count):
            header = MAGIC_DATA + struct.pack(">IQBH", seq, int(time.time() * 1000), flags, len(pcm))
            sock.sendall(header + pcm)
            time.sleep(0.02)

    def test_01_concurrent_lg_v1_and_mobile_v2_with_tls(self):
        """Cenário 1: LG (v1) e Mobile (v2) conectam simultaneamente com TLS 1.2+ e transmitem áudio."""
        sock_lg = self._conectar_tls()
        self._handshake_v1_lg(sock_lg, self.lg_token)

        sock_mobile = self._conectar_tls()
        self._handshake_v2_mobile(sock_mobile, "celular_mobile", self.mobile_token)

        # Transmissão de áudio simultânea
        for seq in range(4):
            hdr_lg = MAGIC_DATA + struct.pack(">IQBH", seq, int(time.time() * 1000), 0, 1280)
            hdr_mob = MAGIC_DATA + struct.pack(">IQBH", seq, int(time.time() * 1000), 0, 1280)
            sock_lg.sendall(hdr_lg + b"\x00" * 1280)
            sock_mobile.sendall(hdr_mob + b"\x00" * 1280)
            time.sleep(0.03)

        time.sleep(0.1)
        status = self.server.obter_status()
        self.assertTrue(status["connected"])
        self.assertTrue(status["tls_active"])
        self.assertEqual(status["connected_count"], 2)

        devices = {d["device_id"]: d for d in status.get("devices", [])}
        self.assertIn("lg_mic", devices)
        self.assertIn("celular_mobile", devices)
        self.assertGreater(devices["lg_mic"]["packets"], 0)
        self.assertGreater(devices["celular_mobile"]["packets"], 0)

        sock_lg.close()
        sock_mobile.close()
        time.sleep(0.1)

    def test_02_reconnection_resilience_lg_drops_mobile_continues(self):
        """Cenário 2: LG cai abruptamente enquanto Mobile continua transmitindo sem interrupção."""
        sock_mobile = self._conectar_tls()
        self._handshake_v2_mobile(sock_mobile, "celular_mobile", self.mobile_token)

        sock_lg = self._conectar_tls()
        self._handshake_v1_lg(sock_lg, self.lg_token)

        # Envia áudio em ambos
        self._enviar_frames(sock_mobile, 2)
        self._enviar_frames(sock_lg, 2)

        # Queda abrupta do LG (fecha socket sem aviso)
        sock_lg.close()
        time.sleep(0.1)

        # Mobile continua transmitindo perfeitamente
        self._enviar_frames(sock_mobile, 3)

        status_after_drop = self.server.obter_status()
        self.assertEqual(status_after_drop["connected_count"], 1)
        self.assertEqual(status_after_drop["devices"][0]["device_id"], "celular_mobile")

        # LG reconecta com sucesso
        sock_lg_reconnect = self._conectar_tls()
        self._handshake_v1_lg(sock_lg_reconnect, self.lg_token)
        self._enviar_frames(sock_lg_reconnect, 2)

        time.sleep(0.1)
        status_recovered = self.server.obter_status()
        self.assertEqual(status_recovered["connected_count"], 2)

        sock_mobile.close()
        sock_lg_reconnect.close()
        time.sleep(0.1)

    def test_03_reconnection_resilience_mobile_drops_lg_continues(self):
        """Cenário 3: Mobile cai abruptamente enquanto LG continua transmitindo."""
        sock_lg = self._conectar_tls()
        self._handshake_v1_lg(sock_lg, self.lg_token)

        sock_mobile = self._conectar_tls()
        self._handshake_v2_mobile(sock_mobile, "celular_mobile", self.mobile_token)

        self._enviar_frames(sock_lg, 2)
        self._enviar_frames(sock_mobile, 2)

        # Mobile cai
        sock_mobile.close()
        time.sleep(0.1)

        # LG continua transmitindo
        self._enviar_frames(sock_lg, 3)

        status = self.server.obter_status()
        self.assertEqual(status["connected_count"], 1)
        self.assertEqual(status["devices"][0]["device_id"], "lg_mic")

        # Mobile reconecta
        sock_mobile_reconnect = self._conectar_tls()
        self._handshake_v2_mobile(sock_mobile_reconnect, "celular_mobile", self.mobile_token)
        self._enviar_frames(sock_mobile_reconnect, 2)

        time.sleep(0.1)
        status_reconnected = self.server.obter_status()
        self.assertEqual(status_reconnected["connected_count"], 2)

        sock_lg.close()
        sock_mobile_reconnect.close()
        time.sleep(0.1)

    def test_04_session_deduplication_between_lg_and_mobile(self):
        """Cenário 4: Ambos dispositivos detectam wake e comando em curto intervalo."""
        # 1. LG detecta "assistente"
        is_new1, sid1 = self.arbitrator.report_wake("lg_mic")
        self.assertTrue(is_new1)
        self.assertEqual(self.arbitrator.primary_source, "lg_mic")

        # 2. 80ms depois, Mobile também detecta "assistente"
        time.sleep(0.08)
        is_new2, sid2 = self.arbitrator.report_wake("celular_mobile")
        self.assertFalse(is_new2)
        self.assertEqual(sid1, sid2)

        # 3. LG envia comando "ligar fusion"
        res1 = self.arbitrator.report_command("lg_mic", "ligar fusion")
        self.assertTrue(res1["valido"])
        self.assertEqual(res1["acao"], "ligar")
        self.assertEqual(res1["alvo"], "fusion")

        # 4. Mobile envia o mesmo comando imediatamente
        res2 = self.arbitrator.report_command("celular_mobile", "ligar fusion")
        self.assertFalse(res2.get("valido", True))
        self.assertTrue(res2.get("deduplicado"))

    def test_05_unauthorized_token_rejection_does_not_affect_active_clients(self):
        """Cenário 5: Tentativa de conexão com token incorreto é rejeitada sem perturbar clientes ativos."""
        sock_lg = self._conectar_tls()
        self._handshake_v1_lg(sock_lg, self.lg_token)

        sock_mobile = self._conectar_tls()
        self._handshake_v2_mobile(sock_mobile, "celular_mobile", self.mobile_token)

        self._enviar_frames(sock_lg, 2)
        self._enviar_frames(sock_mobile, 2)

        # Cliente intruso com token incorreto
        sock_rogue = self._conectar_tls()
        dev_rogue = "celular_invasor".encode("utf-8")
        bad_token = "token-totalmente-errado-e-nao-autorizado-12".encode("utf-8")
        sock_rogue.sendall(MAGIC_HANDSHAKE + struct.pack(">BB", 2, len(dev_rogue)) + dev_rogue + struct.pack(">H", len(bad_token)) + bad_token + struct.pack(">IBB", 16000, 1, 16))

        time.sleep(0.1)
        try:
            data = sock_rogue.recv(1024)
            self.assertEqual(len(data), 0)
        except (ConnectionResetError, BrokenPipeError, ssl.SSLError):
            pass
        finally:
            sock_rogue.close()

        # Clientes legítimos continuam 100% funcionais
        self._enviar_frames(sock_lg, 2)
        self._enviar_frames(sock_mobile, 2)

        status = self.server.obter_status()
        self.assertEqual(status["connected_count"], 2)

        sock_lg.close()
        sock_mobile.close()
        time.sleep(0.1)

    def test_06_idle_ptt_mobile_client_stays_connected(self):
        """Cenário 6: Cliente móvel em modo PTT mantendo conexão com flag heartbeat."""
        sock_mobile = self._conectar_tls()
        self._handshake_v2_mobile(sock_mobile, "celular_mobile", self.mobile_token)

        # Envia heartbeat (flag 0x02) a cada 0.1s simulando cliente ocioso
        for _ in range(5):
            hdr = MAGIC_DATA + struct.pack(">IQBH", 0, int(time.time() * 1000), 0x02, 1280)
            sock_mobile.sendall(hdr + b"\x00" * 1280)
            time.sleep(0.1)

        status = self.server.obter_status()
        self.assertTrue(status["connected"])
        self.assertEqual(status["connected_count"], 1)

        sock_mobile.close()
        time.sleep(0.1)

    def test_07_allowed_tokens_parsing_both_json_formats(self):
        """Cenário 7: Valida suporte a {"token": "dev"} e {"dev": "token"}."""
        # Formato 1: {"token": "device"}
        f1 = '{"11112222333344445555666677778888": "sala_phone"}'
        res1 = parse_allowed_tokens(f1)
        self.assertEqual(res1.get("11112222333344445555666677778888"), "sala_phone")

        # Formato 2: {"device": "token"}
        f2 = '{"quarto_phone": "aaaabbbbccccddddeeeeffffgggghhhh"}'
        res2 = parse_allowed_tokens(f2)
        self.assertEqual(res2.get("aaaabbbbccccddddeeeeffffgggghhhh"), "quarto_phone")


if __name__ == "__main__":
    unittest.main()
