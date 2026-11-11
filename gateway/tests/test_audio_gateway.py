#!/usr/bin/env python3
"""Testes unitários e de integração para o Gateway de Áudio e Árbitro de Microfone Duplo."""
import json
import math
import socket
import struct
import sys
import time
import unittest
from pathlib import Path

# Adiciona o diretório do projeto ao path
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "gateway"))

from audio_gateway import (  # noqa: E402
    MAGIC_DATA,
    MAGIC_HANDSHAKE,
    AudioGatewayApp,
    LgMicServer,
    SessionArbitrator,
)


class TestSessionArbitrator(unittest.TestCase):
    """Valida o árbitro de sessão e o mecanismo de deduplicação."""

    def setUp(self):
        self.arbitrator = SessionArbitrator(dedup_window_s=1.5)

    def test_single_wake_starts_session(self):
        is_new, sid = self.arbitrator.report_wake("me6s")
        self.assertTrue(is_new)
        self.assertTrue(sid.startswith("sess-"))
        self.assertEqual(self.arbitrator.state, "comando")
        self.assertEqual(self.arbitrator.primary_source, "me6s")

    def test_deduplication_simultaneous_wake(self):
        # Primeiro microfone (ME6S) ouve "assistente"
        is_new1, sid1 = self.arbitrator.report_wake("me6s")
        self.assertTrue(is_new1)

        # 0.1s depois, segundo microfone (LG) também ouve a mesma fala
        time.sleep(0.05)
        is_new2, sid2 = self.arbitrator.report_wake("lg_mic")

        # Deve ser deduplicado (não abre segunda sessão)
        self.assertFalse(is_new2)
        self.assertEqual(sid1, sid2)
        self.assertEqual(self.arbitrator.deduplicated_count, 1)
        self.assertEqual(self.arbitrator.total_activations, 1)

    def test_command_wol_simulation(self):
        self.arbitrator.report_wake("me6s")
        res = self.arbitrator.report_command("lg_mic", "ligar fusion")

        self.assertTrue(res["valido"])
        self.assertEqual(res["acao"], "ligar")
        self.assertEqual(res["alvo"], "fusion")
        self.assertTrue(res.get("simulacao"))
        self.assertEqual(self.arbitrator.state, "espera")  # Concluiu a sessão

    def test_command_shutdown_requires_confirmation(self):
        self.arbitrator.report_wake("lg_mic")
        res = self.arbitrator.report_command("me6s", "desligar fusion")

        self.assertTrue(res["valido"])
        self.assertEqual(res["acao"], "desligar")
        self.assertTrue(res.get("confirmacao_necessaria"))
        self.assertEqual(self.arbitrator.state, "confirmacao")

        # Confirmação recebida
        res_conf = self.arbitrator.report_confirmation("lg_mic")
        self.assertTrue(res_conf["valido"])
        self.assertEqual(res_conf["acao"], "desligar_confirmado")
        self.assertTrue(res_conf.get("simulacao"))
        self.assertEqual(self.arbitrator.state, "espera")

    def test_command_cancellation(self):
        self.arbitrator.report_wake("lg_mic")
        self.assertEqual(self.arbitrator.state, "comando")

        res = self.arbitrator.report_command("lg_mic", "cancelar")
        self.assertTrue(res["valido"])
        self.assertEqual(res["acao"], "cancelar")
        self.assertEqual(self.arbitrator.state, "espera")


class TestLgMicServerProtocol(unittest.TestCase):
    """Valida o protocolo de rede TCP, autenticação e decodificação de frames PCM."""

    @classmethod
    def setUpClass(cls):
        cls.port = 18088
        cls.token = "token-teste-unitario-123"
        cls.arbitrator = SessionArbitrator()
        cls.server = LgMicServer(
            host="127.0.0.1",
            port=cls.port,
            auth_token=cls.token,
            arbitrator=cls.arbitrator,
            model_path=None,
        )
        cls.server.start()
        time.sleep(0.3)

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def test_rejected_with_invalid_token(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(("127.0.0.1", self.port))

        bad_token = b"token-errado"
        sock.sendall(MAGIC_HANDSHAKE)
        sock.sendall(struct.pack(">B", 1))
        sock.sendall(struct.pack(">H", len(bad_token)))
        sock.sendall(bad_token)
        sock.sendall(struct.pack(">IBB", 16000, 1, 16))

        # O servidor deve fechar a conexão (EOF ou ConnectionResetError)
        time.sleep(0.1)
        try:
            data = sock.recv(1024)
            self.assertEqual(len(data), 0)
        except (ConnectionResetError, BrokenPipeError):
            pass  # Conexão encerrada pelo servidor como esperado
        sock.close()

    def test_stream_and_rms_metrics(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(("127.0.0.1", self.port))

        # Handshake válido
        token_bytes = self.token.encode("utf-8")
        sock.sendall(MAGIC_HANDSHAKE)
        sock.sendall(struct.pack(">B", 1))
        sock.sendall(struct.pack(">H", len(token_bytes)))
        sock.sendall(token_bytes)
        sock.sendall(struct.pack(">IBB", 16000, 1, 16))

        # Envia 3 frames PCM com tom senoidal a 440 Hz
        sample_rate = 16000
        freq = 440.0
        n_samples = 640  # 40ms

        pcm_samples = bytearray()
        for i in range(n_samples):
            val = math.sin(2.0 * math.pi * freq * (i / sample_rate)) * 0.5
            ival = int(val * 32767.0)
            pcm_samples.extend(struct.pack("<h", ival))

        for seq in range(3):
            header = MAGIC_DATA + struct.pack(">IQBH", seq, int(time.time() * 1000), 0, len(pcm_samples))
            sock.sendall(header + pcm_samples)
            time.sleep(0.04)

        time.sleep(0.1)
        status = self.server.obter_status()
        self.assertTrue(status["connected"])
        self.assertGreater(status["packets_received"], 0)
        self.assertGreater(status["current_rms"], 0.2)  # Seno com amplitude 0.5 gera RMS ~0.35

        sock.close()


if __name__ == "__main__":
    unittest.main()
