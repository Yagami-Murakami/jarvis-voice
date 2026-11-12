#!/usr/bin/env python3
"""Testes automatizados para o helper scripts/testar_agente_local.py.

Comprova:
- Validação estrita de credenciais (rejeição de tokens ausentes ou fracos).
- Avaliação correta de status == "concluido" (não utilizando o campo legado "sucesso").
- Contagem precisa de sucessos e falhas na suíte dos 8 comandos não destrutivos.
- Comportamento de saída e avaliação do modo isolado --bloquear.
"""
from __future__ import annotations

import io
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import scripts.testar_agente_local as local_runner  # noqa: E402


class TestLocalAgentRunner(unittest.TestCase):
    """Testa o script auxiliar de homologação local."""

    def setUp(self) -> None:
        self.strong_token = "0123456789abcdef0123456789abcdef"

    def test_01_missing_token_returns_error_code_1(self) -> None:
        """Sem token configurado no ambiente, o helper deve recusar e retornar código 1."""
        with patch.dict(os.environ, {}, clear=True):
            with patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
                with patch("sys.argv", ["testar_agente_local.py", "--alvo", "ananuke"]):
                    ret = local_runner.main()
                    self.assertEqual(ret, 1)
                    self.assertIn("ERRO: Variável JARVIS_PC_ANANUKE_TOKEN", mock_stderr.getvalue())

    def test_02_weak_token_returns_error_code_1(self) -> None:
        """Token menor que 16 caracteres deve ser recusado fail-closed."""
        with patch.dict(os.environ, {"JARVIS_PC_AGENT_TOKEN": "token_fraco"}, clear=True):
            with patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
                with patch("sys.argv", ["testar_agente_local.py", "--alvo", "fusion"]):
                    ret = local_runner.main()
                    self.assertEqual(ret, 1)
                    self.assertIn("ausente ou fraca", mock_stderr.getvalue())

    @patch("scripts.testar_agente_local.time.sleep", return_value=None)
    @patch("scripts.testar_agente_local.PcControlClient")
    def test_03_all_commands_succeed_with_status_concluido(self, mock_client_cls: MagicMock, mock_sleep: MagicMock) -> None:
        """Quando todas as respostas retornam status='concluido', contabiliza 8 sucessos e retorna 0."""
        mock_instance = MagicMock()
        mock_client_cls.return_value = mock_instance
        # Retorna dicionário sem 'sucesso', apenas com 'status': 'concluido'
        mock_instance.enviar_comando.return_value = {
            "status": "concluido",
            "mensagem_amigavel": "Ação executada com sucesso.",
        }

        with patch.dict(os.environ, {"JARVIS_PC_ANANUKE_TOKEN": self.strong_token}):
            with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
                with patch("sys.argv", ["testar_agente_local.py", "--alvo", "ananuke"]):
                    ret = local_runner.main()
                    self.assertEqual(ret, 0)
                    out = mock_stdout.getvalue()
                    self.assertIn("Resumo: 8 aprovados, 0 falhas de 8 comandos.", out)
                    self.assertEqual(mock_instance.enviar_comando.call_count, 8)

    @patch("scripts.testar_agente_local.time.sleep", return_value=None)
    @patch("scripts.testar_agente_local.PcControlClient")
    def test_04_partial_failures_counted_accurately_and_returns_1(self, mock_client_cls: MagicMock, mock_sleep: MagicMock) -> None:
        """Falhas com status != 'concluido' são contabilizadas e provocam saída código 1."""
        mock_instance = MagicMock()
        mock_client_cls.return_value = mock_instance

        # Alterna entre sucesso e falha (4 sucessos, 4 falhas)
        respostas = [
            {"status": "concluido", "mensagem_amigavel": "OK 1"},
            {"status": "falha", "erro": "timeout", "mensagem_amigavel": "Erro 2"},
            {"status": "concluido", "mensagem_amigavel": "OK 3"},
            {"status": "falha", "erro": "recusado", "mensagem_amigavel": "Erro 4"},
            {"status": "concluido", "mensagem_amigavel": "OK 5"},
            {"status": "falha", "erro": "erro_interno", "mensagem_amigavel": "Erro 6"},
            {"status": "concluido", "mensagem_amigavel": "OK 7"},
            {"status": "falha", "erro": "desconhecido", "mensagem_amigavel": "Erro 8"},
        ]
        mock_instance.enviar_comando.side_effect = respostas

        with patch.dict(os.environ, {"JARVIS_PC_FUSION_TOKEN": self.strong_token}):
            with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
                with patch("sys.argv", ["testar_agente_local.py", "--alvo", "fusion"]):
                    ret = local_runner.main()
                    self.assertEqual(ret, 1)
                    out = mock_stdout.getvalue()
                    self.assertIn("Resumo: 4 aprovados, 4 falhas de 8 comandos.", out)

    @patch("scripts.testar_agente_local.PcControlClient")
    def test_05_bloquear_evaluates_status_concluido(self, mock_client_cls: MagicMock) -> None:
        """Modo --bloquear avalia especificamente status == 'concluido'."""
        mock_instance = MagicMock()
        mock_client_cls.return_value = mock_instance

        # Cenário A: status == 'concluido'
        mock_instance.enviar_comando.return_value = {
            "status": "concluido",
            "mensagem_amigavel": "Computador bloqueado.",
        }
        with patch.dict(os.environ, {"JARVIS_PC_AGENT_TOKEN": self.strong_token}):
            with patch("sys.argv", ["testar_agente_local.py", "--alvo", "ananuke", "--bloquear"]):
                ret = local_runner.main()
                self.assertEqual(ret, 0)

        # Cenário B: status == 'falha'
        mock_instance.enviar_comando.return_value = {
            "status": "falha",
            "erro": "erro_bloqueio",
            "mensagem_amigavel": "Falha ao bloquear.",
        }
        with patch.dict(os.environ, {"JARVIS_PC_AGENT_TOKEN": self.strong_token}):
            with patch("sys.argv", ["testar_agente_local.py", "--alvo", "ananuke", "--bloquear"]):
                ret = local_runner.main()
                self.assertEqual(ret, 1)


if __name__ == "__main__":
    unittest.main()
