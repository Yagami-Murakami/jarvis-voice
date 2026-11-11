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

    def test_action_deduplication_wol(self):
        # 1. Primeira ativação e comando WoL
        is_new, sid1 = self.arbitrator.report_wake("me6s")
        self.assertTrue(is_new)
        res1 = self.arbitrator.report_command("me6s", "ligar fusion")
        self.assertTrue(res1["valido"])
        self.assertEqual(res1["acao"], "ligar")

        # 2. Segunda ativação imediata (mesmo comando WoL para mesmo alvo)
        time.sleep(0.1)
        is_new, sid2 = self.arbitrator.report_wake("lg_mic")
        self.assertTrue(is_new)
        res2 = self.arbitrator.report_command("lg_mic", "ligar fusion")
        # Deve ser bloqueado pela deduplicação de ação (idempotência)
        self.assertFalse(res2["valido"])
        self.assertTrue(res2.get("deduplicado", False))

    def test_action_deduplication_shutdown(self):
        # 1. Primeira confirmação de desligamento
        self.arbitrator.report_wake("me6s")
        self.arbitrator.report_command("me6s", "desligar fusion")
        res1 = self.arbitrator.report_confirmation("me6s")
        self.assertTrue(res1["valido"])

        # 2. Segunda confirmação imediata para mesmo alvo
        time.sleep(0.1)
        self.arbitrator.report_wake("lg_mic")
        self.arbitrator.report_command("lg_mic", "desligar fusion")
        res2 = self.arbitrator.report_confirmation("lg_mic")
        # Deve ser bloqueado pela deduplicação de ação
        self.assertFalse(res2["valido"])
        self.assertTrue(res2.get("deduplicado", False))


class TestLgMicServerProtocol(unittest.TestCase):
    """Valida o protocolo de rede TCP, autenticação e decodificação de frames PCM."""

    @classmethod
    def setUpClass(cls):
        cls.port = 18088
        cls.token = "token-teste-unitario-32-chars-long-ok!"
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

        bad_token = b"token-incorreto-32-caracteres-ok!"
        try:
            sock.sendall(MAGIC_HANDSHAKE)
            sock.sendall(struct.pack(">B", 1))
            sock.sendall(struct.pack(">H", len(bad_token)))
            sock.sendall(bad_token)
            sock.sendall(struct.pack(">IBB", 16000, 1, 16))
        except (ConnectionResetError, BrokenPipeError):
            pass

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

    def test_rejected_with_deprecated_token(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(("127.0.0.1", self.port))

        bad_token = b"jarvis-dual-mic-2026"  # Token público antigo revogado
        sock.sendall(MAGIC_HANDSHAKE)
        sock.sendall(struct.pack(">B", 1))
        sock.sendall(struct.pack(">H", len(bad_token)))
        sock.sendall(bad_token)
        sock.sendall(struct.pack(">IBB", 16000, 1, 16))

        time.sleep(0.1)
        try:
            data = sock.recv(1024)
            self.assertEqual(len(data), 0)
        except (ConnectionResetError, BrokenPipeError):
            pass
        sock.close()

    def test_rejected_with_malformed_payload_length(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(("127.0.0.1", self.port))

        token_bytes = self.token.encode("utf-8")
        sock.sendall(MAGIC_HANDSHAKE)
        sock.sendall(struct.pack(">B", 1))
        sock.sendall(struct.pack(">H", len(token_bytes)))
        sock.sendall(token_bytes)
        sock.sendall(struct.pack(">IBB", 16000, 1, 16))

        # Envia header com payload_len = 500 (deveria ser 1280)
        malformed_len = 500
        header = MAGIC_DATA + struct.pack(">IQBH", 0, int(time.time() * 1000), 0, malformed_len)
        sock.sendall(header + b"\x00" * malformed_len)

        time.sleep(0.1)
        try:
            data = sock.recv(1024)
            self.assertEqual(len(data), 0)
        except (ConnectionResetError, BrokenPipeError):
            pass
        sock.close()


class TestTlsLgMicServerProtocol(unittest.TestCase):
    """Valida o transporte criptografado TLS 1.2+ no gateway de áudio."""

    @classmethod
    def setUpClass(cls):
        import tempfile
        import subprocess
        import ssl

        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.cert_path = Path(cls.temp_dir.name) / "test_gateway.crt"
        cls.key_path = Path(cls.temp_dir.name) / "test_gateway.key"

        # Gera certificado auto-assinado temporário para teste unitário
        cmd = [
            "openssl", "req", "-x509", "-newkey", "rsa:2048",
            "-keyout", str(cls.key_path),
            "-out", str(cls.cert_path),
            "-days", "1", "-nodes",
            "-subj", "/CN=127.0.0.1",
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        cls.port = 18089
        cls.token = "token-teste-tls-32-chars-long-ok!"
        cls.arbitrator = SessionArbitrator()
        cls.server = LgMicServer(
            host="127.0.0.1",
            port=cls.port,
            auth_token=cls.token,
            arbitrator=cls.arbitrator,
            model_path=None,
            tls_cert=str(cls.cert_path),
            tls_key=str(cls.key_path),
        )
        cls.server.start()
        time.sleep(0.3)

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()
        cls.temp_dir.cleanup()

    def test_tls_stream_and_authentication(self):
        import ssl

        raw_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        raw_sock.connect(("127.0.0.1", self.port))

        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE  # Certificado auto-assinado de teste

        sock = ssl_ctx.wrap_socket(raw_sock, server_hostname="127.0.0.1")

        # Handshake no túnel TLS
        token_bytes = self.token.encode("utf-8")
        sock.sendall(MAGIC_HANDSHAKE)
        sock.sendall(struct.pack(">B", 1))
        sock.sendall(struct.pack(">H", len(token_bytes)))
        sock.sendall(token_bytes)
        sock.sendall(struct.pack(">IBB", 16000, 1, 16))

        # Envia 1 quadro de áudio silencioso (1280 bytes)
        payload = b"\x00" * 1280
        header = MAGIC_DATA + struct.pack(">IQBH", 0, int(time.time() * 1000), 0, len(payload))
        sock.sendall(header + payload)

        time.sleep(0.1)
        status = self.server.obter_status()
        self.assertTrue(status["connected"])
        self.assertTrue(status["tls_active"])
        self.assertGreater(status["packets_received"], 0)

        sock.close()


if __name__ == "__main__":
    unittest.main()
