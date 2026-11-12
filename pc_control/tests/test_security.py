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
    NonceCache,
    calcular_assinatura,
    validar_requisicao,
    verificar_assinatura,
)


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


if __name__ == "__main__":
    unittest.main()
