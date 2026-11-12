#!/usr/bin/env python3
"""Testes de Segurança, Autenticação HMAC e Proteção contra Replay.

Valida:
- Autenticação por HMAC-SHA256 (tokens válidos e inválidos).
- Tolerância máxima a timestamp drift (rejeita requisições defasadas > 5s).
- Prevenção contra Replay Attack por rastreamento rigoroso de nonces.
- Validação estrita de allowlists para targets e actions.
- Ausência de shell arbitrário e injeção de comandos.
"""
from __future__ import annotations

import time
import unittest

from pc_control.security import (
    MAX_TIMESTAMP_DRIFT_S,
    MIN_TOKEN_LENGTH,
    WEAK_DEFAULT_TOKENS,
    NonceCache,
    calcular_assinatura,
    is_strong_token,
    validar_requisicao,
    verificar_assinatura,
)
from pc_control.client import PcControlClient
from pc_control.agents.base_agent import PcAgentHandler, PcAgentServer
import http.server
import json
import os
import socket
import ssl
import subprocess
import tempfile
import threading
import urllib.request
import urllib.error
from unittest.mock import MagicMock, patch


class TestPcControlSecurity(unittest.TestCase):
    """Bateria de testes de segurança do Jarvis PC Control."""

    def setUp(self):
        self.token = "segredo-forte-de-teste-12345"
        self.cache = NonceCache(expiry_seconds=10.0)

    def test_01_hmac_signature_valid(self):
        """Valida que assinatura correta é aceita."""
        agora = time.time()
        nonce = "nonce-test-1"
        sig = calcular_assinatura(self.token, agora, nonce, "fusion", "abrir_navegador")
        self.assertTrue(verificar_assinatura(self.token, sig, agora, nonce, "fusion", "abrir_navegador"))

    def test_02_hmac_signature_invalid_token_rejected(self):
        """Valida que token incorreto é rejeitado."""
        agora = time.time()
        nonce = "nonce-test-2"
        sig = calcular_assinatura("token-errado", agora, nonce, "fusion", "abrir_navegador")
        self.assertFalse(verificar_assinatura(self.token, sig, agora, nonce, "fusion", "abrir_navegador"))

    def test_03_tampered_action_rejected(self):
        """Valida que adulteração de ação invalida a assinatura."""
        agora = time.time()
        nonce = "nonce-test-3"
        sig = calcular_assinatura(self.token, agora, nonce, "fusion", "abrir_navegador")
        self.assertFalse(verificar_assinatura(self.token, sig, agora, nonce, "fusion", "reiniciar"))

    def test_04_tampered_target_rejected(self):
        """Valida que adulteração de target invalida a assinatura."""
        agora = time.time()
        nonce = "nonce-test-4"
        sig = calcular_assinatura(self.token, agora, nonce, "fusion", "bloquear")
        self.assertFalse(verificar_assinatura(self.token, sig, agora, nonce, "ananuke", "bloquear"))

    def test_05_timestamp_drift_rejection(self):
        """Valida que timestamp com drift superior a 5 segundos é rejeitado."""
        agora = time.time()
        nonce = "nonce-test-5"
        timestamp_antigo = agora - 15.0  # 15s atrás (> 5s drift)
        sig = calcular_assinatura(self.token, timestamp_antigo, nonce, "fusion", "abrir_navegador")

        valido, motivo = validar_requisicao(
            token_esperado=self.token,
            target_esperado="fusion",
            target_recebido="fusion",
            action_recebida="abrir_navegador",
            timestamp=timestamp_antigo,
            nonce=nonce,
            assinatura=sig,
            nonce_cache=self.cache,
        )
        self.assertFalse(valido)
        self.assertIn("timestamp_expirado", motivo)

    def test_06_replay_attack_rejected_by_nonce_cache(self):
        """Valida que submissão duplicada do mesmo nonce é bloqueada como replay."""
        agora = time.time()
        nonce = "nonce-replay-001"
        sig = calcular_assinatura(self.token, agora, nonce, "fusion", "abrir_navegador")

        # 1a tentativa: válida
        v1, m1 = validar_requisicao(
            token_esperado=self.token,
            target_esperado="fusion",
            target_recebido="fusion",
            action_recebida="abrir_navegador",
            timestamp=agora,
            nonce=nonce,
            assinatura=sig,
            nonce_cache=self.cache,
        )
        self.assertTrue(v1, f"Falha na 1a tentativa: {m1}")

        # 2a tentativa idêntica (Replay): deve ser bloqueada
        v2, m2 = validar_requisicao(
            token_esperado=self.token,
            target_esperado="fusion",
            target_recebido="fusion",
            action_recebida="abrir_navegador",
            timestamp=agora,
            nonce=nonce,
            assinatura=sig,
            nonce_cache=self.cache,
        )
        self.assertFalse(v2)
        self.assertIn("nonce_reutilizado", m2)

    def test_07_unauthorized_target_rejected(self):
        """Valida que alvo desconhecido ou divergente é estritamente rejeitado."""
        agora = time.time()
        nonce = "nonce-test-7"
        sig = calcular_assinatura(self.token, agora, nonce, "outro_pc", "abrir_navegador")

        valido, motivo = validar_requisicao(
            token_esperado=self.token,
            target_esperado="fusion",
            target_recebido="outro_pc",
            action_recebida="abrir_navegador",
            timestamp=agora,
            nonce=nonce,
            assinatura=sig,
            nonce_cache=self.cache,
        )
        self.assertFalse(valido)
        self.assertIn("alvo_invalido", motivo)

    def test_08_cross_target_command_rejected(self):
        """Valida que comando endereçado para Ananuke enviado ao agente Fusion é rejeitado."""
        agora = time.time()
        nonce = "nonce-test-8"
        sig = calcular_assinatura(self.token, agora, nonce, "ananuke", "abrir_navegador")

        valido, motivo = validar_requisicao(
            token_esperado=self.token,
            target_esperado="fusion",  # Agente Fusion esperando fusion
            target_recebido="ananuke",  # Requisição enviada com ananuke
            action_recebida="abrir_navegador",
            timestamp=agora,
            nonce=nonce,
            assinatura=sig,
            nonce_cache=self.cache,
        )
        self.assertFalse(valido)
        self.assertIn("alvo_incompativel", motivo)

    def test_09_unauthorized_action_rejected(self):
        """Valida que qualquer ação arbitrária fora da allowlist é sumariamente bloqueada."""
        agora = time.time()
        nonce = "nonce-test-9"
        sig = calcular_assinatura(self.token, agora, nonce, "fusion", "executar_script_powershell")

        valido, motivo = validar_requisicao(
            token_esperado=self.token,
            target_esperado="fusion",
            target_recebido="fusion",
            action_recebida="executar_script_powershell",
            timestamp=agora,
            nonce=nonce,
            assinatura=sig,
            nonce_cache=self.cache,
        )
        self.assertFalse(valido)
        self.assertIn("acao_nao_permitida", motivo)

    def test_10_is_strong_token_rules(self):
        """Valida que tokens curtos, vazios ou padrão de dev são estritamente rejeitados."""
        self.assertFalse(is_strong_token(None))
        self.assertFalse(is_strong_token(""))
        self.assertFalse(is_strong_token("   "))
        self.assertFalse(is_strong_token("123456789012345"))  # 15 chars (< 16)
        self.assertFalse(is_strong_token("jarvis-pc-control-secret-v1-dev"))  # dev secret
        self.assertFalse(is_strong_token("chave_compartilhada_secreta_aqui"))
        self.assertTrue(is_strong_token("segredo-forte-valido-12345"))

    def test_11_weak_or_missing_token_rejected_in_validar_requisicao(self):
        """Valida que validar_requisicao rejeita fail-closed tokens fracos ou ausentes."""
        agora = time.time()
        nonce = "nonce-weak-token"
        sig = calcular_assinatura("123", agora, nonce, "fusion", "bloquear")

        valido_fraco, motivo_fraco = validar_requisicao(
            token_esperado="123",
            target_esperado="fusion",
            target_recebido="fusion",
            action_recebida="bloquear",
            timestamp=agora,
            nonce=nonce,
            assinatura=sig,
            nonce_cache=self.cache,
        )
        self.assertFalse(valido_fraco)
        self.assertEqual(motivo_fraco, "configuracao_incompleta_credencial_invalida_ou_fraca")

        valido_vazio, motivo_vazio = validar_requisicao(
            token_esperado="",
            target_esperado="fusion",
            target_recebido="fusion",
            action_recebida="bloquear",
            timestamp=agora,
            nonce=nonce,
            assinatura=sig,
            nonce_cache=self.cache,
        )
        self.assertFalse(valido_vazio)
        self.assertEqual(motivo_vazio, "configuracao_incompleta_credencial_invalida_ou_fraca")

    def test_12_client_fails_closed_on_weak_or_missing_token(self):
        """Valida que o PcControlClient não efetua requisição HTTP se o token for fraco ou ausente."""
        client_sem_token = PcControlClient(token="", url_overrides={"fusion": "http://127.0.0.1:9999"})
        resp = client_sem_token.enviar_comando(alvo="fusion", acao="bloquear", event_id="ev-sec-1")
        self.assertEqual(resp["status"], "falha")
        self.assertEqual(resp["erro"], "configuracao_incompleta_credencial_invalida_ou_ausente")

        client_token_fraco = PcControlClient(token="curto", url_overrides={"fusion": "http://127.0.0.1:9999"})
        resp2 = client_token_fraco.enviar_comando(alvo="fusion", acao="bloquear", event_id="ev-sec-2")
        self.assertEqual(resp2["status"], "falha")
        self.assertEqual(resp2["erro"], "configuracao_incompleta_credencial_invalida_ou_ausente")

    def test_13_no_secret_in_http_headers(self):
        """Valida que o token/segredo NUNCA é transmitido nos cabeçalhos HTTP."""
        captured_headers = {}

        class HeaderCaptureHandler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                content_len = int(self.headers.get("Content-Length", 0))
                self.rfile.read(content_len)
                for k, v in self.headers.items():
                    captured_headers[k.lower()] = v
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "concluido", "resultado": {}}).encode("utf-8"))

            def log_message(self, format, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), HeaderCaptureHandler)
        port = server.server_port
        th = threading.Thread(target=server.handle_request, daemon=True)
        th.start()

        segredo = "token-super-seguro-de-teste-12345"
        client = PcControlClient(token=segredo, url_overrides={"fusion": f"http://127.0.0.1:{port}"})
        resp = client.enviar_comando(alvo="fusion", acao="abrir_navegador", event_id="ev-sec-3")

        self.assertEqual(resp["status"], "concluido")
        self.assertNotIn("x-jarvis-token", captured_headers)
        self.assertNotIn("authorization", captured_headers)
        self.assertIn("x-jarvis-signature", captured_headers)
        server.server_close()

    def test_14_agent_rejects_unauthorized_ip(self):
        """Valida que o agente rejeita conexões de IPs fora da allowlist com 403 Forbidden."""
        class DummyAgent(PcAgentServer):
            def executar_acao(self, action: str, params=None, event_id="", session_id=None):
                return True, {}, None, "ok"

        agent = DummyAgent(
            host="127.0.0.1",
            port=0,
            target_id="fusion",
            auth_token="segredo-forte-de-teste-12345",
            allowed_ips={"192.168.0.250"},
        )
        port = agent.server_address[1]
        th = threading.Thread(target=agent.handle_request, daemon=True)
        th.start()

        req = urllib.request.Request(f"http://127.0.0.1:{port}/health")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=2.0)
        self.assertEqual(ctx.exception.code, 403)
        agent.server_close()

    def test_15_agent_default_allowed_ips_accepts_localhost(self):
        """Valida que sem JARVIS_PC_ALLOWED_IPS configurado, o agente aceita localhost por padrão."""
        class DummyAgent(PcAgentServer):
            def executar_acao(self, action: str, params=None, event_id="", session_id=None):
                return True, {}, None, "ok"

        agent = DummyAgent(
            host="127.0.0.1",
            port=0,
            target_id="fusion",
            auth_token="segredo-forte-de-teste-12345",
        )
        port = agent.server_address[1]
        th = threading.Thread(target=agent.handle_request, daemon=True)
        th.start()

        req = urllib.request.Request(f"http://127.0.0.1:{port}/health")
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            self.assertEqual(resp.status, 200)
        agent.server_close()

    def test_16_agent_handles_ipv4_mapped_ipv6(self):
        """Valida que endereço IPv4 mapeado sobre IPv6 (::ffff:127.0.0.1) é devidamente autorizado."""
        class DummyAgent(PcAgentServer):
            def executar_acao(self, action: str, params=None, event_id="", session_id=None):
                return True, {}, None, "ok"

        agent = DummyAgent(
            host="127.0.0.1",
            port=0,
            target_id="fusion",
            auth_token="segredo-forte-de-teste-12345",
            allowed_ips={"127.0.0.1"},
        )

        class FakeHandler:
            client_address = ("::ffff:127.0.0.1", 12345)
            server = agent

        # Chama a rotina interna _verificar_ip_autorizado com o client IPv4 mapeado
        handler_unbound = PcAgentHandler._verificar_ip_autorizado
        fake_inst = FakeHandler()
        self.assertTrue(handler_unbound(fake_inst))
        agent.server_close()

    def test_17_tls_incomplete_config_fails_initialization(self):
        """Valida que o agente falha ao inicializar se TLS estiver incompleto (apenas cert ou key)."""
        # Apenas certfile informado
        with self.assertRaises(ValueError) as ctx1:
            PcAgentServer(
                host="127.0.0.1",
                port=0,
                target_id="fusion",
                auth_token="segredo-forte-de-teste-12345",
                certfile="/caminho/cert.pem",
                keyfile=None,
            )
        self.assertIn("incompleta", str(ctx1.exception).lower())

        # Apenas keyfile informado
        with self.assertRaises(ValueError) as ctx2:
            PcAgentServer(
                host="127.0.0.1",
                port=0,
                target_id="fusion",
                auth_token="segredo-forte-de-teste-12345",
                certfile=None,
                keyfile="/caminho/key.pem",
            )
        self.assertIn("incompleta", str(ctx2.exception).lower())

        # TLS ativado por variável de ambiente sem fornecer cert/key
        with patch.dict(os.environ, {"JARVIS_PC_TLS_ENABLED": "1"}):
            with self.assertRaises(ValueError) as ctx3:
                PcAgentServer(
                    host="127.0.0.1",
                    port=0,
                    target_id="fusion",
                    auth_token="segredo-forte-de-teste-12345",
                )
            self.assertIn("incompleta", str(ctx3.exception).lower())

    def test_18_tls_missing_cert_files_fails_initialization(self):
        """Valida que o agente falha com FileNotFoundError se arquivos de certificado/chave não existirem."""
        with self.assertRaises(FileNotFoundError) as ctx:
            PcAgentServer(
                host="127.0.0.1",
                port=0,
                target_id="fusion",
                auth_token="segredo-forte-de-teste-12345",
                certfile="/tmp/arquivo_cert_inexistente_xyz_123.crt",
                keyfile="/tmp/arquivo_key_inexistente_xyz_123.key",
            )
        self.assertIn("não encontrado", str(ctx.exception).lower())

    def test_19_tls_invalid_cert_content_fails_initialization(self):
        """Valida que certificados corrompidos ou inválidos impedem inicialização e não fazem fallback para HTTP."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bad_cert = os.path.join(tmpdir, "bad_cert.pem")
            bad_key = os.path.join(tmpdir, "bad_key.pem")
            with open(bad_cert, "w", encoding="utf-8") as f:
                f.write("-----BEGIN CERTIFICATE-----\nCONTEUDO_INVALIDO\n-----END CERTIFICATE-----\n")
            with open(bad_key, "w", encoding="utf-8") as f:
                f.write("-----BEGIN PRIVATE KEY-----\nCONTEUDO_INVALIDO\n-----END PRIVATE KEY-----\n")

            with self.assertRaises(Exception) as ctx:
                PcAgentServer(
                    host="127.0.0.1",
                    port=0,
                    target_id="fusion",
                    auth_token="segredo-forte-de-teste-12345",
                    certfile=bad_cert,
                    keyfile=bad_key,
                )
            self.assertTrue(issubclass(type(ctx.exception), (ssl.SSLError, Exception)))

    def test_20_tls_valid_cert_initializes_tls_socket(self):
        """Valida que certificados válidos envelopam o socket do servidor em SSLSocket com TLS 1.2+."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cert_path = os.path.join(tmpdir, "test_cert.pem")
            key_path = os.path.join(tmpdir, "test_key.pem")
            cmd = [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-keyout",
                key_path,
                "-out",
                cert_path,
                "-days",
                "1",
                "-nodes",
                "-subj",
                "/CN=127.0.0.1",
            ]
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            server = PcAgentServer(
                host="127.0.0.1",
                port=0,
                target_id="fusion",
                auth_token="segredo-forte-de-teste-12345",
                certfile=cert_path,
                keyfile=key_path,
            )
            try:
                self.assertIsInstance(server.socket, ssl.SSLSocket)
            finally:
                server.server_close()

    def test_21_client_fails_closed_when_ca_cert_missing(self):
        """Valida que PcControlClient falha com erro seguro se o CA especificado não existir."""
        client = PcControlClient(
            token="segredo-forte-de-teste-12345",
            ca_cert="/tmp/ca_inexistente_98765.pem",
            url_overrides={"fusion": "https://127.0.0.1:8092"},
        )
        resp = client.enviar_comando("fusion", "abrir_navegador", "ev-ca-missing")
        self.assertEqual(resp["status"], "falha")
        self.assertIn("ca_cert_nao_encontrado", resp["erro"])

    def test_22_client_default_https_creates_verified_context(self):
        """Valida que por padrão conexões HTTPS utilizam contexto verificado (CERT_REQUIRED) e checam hostname."""
        client = PcControlClient(
            token="segredo-forte-de-teste-12345",
            url_overrides={"fusion": "https://127.0.0.1:8092"},
        )
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("JARVIS_PC_TLS_INSECURE_SKIP_VERIFY", None)
            with patch("urllib.request.urlopen") as mock_urlopen:
                mock_resp = MagicMock()
                mock_resp.status = 200
                mock_resp.read.return_value = json.dumps({"status": "concluido", "resultado": {}}).encode("utf-8")
                mock_resp.__enter__.return_value = mock_resp
                mock_urlopen.return_value = mock_resp

                resp = client.enviar_comando("fusion", "status_computador", "ev-https-default")
                self.assertEqual(resp["status"], "concluido")

                self.assertTrue(mock_urlopen.called)
                passed_ctx = mock_urlopen.call_args[1].get("context")
                self.assertIsNotNone(passed_ctx)
                self.assertEqual(passed_ctx.verify_mode, ssl.CERT_REQUIRED)
                self.assertTrue(passed_ctx.check_hostname)

    def test_23_client_insecure_skip_verify_creates_unverified_context(self):
        """Valida que com JARVIS_PC_TLS_INSECURE_SKIP_VERIFY=1 o contexto unverified é utilizado com alerta."""
        client = PcControlClient(
            token="segredo-forte-de-teste-12345",
            url_overrides={"fusion": "https://127.0.0.1:8092"},
        )
        with patch.dict(os.environ, {"JARVIS_PC_TLS_INSECURE_SKIP_VERIFY": "1"}):
            with patch("urllib.request.urlopen") as mock_urlopen:
                mock_resp = MagicMock()
                mock_resp.status = 200
                mock_resp.read.return_value = json.dumps({"status": "concluido", "resultado": {}}).encode("utf-8")
                mock_resp.__enter__.return_value = mock_resp
                mock_urlopen.return_value = mock_resp

                resp = client.enviar_comando("fusion", "status_computador", "ev-https-unverified")
                self.assertEqual(resp["status"], "concluido")

                passed_ctx = mock_urlopen.call_args[1].get("context")
                self.assertIsNotNone(passed_ctx)
                self.assertEqual(passed_ctx.verify_mode, ssl.CERT_NONE)
                self.assertFalse(passed_ctx.check_hostname)

    def test_24_client_reports_tls_verification_failure_specifically(self):
        """Valida que falha de verificação SSL retorna falha_tls_verificacao em vez de computador offline."""
        client = PcControlClient(
            token="segredo-forte-de-teste-12345",
            url_overrides={"fusion": "https://127.0.0.1:8092"},
        )
        ssl_err = urllib.error.URLError(ssl.SSLCertVerificationError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed"))
        with patch("urllib.request.urlopen", side_effect=ssl_err):
            resp = client.enviar_comando("fusion", "status_computador", "ev-https-ssl-fail")
            self.assertEqual(resp["status"], "falha")
            self.assertEqual(resp["erro"], "falha_tls_verificacao")
            self.assertIn("TLS", resp["mensagem_amigavel"])

    def test_25_live_tls_server_and_client_with_ca_and_san(self):
        """Valida comunicação ponta a ponta live HTTPS com certificado TLS e CA válidos via IP SAN."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cert_path = os.path.join(tmpdir, "cert.pem")
            key_path = os.path.join(tmpdir, "key.pem")
            cmd = [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-keyout",
                key_path,
                "-out",
                cert_path,
                "-days",
                "1",
                "-nodes",
                "-subj",
                "/CN=127.0.0.1",
                "-addext",
                "subjectAltName=IP:127.0.0.1",
            ]
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            class EchoAgent(PcAgentServer):
                def executar_acao(self, action, params, event_id, session_id=None):
                    return True, {"acao_executada": action}, None, f"Ação {action} ok"

            server = EchoAgent(
                host="127.0.0.1",
                port=0,
                target_id="fusion",
                auth_token="segredo-forte-de-teste-12345",
                certfile=cert_path,
                keyfile=key_path,
            )
            port = server.server_address[1]
            t = threading.Thread(target=server.serve_forever)
            t.daemon = True
            t.start()
            try:
                client = PcControlClient(
                    token="segredo-forte-de-teste-12345",
                    ca_cert=cert_path,
                    url_overrides={"fusion": f"https://127.0.0.1:{port}"},
                )
                resp = client.enviar_comando("fusion", "status_computador", "ev-live-https")
                self.assertEqual(resp["status"], "concluido")
                self.assertEqual(resp["resultado"], {"acao_executada": "status_computador"})
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
