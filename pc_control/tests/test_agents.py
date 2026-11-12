#!/usr/bin/env python3
"""Testes Unitários dos Agentes Locais (WindowsPcAgent e LinuxPcAgent).

Valida:
- Execução isolada dos 11 comandos para Fusion (Windows) em modo seguro.
- Execução isolada dos 11 comandos para Ananuke (Debian 13 / Xfce) em modo seguro.
- Idempotência estrita do comando 'silenciar_audio' (nunca desmuta em repetição).
- Limites seguros de volume (nunca ultrapassa 100% nem desce abaixo de 0%).
- Fechamento gracioso de navegadores sem SIGKILL ou /F.
- Simulação estrita de reinicialização (sem afetar a máquina real).
- Consulta correta de CPU, memória e uptime.
"""
from __future__ import annotations

import os
import subprocess
import sys
import unittest
from unittest.mock import MagicMock, patch

from pc_control.agents.linux_agent import LinuxPcAgent
from pc_control.agents.windows_agent import WindowsPcAgent


class TestWindowsPcAgent(unittest.TestCase):
    """Testes unitários para o agente Windows (Fusion)."""

    def setUp(self):
        self.agent = WindowsPcAgent(host="127.0.0.1", port=0, auth_token="test-token-win-12345", mock_mode=True)

    def tearDown(self):
        try:
            self.agent.server_close()
        except Exception:
            pass

    def test_01_all_11_actions_callable(self):
        """Valida que todas as 11 ações obrigatórias são despachadas com sucesso."""
        acoes = [
            "abrir_navegador",
            "abrir_youtube",
            "bloquear",
            "aumentar_volume",
            "diminuir_volume",
            "silenciar_audio",
            "status_computador",
            "abrir_terminal",
            "fechar_navegador",
            "reiniciar",
            "mostrar_area_de_trabalho",
        ]
        for acao in acoes:
            ok, res, erro, msg = self.agent.executar_acao(acao, {}, "ev-win-test", "sess-win-1")
            self.assertTrue(ok, f"Falha na ação {acao}: {erro}")
            self.assertIsNone(erro)
            self.assertIn("Fusion", msg)

    def test_02_volume_clamping_limits(self):
        """Garante que o volume respeita estritamente os limites de 0% a 100%."""
        self.agent._sim_volume = 0.95
        ok, res, _, _ = self.agent.executar_acao("aumentar_volume", {"passo": 0.10}, "ev-vol-up")
        self.assertTrue(ok)
        self.assertEqual(res.get("volume_pct"), 100, "Volume não deveria passar de 100%")

        self.agent._sim_volume = 0.05
        ok, res, _, _ = self.agent.executar_acao("diminuir_volume", {"passo": 0.10}, "ev-vol-down")
        self.assertTrue(ok)
        self.assertEqual(res.get("volume_pct"), 0, "Volume não deveria descer de 0%")

    def test_03_mute_idempotency_never_unmutes(self):
        """O comando 'silenciar áudio' deve ativar o mudo e NUNCA alterná-lo em chamadas repetidas."""
        self.agent._sim_muted = False
        ok1, res1, _, _ = self.agent.executar_acao("silenciar_audio", {}, "ev-mute-1")
        self.assertTrue(ok1)
        self.assertTrue(self.agent._sim_muted)
        self.assertTrue(res1.get("mudo"))

        # Segunda chamada: deve PERMANECER mudo
        ok2, res2, _, _ = self.agent.executar_acao("silenciar_audio", {}, "ev-mute-2")
        self.assertTrue(ok2)
        self.assertTrue(self.agent._sim_muted)
        self.assertTrue(res2.get("mudo"))

    def test_04_status_telemetry(self):
        """Valida que status do computador retorna métricas de CPU, memória e uptime."""
        ok, res, _, msg = self.agent.executar_acao("status_computador", {}, "ev-stat-1")
        self.assertTrue(ok)
        self.assertIn("cpu_percent", res)
        self.assertIn("memoria_percent", res)
        self.assertIn("uptime", res)
        self.assertIn("CPU", msg)
        self.assertIn("RAM", msg)

    def test_05_reboot_simulated(self):
        """Garante que o reboot em modo de teste opera em simulação sem afetar o host."""
        ok, res, _, msg = self.agent.executar_acao("reiniciar", {}, "ev-reboot-1")
        self.assertTrue(ok)
        self.assertTrue(res.get("simulado"))
        self.assertTrue(res.get("reiniciando"))

    def test_06_fechar_navegador_falha_se_nenhum_aberto(self):
        """Valida que fechar_navegador retorna sucesso=False quando nenhum navegador está ativo."""
        ok, res, erro, msg = self.agent.executar_acao("fechar_navegador", {"simular_nenhum": True}, "ev-close-none")
        self.assertFalse(ok)
        self.assertEqual(erro, "nenhum_navegador_encontrado")
        self.assertEqual(res.get("navegadores_fechados"), [])
        self.assertIn("Nenhum navegador", msg)

    def test_07_fechar_navegador_falha_se_nao_confirmado(self):
        """Valida que fechar_navegador retorna sucesso=False quando fechamento não é confirmado."""
        ok, res, erro, msg = self.agent.executar_acao("fechar_navegador", {"simular_nao_confirmado": True}, "ev-close-unconfirmed")
        self.assertFalse(ok)
        self.assertEqual(erro, "fechamento_nao_confirmado")
        self.assertEqual(res.get("navegadores_fechados"), [])
        self.assertIn("foi fechado ou confirmado", msg)


