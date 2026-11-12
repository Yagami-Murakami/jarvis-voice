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
import socket
import threading
import urllib.request
import urllib.error


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


if __name__ == "__main__":
    unittest.main()
