#!/usr/bin/env python3
"""Testes Unitários do Cliente e do Despachante do Jarvis PC Control.

Valida:
- Feature Flag JARVIS_PC_CONTROL_ENABLED (desativado por padrão).
- Fluxo de confirmação em duas etapas para fechar navegador e reiniciar.
- Rejeição estrita de confirmações de outra sessão (session_id divergente).
- Rejeição de confirmações atrasadas (após timeout).
- Cancelamento explícito via comando 'cancelar'.
- Deduplicação por idempotência de ações recentes.
- Tratamento de computador offline ou agente inacessível.
"""
from __future__ import annotations

import os
import time
import unittest
from unittest.mock import MagicMock, patch

from pc_control.client import PcControlClient
from pc_control.dispatcher import PcControlDispatcher


class TestClientAndDispatcher(unittest.TestCase):
    """Bateria de testes para cliente e despachante do PC Control."""

    def setUp(self):
        self.mock_client = MagicMock(spec=PcControlClient)
        self.mock_client.enviar_comando.return_value = {
            "event_id": "ev-test-1",
            "session_id": "sess-test-1",
            "origem": "me6s",
            "computador_alvo": "fusion",
            "acao": "abrir_navegador",
            "estado": "concluido",
            "status": "concluido",
            "resultado": {"url": "about:blank"},
            "erro": None,
            "mensagem_amigavel": "Navegador padrão aberto no Fusion.",
            "timestamp": time.time(),
        }
        self.dispatcher = PcControlDispatcher(
            client=self.mock_client,
            dedup_window_s=3.5,
            confirmation_timeout_s=15.0,
        )

    def test_01_feature_flag_disabled_by_default(self):
        """Por padrão (JARVIS_PC_CONTROL_ENABLED=0), qualquer comando é rejeitado com status 'cancelado'."""
        with patch.dict(os.environ, {"JARVIS_PC_CONTROL_ENABLED": "0"}, clear=False):
            res = self.dispatcher.processar_comando_ou_confirmacao(
                acao="abrir_navegador",
                alvo="fusion",
                texto_falado="abrir navegador no fusion",
                session_id="sess-ff-1",
                event_id="ev-ff-1",
            )
            self.assertFalse(res.get("sucesso"))
            self.assertEqual(res.get("status"), "cancelado")
            self.assertEqual(res.get("erro"), "pc_control_desativado")
            self.assertIn("desativado por configuração", res.get("mensagem_amigavel", ""))
            self.mock_client.enviar_comando.assert_not_called()

    def test_02_feature_flag_enabled_allows_immediate_command(self):
        """Com JARVIS_PC_CONTROL_ENABLED=1, comando imediato é despachado ao cliente."""
        with patch.dict(os.environ, {"JARVIS_PC_CONTROL_ENABLED": "1"}, clear=False):
            res = self.dispatcher.processar_comando_ou_confirmacao(
                acao="abrir_navegador",
                alvo="fusion",
                texto_falado="abrir navegador no fusion",
                session_id="sess-ff-2",
                event_id="ev-ff-2",
            )
            self.assertTrue(res.get("sucesso"))
            self.assertEqual(res.get("status"), "concluido")
            self.mock_client.enviar_comando.assert_called_once()

    def test_03_close_browser_requires_confirmation(self):
        """Fechar navegador entra em confirmação pendente e só despacha após confirmação."""
        with patch.dict(os.environ, {"JARVIS_PC_CONTROL_ENABLED": "1"}, clear=False):
            # 1. Pedido inicial
            res1 = self.dispatcher.processar_comando_ou_confirmacao(
                acao="fechar_navegador",
                alvo="fusion",
                texto_falado="fechar navegador no fusion",
                session_id="sess-close-1",
                event_id="ev-close-1",
            )
            self.assertTrue(res1.get("sucesso"))
            self.assertEqual(res1.get("status"), "confirmacao_pendente")
            self.assertIn("Confirme fechar navegador", res1.get("mensagem_amigavel", ""))
            self.mock_client.enviar_comando.assert_not_called()

            # 2. Confirmação
            res2 = self.dispatcher.processar_comando_ou_confirmacao(
                acao="confirmar",
                alvo="fusion",
                texto_falado="confirmar",
                session_id="sess-close-1",
                event_id="ev-close-2",
            )
            self.assertTrue(res2.get("sucesso"))
            self.mock_client.enviar_comando.assert_called_once_with(
                alvo="fusion",
                acao="fechar_navegador",
                event_id="ev-close-2",
                session_id="sess-close-1",
                fonte="me6s",
                params=None,
            )

    def test_04_reboot_requires_confirmation(self):
        """Reiniciar entra em confirmação pendente e só despacha após confirmação explícita."""
        with patch.dict(os.environ, {"JARVIS_PC_CONTROL_ENABLED": "1"}, clear=False):
            # 1. Pedido
            res1 = self.dispatcher.processar_comando_ou_confirmacao(
                acao="reiniciar",
                alvo="ananuke",
                texto_falado="reiniciar ananuke",
                session_id="sess-reb-1",
                event_id="ev-reb-1",
            )
            self.assertTrue(res1.get("sucesso"))
            self.assertEqual(res1.get("status"), "confirmacao_pendente")
            self.assertIn("Confirme reinicialização", res1.get("mensagem_amigavel", ""))
            self.mock_client.enviar_comando.assert_not_called()

            # 2. Confirmação
            res2 = self.dispatcher.processar_comando_ou_confirmacao(
                acao="confirmar",
                alvo="ananuke",
                texto_falado="confirmar reinicio",
                session_id="sess-reb-1",
                event_id="ev-reb-2",
            )
            self.assertTrue(res2.get("sucesso"))
            self.mock_client.enviar_comando.assert_called_once()

    def test_05_rejection_of_divergent_session_confirmation(self):
        """Confirmação com session_id diferente é estritamente rejeitada."""
        with patch.dict(os.environ, {"JARVIS_PC_CONTROL_ENABLED": "1"}, clear=False):
            self.dispatcher.processar_comando_ou_confirmacao(
                acao="reiniciar",
                alvo="fusion",
                texto_falado="reiniciar fusion",
                session_id="sess-legitima",
                event_id="ev-reb-1",
            )

            # Confirmação vinda de outra sessão
            res_div = self.dispatcher.processar_comando_ou_confirmacao(
                acao="confirmar",
                alvo="fusion",
                texto_falado="confirmar",
                session_id="sess-invasora-divergente",
                event_id="ev-reb-2",
            )
            self.assertFalse(res_div.get("sucesso"))
            self.assertEqual(res_div.get("erro"), "session_id_divergente")
            self.mock_client.enviar_comando.assert_not_called()

    def test_06_delayed_confirmation_rejected_after_timeout(self):
        """Confirmação enviada após expiração do tempo limite é rejeitada."""
        with patch.dict(os.environ, {"JARVIS_PC_CONTROL_ENABLED": "1"}, clear=False):
            self.dispatcher.confirmation_timeout_s = 0.05
            self.dispatcher.processar_comando_ou_confirmacao(
                acao="reiniciar",
                alvo="fusion",
                texto_falado="reiniciar fusion",
                session_id="sess-time-1",
                event_id="ev-time-1",
            )
            time.sleep(0.08)

            res = self.dispatcher.processar_comando_ou_confirmacao(
                acao="confirmar",
                alvo="fusion",
                texto_falado="confirmar",
                session_id="sess-time-1",
                event_id="ev-time-2",
            )
            self.assertFalse(res.get("sucesso"))
            self.assertEqual(res.get("erro"), "confirmacao_expirada")
            self.mock_client.enviar_comando.assert_not_called()

    def test_07_cancellation_clears_pending_action(self):
        """Comando 'cancelar' cancela a confirmação pendente."""
        with patch.dict(os.environ, {"JARVIS_PC_CONTROL_ENABLED": "1"}, clear=False):
            self.dispatcher.processar_comando_ou_confirmacao(
                acao="reiniciar",
                alvo="fusion",
                texto_falado="reiniciar fusion",
                session_id="sess-canc-1",
                event_id="ev-canc-1",
            )
            res_canc = self.dispatcher.processar_comando_ou_confirmacao(
                acao="cancelar",
                alvo="fusion",
                texto_falado="cancelar",
                session_id="sess-canc-1",
                event_id="ev-canc-2",
            )
            self.assertTrue(res_canc.get("sucesso"))
            self.assertEqual(res_canc.get("status"), "cancelado")
            self.assertIsNone(self.dispatcher.pending_confirmation)
            self.mock_client.enviar_comando.assert_not_called()

    def test_08_action_idempotency_deduplication(self):
        """Comando repetido dentro da janela de 3.5s é deduplicado por idempotência."""
        with patch.dict(os.environ, {"JARVIS_PC_CONTROL_ENABLED": "1"}, clear=False):
            res1 = self.dispatcher.processar_comando_ou_confirmacao(
                acao="bloquear",
                alvo="fusion",
                texto_falado="bloquear fusion",
                session_id="sess-idemp-1",
                event_id="ev-idemp-1",
            )
            self.assertTrue(res1.get("sucesso"))
            self.assertEqual(self.mock_client.enviar_comando.call_count, 1)

            # 2a chamada idêntica logo em seguida: deduplicada
            res2 = self.dispatcher.processar_comando_ou_confirmacao(
                acao="bloquear",
                alvo="fusion",
                texto_falado="bloquear fusion",
                session_id="sess-idemp-2",
                event_id="ev-idemp-2",
            )
            self.assertTrue(res2.get("sucesso"))
            self.assertTrue(res2.get("deduplicado"))
            self.assertEqual(res2.get("status"), "concluido")
            self.assertEqual(self.mock_client.enviar_comando.call_count, 1)

    def test_09_client_handles_offline_host(self):
        """Valida que PcControlClient trata host offline com erro amigável sem travar."""
        # Usa porta inexistente/fechada no localhost para simular máquina inacessível
        real_client = PcControlClient(
            token="segredo-forte-valido-12345",
            timeout=0.2,
            url_overrides={"fusion": "http://127.0.0.1:59999"},
        )
        res = real_client.enviar_comando(
            alvo="fusion",
            acao="abrir_navegador",
            event_id="ev-offline-1",
            session_id="sess-off-1",
            fonte="me6s",
        )
        self.assertEqual(res.get("status"), "falha")
        self.assertIn("computador_offline_ou_inacessivel", res.get("erro", ""))
        self.assertIn("desligado ou com agente inacessível", res.get("mensagem_amigavel", ""))

    def test_10_orphan_confirmation_rejected(self):
        """Valida que confirmação órfã (sem ação pendente prévia) é rejeitada com erro específico."""
        with patch.dict(os.environ, {"JARVIS_PC_CONTROL_ENABLED": "1"}, clear=False):
            self.dispatcher.pending_confirmation = None
            res = self.dispatcher.processar_comando_ou_confirmacao(
                acao="confirmar",
                alvo="fusion",
                texto_falado="confirmar",
                session_id="sess-orphan-1",
                event_id="ev-orphan-1",
            )
            self.assertFalse(res.get("sucesso"))
            self.assertEqual(res.get("erro"), "nenhuma_confirmacao_pendente")
            self.assertIn("Nenhuma operação pendente", res.get("mensagem_amigavel", ""))
            self.mock_client.enviar_comando.assert_not_called()

    def test_11_target_divergent_confirmation_rejected(self):
        """Valida que confirmação com computador-alvo conflitante com o pendente é rejeitada."""
        with patch.dict(os.environ, {"JARVIS_PC_CONTROL_ENABLED": "1"}, clear=False):
            # Registra confirmação para Fusion
            self.dispatcher.processar_comando_ou_confirmacao(
                acao="fechar_navegador",
                alvo="fusion",
                texto_falado="fechar navegador no fusion",
                session_id="sess-match-1",
                event_id="ev-match-1",
            )
            # Tenta confirmar apontando para Ananuke
            res = self.dispatcher.processar_comando_ou_confirmacao(
                acao="confirmar",
                alvo="ananuke",
                texto_falado="confirmar ananuke",
                session_id="sess-match-1",
                event_id="ev-match-2",
            )
            self.assertFalse(res.get("sucesso"))
            self.assertEqual(res.get("erro"), "computador_alvo_divergente")
            self.mock_client.enviar_comando.assert_not_called()

    def test_12_action_divergent_confirmation_rejected(self):
        """Valida que envio de ação divergente durante confirmação pendente é bloqueado."""
        with patch.dict(os.environ, {"JARVIS_PC_CONTROL_ENABLED": "1"}, clear=False):
            self.dispatcher.processar_comando_ou_confirmacao(
                acao="fechar_navegador",
                alvo="fusion",
                texto_falado="fechar navegador no fusion",
                session_id="sess-act-1",
                event_id="ev-act-1",
            )
            # Tenta enviar outra ação de PC Control (ex: bloquear) em vez de confirmar/cancelar
            res = self.dispatcher.processar_comando_ou_confirmacao(
                acao="bloquear",
                alvo="fusion",
                texto_falado="bloquear fusion",
                session_id="sess-act-1",
                event_id="ev-act-2",
            )
            self.assertFalse(res.get("sucesso"))
            self.assertEqual(res.get("erro"), "acao_divergente")
            self.mock_client.enviar_comando.assert_not_called()

    def test_13_requires_confirmation_without_session_id_rejected(self):
        """Valida que ação destrutiva sem session_id é rejeitada em fail-closed."""
        with patch.dict(os.environ, {"JARVIS_PC_CONTROL_ENABLED": "1"}, clear=False):
            res = self.dispatcher.processar_comando_ou_confirmacao(
                acao="reiniciar",
                alvo="fusion",
                texto_falado="reiniciar fusion",
                session_id=None,
                event_id="ev-nosess-1",
            )
            self.assertFalse(res.get("sucesso"))
            self.assertEqual(res.get("erro"), "sessao_obrigatoria_para_confirmacao")
            self.assertIsNone(self.dispatcher.pending_confirmation)

    def test_14_insufficient_confirmation_clears_pending_state(self):
        """Valida que resposta inválida/insuficiente cancela e limpa a confirmação pendente."""
        with patch.dict(os.environ, {"JARVIS_PC_CONTROL_ENABLED": "1"}, clear=False):
            self.dispatcher.processar_comando_ou_confirmacao(
                acao="fechar_navegador",
                alvo="fusion",
                texto_falado="fechar navegador no fusion",
                session_id="sess-insuf-1",
                event_id="ev-insuf-1",
            )
            self.assertIsNotNone(self.dispatcher.pending_confirmation)

            # Usuário responde frase desconhecida/inválida
            res = self.dispatcher.processar_comando_ou_confirmacao(
                acao="confirmar",
                alvo="fusion",
                texto_falado="frase invalida qualquer",
                session_id="sess-insuf-1",
                event_id="ev-insuf-2",
            )
            self.assertFalse(res.get("sucesso"))
            self.assertEqual(res.get("erro"), "confirmacao_insuficiente")
            self.assertIsNone(self.dispatcher.pending_confirmation)


if __name__ == "__main__":
    unittest.main()