class TestLinuxPcAgent(unittest.TestCase):
    """Testes unitários para o agente Linux (Ananuke - Debian 13 / Xfce)."""

    def setUp(self):
        self.agent = LinuxPcAgent(host="127.0.0.1", port=0, auth_token="test-token-lin-12345", mock_mode=True)

    def tearDown(self):
        try:
            self.agent.server_close()
        except Exception:
            pass

    def test_01_all_11_actions_callable(self):
        """Valida que todas as 11 ações obrigatórias são despachadas com sucesso."""
        acoes = [
            "abrir_navegador",
            "abrir_youtube",
            "bloquear",
            "aumentar_volume",
            "diminuir_volume",
            "silenciar_audio",
            "status_computador",
            "abrir_terminal",
            "fechar_navegador",
            "reiniciar",
            "mostrar_area_de_trabalho",
        ]
        for acao in acoes:
            ok, res, erro, msg = self.agent.executar_acao(acao, {}, "ev-lin-test", "sess-lin-1")
            self.assertTrue(ok, f"Falha na ação {acao}: {erro}")
            self.assertIsNone(erro)
            self.assertIn("Ananuke", msg)

    def test_02_volume_clamping_limits(self):
        """Garante que o volume no Linux respeita estritamente [0%, 100%]."""
        self.agent._sim_volume = 0.95
        ok, res, _, _ = self.agent.executar_acao("aumentar_volume", {"passo": 0.10}, "ev-vol-up")
        self.assertTrue(ok)
        self.assertEqual(res.get("volume_pct"), 100)

        self.agent._sim_volume = 0.05
        ok, res, _, _ = self.agent.executar_acao("diminuir_volume", {"passo": 0.10}, "ev-vol-down")
        self.assertTrue(ok)
        self.assertEqual(res.get("volume_pct"), 0)

    def test_03_mute_idempotency_never_unmutes(self):
        """Garante que silenciar áudio no Linux é estritamente idempotente."""
        self.agent._sim_muted = False
        ok1, res1, _, _ = self.agent.executar_acao("silenciar_audio", {}, "ev-mute-1")
        self.assertTrue(ok1)
        self.assertTrue(self.agent._sim_muted)
        self.assertTrue(res1.get("mudo"))

        # Repetição não deve desmutar
        ok2, res2, _, _ = self.agent.executar_acao("silenciar_audio", {}, "ev-mute-2")
        self.assertTrue(ok2)
        self.assertTrue(self.agent._sim_muted)
        self.assertTrue(res2.get("mudo"))

    def test_04_proc_telemetry_metrics(self):
        """Valida a leitura real das métricas do Linux (/proc/stat, /proc/meminfo, /proc/uptime)."""
        cpu, mem, up = self.agent._ler_metricas_linux()
        self.assertIsInstance(cpu, float)
        self.assertIsInstance(mem, float)
        self.assertIsInstance(up, str)
        self.assertGreaterEqual(cpu, 0.0)
        self.assertLessEqual(cpu, 100.0)
        self.assertGreaterEqual(mem, 0.0)
        self.assertLessEqual(mem, 100.0)

    def test_05_reboot_simulated(self):
        """Garante que o reboot em modo seguro NUNCA executa systemctl reboot no host."""
        ok, res, _, msg = self.agent.executar_acao("reiniciar", {}, "ev-reboot-1")
        self.assertTrue(ok)
        self.assertTrue(res.get("simulado"))
        self.assertTrue(res.get("reiniciando"))

    def test_06_fechar_navegador_falha_se_nenhum_aberto(self):
        """Valida que fechar_navegador retorna sucesso=False quando nenhum navegador está ativo."""
        ok, res, erro, msg = self.agent.executar_acao("fechar_navegador", {"simular_nenhum": True}, "ev-close-none-lin")
        self.assertFalse(ok)
        self.assertEqual(erro, "nenhum_navegador_encontrado")
        self.assertEqual(res.get("processos_fechados"), [])
        self.assertIn("Nenhum navegador", msg)

    def test_07_fechar_navegador_falha_se_nao_confirmado(self):
        """Valida que fechar_navegador retorna sucesso=False quando fechamento não é confirmado no Linux."""
        ok, res, erro, msg = self.agent.executar_acao("fechar_navegador", {"simular_nao_confirmado": True}, "ev-close-unconfirmed-lin")
        self.assertFalse(ok)
        self.assertEqual(erro, "fechamento_nao_confirmado")
        self.assertEqual(res.get("processos_fechados"), [])
        self.assertIn("foi fechado ou confirmado", msg)

    def test_08_fechar_navegador_falha_segura_quando_wmctrl_indisponivel_sem_pkill(self):
        """Valida que quando wmctrl não está disponível, fechar_navegador falha com erro seguro sem invocar pkill."""
        # 1. Modo simulado com parâmetro explícito
        ok, res, erro, msg = self.agent.executar_acao(
            "fechar_navegador", {"simular_sem_wmctrl": True}, "ev-no-wmctrl-sim"
        )
        self.assertFalse(ok)
        self.assertEqual(erro, "fechamento_gracioso_indisponivel")
        self.assertIn("wmctrl", msg)

        # 2. Modo não-simulado com wmctrl ausente e processo de navegador ativo
        self.agent.mock_mode = False
        try:
            with patch("shutil.which", return_value=None):
                # Simula pgrep encontrando firefox ativo
                def fake_run(cmd, *args, **kwargs):
                    if cmd[0] == "pgrep" and "firefox" in cmd:
                        return subprocess.CompletedProcess(cmd, returncode=0, stdout="12345\n", stderr="")
                    return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr="")

                with patch("subprocess.run", side_effect=fake_run) as mock_run:
                    ok_real, res_real, erro_real, msg_real = self.agent.executar_acao(
                        "fechar_navegador", {}, "ev-no-wmctrl-real"
                    )
                    self.assertFalse(ok_real)
                    self.assertEqual(erro_real, "fechamento_gracioso_indisponivel")
                    self.assertIn("wmctrl", msg_real)

                    # Garante formalmente que NENHUMA chamada a pkill foi realizada
                    for chamada in mock_run.call_args_list:
                        cmd_exec = chamada[0][0]
                        self.assertNotEqual(cmd_exec[0], "pkill", "pkill jamais deve ser chamado quando wmctrl está ausente")
        finally:
            self.agent.mock_mode = True


