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

    def test_second_wake_during_active_session_preserves_session_id(self):
        """Cenário 2: Uma segunda ativação pelo LG durante sessão já aberta NUNCA substitui o ID."""
        # 1. Sessão inicial aberta pelo LG
        is_new1, sid1 = self.arbitrator.report_wake("lg_mic")
        self.assertTrue(is_new1)
        self.assertEqual(self.arbitrator.state, "comando")

        # 2. Usuário repete 'assistente' pelo mesmo microfone LG dentro da sessão ativa
        time.sleep(0.05)
        is_new2, sid2 = self.arbitrator.report_wake("lg_mic")
        self.assertFalse(is_new2)
        self.assertEqual(sid2, sid1)
        self.assertEqual(self.arbitrator.session_id, sid1)

        # 3. Microfone ME6S também repete 'assistente'
        is_new3, sid3 = self.arbitrator.report_wake("me6s")
        self.assertFalse(is_new3)
        self.assertEqual(sid3, sid1)
        self.assertEqual(self.arbitrator.session_id, sid1)

    def test_sync_session_id_with_controller(self):
        """Valida que o Gateway adota a autoridade única do controlador em caso de divergência."""
        is_new, sid1 = self.arbitrator.report_wake("lg_mic")
        self.assertTrue(is_new)

        controller_authoritative_id = "sess-controller-official-999"
        self.arbitrator.sync_session_id(controller_authoritative_id)
        self.assertEqual(self.arbitrator.session_id, controller_authoritative_id)

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
        try:
            sock.sendall(struct.pack(">IBB", 16000, 1, 16))
            time.sleep(0.1)
            data = sock.recv(1024)
            self.assertEqual(len(data), 0)
        except (ConnectionResetError, BrokenPipeError, OSError):
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


class TestMultiClientLgMicServerProtocol(unittest.TestCase):
    """Valida o suporte a múltiplos smartphones conectados simultaneamente com tokens individuais e handshake v1/v2."""

    @classmethod
    def setUpClass(cls):
        cls.port = 18090
        cls.default_token = "token-padrao-lg-mic-32-caracteres-ok"
        cls.mobile1_token = "token-individual-celular-sala-32-chars"
        cls.mobile2_token = "token-individual-celular-quarto-32-c"
        cls.allowed_tokens = {
            cls.mobile1_token: "celular_sala",
            cls.mobile2_token: "celular_quarto",
        }
        cls.arbitrator = SessionArbitrator()
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

    def test_version_2_handshake_with_device_id(self):
        """Valida conexão com handshake v2 contendo device_id explícito."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(("127.0.0.1", self.port))

        device_id = "celular_sala".encode("utf-8")
        token_bytes = self.mobile1_token.encode("utf-8")

        # Handshake v2: JMIC + \x02 + len(dev) + dev + len(tok) + tok + 16000 + 1 + 16
        sock.sendall(MAGIC_HANDSHAKE)
        sock.sendall(struct.pack(">BB", 2, len(device_id)))
        sock.sendall(device_id)
        sock.sendall(struct.pack(">H", len(token_bytes)))
        sock.sendall(token_bytes)
        sock.sendall(struct.pack(">IBB", 16000, 1, 16))

        # Envia 2 frames de áudio
        for seq in range(2):
            payload = b"\x00" * 1280
            header = MAGIC_DATA + struct.pack(">IQBH", seq, int(time.time() * 1000), 0, len(payload))
            sock.sendall(header + payload)
            time.sleep(0.04)

        time.sleep(0.1)
        status = self.server.obter_status()
        self.assertTrue(status["connected"])
        self.assertGreaterEqual(status["connected_count"], 1)

        dev_names = [d["device_id"] for d in status.get("devices", [])]
        self.assertIn("celular_sala", dev_names)

        sock.close()

    def test_multi_client_concurrent_streaming(self):
        """Valida que dois clientes (LG legado v1 e Mobile v2) transmitem áudio em paralelo sem interferência."""
        sock_lg = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock_lg.connect(("127.0.0.1", self.port))

        sock_mobile = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock_mobile.connect(("127.0.0.1", self.port))

        # 1. Handshake LG (v1)
        lg_token = self.default_token.encode("utf-8")
        sock_lg.sendall(MAGIC_HANDSHAKE)
        sock_lg.sendall(struct.pack(">B", 1))
        sock_lg.sendall(struct.pack(">H", len(lg_token)))
        sock_lg.sendall(lg_token)
        sock_lg.sendall(struct.pack(">IBB", 16000, 1, 16))

        # 2. Handshake Mobile (v2)
        dev_id = "celular_quarto".encode("utf-8")
        mob_token = self.mobile2_token.encode("utf-8")
        sock_mobile.sendall(MAGIC_HANDSHAKE)
        sock_mobile.sendall(struct.pack(">BB", 2, len(dev_id)))
        sock_mobile.sendall(dev_id)
        sock_mobile.sendall(struct.pack(">H", len(mob_token)))
        sock_mobile.sendall(mob_token)
        sock_mobile.sendall(struct.pack(">IBB", 16000, 1, 16))

        # 3. Transmissão concorrente
        for seq in range(3):
            header = MAGIC_DATA + struct.pack(">IQBH", seq, int(time.time() * 1000), 0, 1280)
            sock_lg.sendall(header + b"\x00" * 1280)
            sock_mobile.sendall(header + b"\x00" * 1280)
            time.sleep(0.04)

        time.sleep(0.15)
        status = self.server.obter_status()
        self.assertEqual(status["connected_count"], 2)

        devs = {d["device_id"]: d for d in status.get("devices", [])}
        self.assertIn("lg_mic", devs)
        self.assertIn("celular_quarto", devs)
        self.assertGreater(devs["lg_mic"]["packets"], 0)
        self.assertGreater(devs["celular_quarto"]["packets"], 0)

        sock_lg.close()
        sock_mobile.close()


if __name__ == "__main__":
    unittest.main()
