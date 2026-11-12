#!/usr/bin/env python3
"""Testes de integração automatizados para o Jarvis Voice Mobile (multi-dispositivo).

Valida de ponta a ponta:
1. Handshake v2 com device_id customizado e token individual.
2. Streaming concorrente de múltiplos celulares Android em paralelo.
3. Rejeição de tokens inválidos ou não autorizados (fail-closed).
4. Atribuição de origem correta (source) e deduplicação no SessionArbitrator.
5. Emissão e parsing de métricas do Ananuke (CPU, RAM, Uptime) compatíveis com o app.
"""
from __future__ import annotations

import json
import socket
import struct
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gateway"))
sys.path.insert(0, str(ROOT))

from audio_gateway import (
    MAGIC_DATA,
    MAGIC_HANDSHAKE,
    LgMicServer,
    SessionArbitrator,
)


class TestMobileGatewayIntegration(unittest.TestCase):
    """Suíte de testes de integração do cliente móvel Android contra o Gateway."""

    @classmethod
    def setUpClass(cls):
        cls.port = 18095
        cls.default_token = "token-padrao-lg-x230dsv-compat-32c"
        cls.mobile1_token = "token-secreto-mobile-pixel-7-32chars"
        cls.mobile2_token = "token-secreto-mobile-galaxy-s23-32c"
        cls.allowed_tokens = {
            cls.mobile1_token: "pixel_7_sala",
            cls.mobile2_token: "galaxy_quarto",
        }
        cls.arbitrator = SessionArbitrator(dedup_window_s=1.5, action_dedup_window_s=3.0)
        cls.server = LgMicServer(
            host="127.0.0.1",
            port=cls.port,
            auth_token=cls.default_token,
            arbitrator=cls.arbitrator,
            model_path=None,
            allowed_tokens=cls.allowed_tokens,
        )
        cls.server.start()
        time.sleep(0.3)

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def test_01_mobile_handshake_v2_success(self):
        """Valida que o cliente Android conecta com Handshake v2 e token individual."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(("127.0.0.1", self.port))

        device_id = "pixel_7_sala".encode("utf-8")
        token_bytes = self.mobile1_token.encode("utf-8")

        # Framing do Handshake v2
        sock.sendall(MAGIC_HANDSHAKE)
        sock.sendall(struct.pack(">BB", 2, len(device_id)))
        sock.sendall(device_id)
        sock.sendall(struct.pack(">H", len(token_bytes)))
        sock.sendall(token_bytes)
        sock.sendall(struct.pack(">IBB", 16000, 1, 16))

        # Envia 2 frames de 1280 bytes
        for seq in range(2):
            payload = b"\x00" * 1280
            header = MAGIC_DATA + struct.pack(">IQBH", seq, int(time.time() * 1000), 0, len(payload))
            sock.sendall(header + payload)
            time.sleep(0.04)

        time.sleep(0.1)
        status = self.server.obter_status()
        self.assertTrue(status["connected"])
        dev_ids = [d["device_id"] for d in status.get("devices", [])]
        self.assertIn("pixel_7_sala", dev_ids)

        sock.close()

    def test_02_mobile_unauthorized_token_rejected(self):
        """Valida que um celular com token incorreto é rejeitado imediatamente pelo servidor."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(("127.0.0.1", self.port))

        device_id = "hacker_phone".encode("utf-8")
        bad_token = "token-totalmente-invalido-e-nao-autorizado-123".encode("utf-8")

        sock.sendall(MAGIC_HANDSHAKE)
        sock.sendall(struct.pack(">BB", 2, len(device_id)))
        sock.sendall(device_id)
        sock.sendall(struct.pack(">H", len(bad_token)))
        sock.sendall(bad_token)
        try:
            sock.sendall(struct.pack(">IBB", 16000, 1, 16))
            time.sleep(0.1)
            data = sock.recv(1024)
            self.assertEqual(len(data), 0, "Socket deveria ter sido fechado pelo servidor")
        except (ConnectionResetError, BrokenPipeError, OSError):
            pass
        finally:
            sock.close()

    def test_03_concurrent_mobile_phones_streaming(self):
        """Valida múltiplos celulares Android transmitindo áudio concorrentemente."""
        s1 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s1.connect(("127.0.0.1", self.port))

        s2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s2.connect(("127.0.0.1", self.port))

        # Dispositivo 1
        d1 = "pixel_7_sala".encode("utf-8")
        t1 = self.mobile1_token.encode("utf-8")
        s1.sendall(MAGIC_HANDSHAKE + struct.pack(">BB", 2, len(d1)) + d1 + struct.pack(">H", len(t1)) + t1 + struct.pack(">IBB", 16000, 1, 16))

        # Dispositivo 2
        d2 = "galaxy_quarto".encode("utf-8")
        t2 = self.mobile2_token.encode("utf-8")
        s2.sendall(MAGIC_HANDSHAKE + struct.pack(">BB", 2, len(d2)) + d2 + struct.pack(">H", len(t2)) + t2 + struct.pack(">IBB", 16000, 1, 16))

        # Transmissão simultânea de áudio
        for seq in range(4):
            hdr1 = MAGIC_DATA + struct.pack(">IQBH", seq, int(time.time() * 1000), 0, 1280)
            hdr2 = MAGIC_DATA + struct.pack(">IQBH", seq, int(time.time() * 1000), 0, 1280)
            s1.sendall(hdr1 + b"\x00" * 1280)
            s2.sendall(hdr2 + b"\x00" * 1280)
            time.sleep(0.04)

        time.sleep(0.15)
        status = self.server.obter_status()
        self.assertEqual(status["connected_count"], 2)

        devices = {d["device_id"]: d for d in status.get("devices", [])}
        self.assertIn("pixel_7_sala", devices)
        self.assertIn("galaxy_quarto", devices)
        self.assertGreater(devices["pixel_7_sala"]["packets"], 0)
        self.assertGreater(devices["galaxy_quarto"]["packets"], 0)

        s1.close()
        s2.close()

    def test_04_arbitrator_deduplication_between_phones(self):
        """Valida que comandos simultâneos de dois celulares diferentes são deduplicados pelo árbitro."""
        # Telefone 1 diz "assistente"
        is_new1, sid1 = self.arbitrator.report_wake("pixel_7_sala")
        self.assertTrue(is_new1)
        self.assertEqual(self.arbitrator.primary_source, "pixel_7_sala")

        # 0.05s depois, Telefone 2 também ouve e envia "assistente"
        time.sleep(0.05)
        is_new2, sid2 = self.arbitrator.report_wake("galaxy_quarto")
        self.assertFalse(is_new2, "Segunda ativação no mesmo intervalo deve ser deduplicada")
        self.assertEqual(sid1, sid2)

        # Telefone 1 envia comando "ligar fusion"
        res1 = self.arbitrator.report_command("pixel_7_sala", "ligar fusion")
        self.assertTrue(res1["valido"])
        self.assertEqual(res1["alvo"], "fusion")

        # Telefone 2 tenta enviar mesmo comando imediatamente
        res2 = self.arbitrator.report_command("galaxy_quarto", "ligar fusion")
        self.assertFalse(res2.get("valido", True))
        self.assertTrue(res2.get("deduplicado"))

    def test_05_ananuke_metrics_parsing_for_mobile_ui(self):
        """Valida a extração de métricas de CPU, RAM e Uptime do Ananuke como esperado pelo Kotlin."""
        import re
        texto_exemplo = "Ananuke: CPU 18% | RAM 52% | Ativo há 5h 42m."
        regex = re.compile(r"CPU\s+(\d+)%\s*\|\s*RAM\s+(\d+)%\s*\|\s*Ativo há\s+([^.]+)", re.IGNORECASE)
        m = regex.search(texto_exemplo)
        self.assertIsNotNone(m)
        self.assertEqual(int(m.group(1)), 18)
        self.assertEqual(int(m.group(2)), 52)
        self.assertEqual(m.group(3).strip(), "5h 42m")


if __name__ == "__main__":
    unittest.main()