class TestAgentStartupCommands(unittest.TestCase):
    """Valida a inicialização correta dos agentes via python -m e scripts diretos."""

    @classmethod
    def setUpClass(cls):
        # Determina a raiz do repositório jarvis-voice
        cls.repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

    def test_01_linux_agent_module_startup(self):
        """Valida inicialização recomendada do agente Linux via python3 -m pc_control.agents.linux_agent."""
        cmd = [sys.executable, "-m", "pc_control.agents.linux_agent", "--help"]
        res = subprocess.run(cmd, cwd=self.repo_root, capture_output=True, text=True, timeout=5)
        self.assertEqual(res.returncode, 0, f"Falha no comando: {res.stderr}")
        self.assertIn("Agente Local do Jarvis PC Control para Linux", res.stdout)
        self.assertNotIn("RuntimeWarning", res.stderr)

    def test_02_windows_agent_module_startup(self):
        """Valida inicialização recomendada do agente Windows via python -m pc_control.agents.windows_agent."""
        cmd = [sys.executable, "-m", "pc_control.agents.windows_agent", "--help"]
        res = subprocess.run(cmd, cwd=self.repo_root, capture_output=True, text=True, timeout=5)
        self.assertEqual(res.returncode, 0, f"Falha no comando: {res.stderr}")
        self.assertIn("Agente Local do Jarvis PC Control para Windows", res.stdout)
        self.assertNotIn("RuntimeWarning", res.stderr)

    def test_03_linux_agent_standalone_script_startup(self):
        """Valida fallback de sys.path quando agente Linux é executado diretamente como script."""
        script_path = os.path.join(self.repo_root, "pc_control", "agents", "linux_agent.py")
        cmd = [sys.executable, script_path, "--help"]
        res = subprocess.run(cmd, cwd=self.repo_root, capture_output=True, text=True, timeout=5)
        self.assertEqual(res.returncode, 0, f"Falha no script direto: {res.stderr}")
        self.assertIn("Agente Local do Jarvis PC Control para Linux", res.stdout)

    def test_04_windows_agent_standalone_script_startup(self):
        """Valida fallback de sys.path quando agente Windows é executado diretamente como script."""
        script_path = os.path.join(self.repo_root, "pc_control", "agents", "windows_agent.py")
        cmd = [sys.executable, script_path, "--help"]
        res = subprocess.run(cmd, cwd=self.repo_root, capture_output=True, text=True, timeout=5)
        self.assertEqual(res.returncode, 0, f"Falha no script direto: {res.stderr}")
        self.assertIn("Agente Local do Jarvis PC Control para Windows", res.stdout)


if __name__ == "__main__":
    unittest.main()
