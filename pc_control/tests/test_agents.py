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
import tempfile
import time
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

    def test_09_fechar_navegador_falha_segura_quando_wmctrl_falha_na_sessao(self):
        """Valida que quando wmctrl existe mas falha ao comunicar com X11 (código != 0), fechar_navegador falha com erro seguro."""
        self.agent.mock_mode = False
        try:
            with patch("shutil.which", side_effect=lambda x: "/usr/bin/wmctrl" if x == "wmctrl" else None):
                def fake_run(cmd, *args, **kwargs):
                    if cmd[0] == "wmctrl":
                        return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr="Cannot open display: :0")
                    if cmd[0] == "pgrep":
                        return subprocess.CompletedProcess(cmd, returncode=0, stdout="12345\n", stderr="")
                    return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr="")

                with patch("subprocess.run", side_effect=fake_run):
                    ok, res, erro, msg = self.agent.executar_acao("fechar_navegador", {}, "ev-wmctrl-display-err")
                    self.assertFalse(ok)
                    self.assertEqual(erro, "fechamento_gracioso_indisponivel")
                    self.assertIn("servidor gráfico", msg)
        finally:
            self.agent.mock_mode = True

    def test_10_desktop_auto_detection(self):
        """Valida detecção automática do desktop (GNOME e Xfce) via variáveis de ambiente, processos e overrides."""
        # 1. Detecção GNOME via XDG_CURRENT_DESKTOP
        with patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "GNOME", "DESKTOP_SESSION": "", "GDMSESSION": ""}, clear=True):
            self.assertEqual(self.agent._detectar_ambiente_desktop(), "gnome")

        # 2. Detecção GNOME via DESKTOP_SESSION (gnome-xorg)
        with patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "", "DESKTOP_SESSION": "gnome-xorg", "GDMSESSION": ""}, clear=True):
            self.assertEqual(self.agent._detectar_ambiente_desktop(), "gnome")

        # 3. Detecção GNOME via processo ativo gnome-shell
        with patch.dict(os.environ, {}, clear=True):
            def fake_pgrep_gnome(cmd, *args, **kwargs):
                if cmd[0] == "pgrep" and "gnome-shell" in cmd:
                    return subprocess.CompletedProcess(cmd, returncode=0, stdout="2690\n")
                return subprocess.CompletedProcess(cmd, returncode=1, stdout="")

            with patch("subprocess.run", side_effect=fake_pgrep_gnome):
                self.assertEqual(self.agent._detectar_ambiente_desktop(), "gnome")

        # 4. Detecção Xfce via XDG_CURRENT_DESKTOP
        with patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "XFCE", "DESKTOP_SESSION": "", "GDMSESSION": ""}, clear=True):
            self.assertEqual(self.agent._detectar_ambiente_desktop(), "xfce")

        # 5. Detecção Xfce via processo ativo xfce4-session
        with patch.dict(os.environ, {}, clear=True):
            def fake_pgrep_xfce(cmd, *args, **kwargs):
                if cmd[0] == "pgrep" and "xfce4-session" in cmd:
                    return subprocess.CompletedProcess(cmd, returncode=0, stdout="1122\n")
                return subprocess.CompletedProcess(cmd, returncode=1, stdout="")

            with patch("subprocess.run", side_effect=fake_pgrep_xfce):
                self.assertEqual(self.agent._detectar_ambiente_desktop(), "xfce")

        # 6. Override explícito via JARVIS_DESKTOP_ENV
        with patch.dict(os.environ, {"JARVIS_DESKTOP_ENV": "xfce", "XDG_CURRENT_DESKTOP": "GNOME"}, clear=True):
            self.assertEqual(self.agent._detectar_ambiente_desktop(), "xfce")

        with patch.dict(os.environ, {"JARVIS_DESKTOP_ENV": "gnome", "XDG_CURRENT_DESKTOP": "XFCE"}, clear=True):
            self.assertEqual(self.agent._detectar_ambiente_desktop(), "gnome")

        # 7. Ambiente desconhecido quando nada é localizado
        with patch.dict(os.environ, {}, clear=True):
            with patch("subprocess.run", return_value=subprocess.CompletedProcess([], returncode=1, stdout="")):
                with patch("shutil.which", return_value=None):
                    self.assertEqual(self.agent._detectar_ambiente_desktop(), "desconhecido")

        # 8. Inicialização com desktop_env='auto' resolve para o ambiente detectado (não para 'auto')
        with patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "GNOME"}, clear=True):
            ag_auto = LinuxPcAgent(host="127.0.0.1", port=0, auth_token="test-token-lin-12345", mock_mode=True, desktop_env="auto")
            self.assertEqual(ag_auto.desktop_env, "gnome")
            ag_auto.server_close()

        # 9. Inicialização com desktop_env explícito ('xfce' ou 'gnome')
        ag_xfce = LinuxPcAgent(host="127.0.0.1", port=0, auth_token="test-token-lin-12345", mock_mode=True, desktop_env="xfce")
        self.assertEqual(ag_xfce.desktop_env, "xfce")
        ag_xfce.server_close()

    def test_11_gnome_terminal_prioritized(self):
        """Valida que no GNOME o terminal gnome-terminal é estritamente priorizado."""
        # 1. Modo simulado
        self.agent.desktop_env = "gnome"
        ok, res, erro, msg = self.agent.executar_acao("abrir_terminal", {}, "ev-term-gnome-sim")
        self.assertTrue(ok)
        self.assertEqual(res.get("terminal"), "gnome-terminal")
        self.assertEqual(res.get("desktop"), "gnome")

        # 2. Modo real com gnome-terminal presente
        self.agent.mock_mode = False
        try:
            with patch("shutil.which", side_effect=lambda x: f"/usr/bin/{x}" if x in ("gnome-terminal", "xfce4-terminal") else None):
                with patch("subprocess.Popen") as mock_popen:
                    ok_real, res_real, erro_real, msg_real = self.agent.executar_acao("abrir_terminal", {}, "ev-term-gnome-real")
                    self.assertTrue(ok_real)
                    self.assertEqual(res_real.get("terminal"), "gnome-terminal")
                    mock_popen.assert_called_once()
                    self.assertEqual(mock_popen.call_args[0][0], ["gnome-terminal"])

            # 3. Modo real: fallback para x-terminal-emulator se gnome-terminal ausente
            with patch("shutil.which", side_effect=lambda x: f"/usr/bin/{x}" if x == "x-terminal-emulator" else None):
                with patch("subprocess.Popen") as mock_popen:
                    ok_fb, res_fb, erro_fb, msg_fb = self.agent.executar_acao("abrir_terminal", {}, "ev-term-gnome-fb")
                    self.assertTrue(ok_fb)
                    self.assertEqual(res_fb.get("terminal"), "x-terminal-emulator")
                    mock_popen.assert_called_once()
                    self.assertEqual(mock_popen.call_args[0][0], ["x-terminal-emulator"])

            # 4. Modo real: falha quando nenhum terminal é encontrado
            with patch("shutil.which", return_value=None):
                ok_none, res_none, erro_none, msg_none = self.agent.executar_acao("abrir_terminal", {}, "ev-term-gnome-none")
                self.assertFalse(ok_none)
                self.assertEqual(erro_none, "terminal_nao_encontrado")
        finally:
            self.agent.mock_mode = True

    def test_12_xfce_terminal_prioritized(self):
        """Valida que no Xfce o terminal xfce4-terminal é estritamente priorizado."""
        # 1. Modo simulado
        self.agent.desktop_env = "xfce"
        ok, res, erro, msg = self.agent.executar_acao("abrir_terminal", {}, "ev-term-xfce-sim")
        self.assertTrue(ok)
        self.assertEqual(res.get("terminal"), "xfce4-terminal")
        self.assertEqual(res.get("desktop"), "xfce")

        # 2. Modo real com xfce4-terminal presente
        self.agent.mock_mode = False
        try:
            with patch("shutil.which", side_effect=lambda x: f"/usr/bin/{x}" if x in ("xfce4-terminal", "gnome-terminal") else None):
                with patch("subprocess.Popen") as mock_popen:
                    ok_real, res_real, erro_real, msg_real = self.agent.executar_acao("abrir_terminal", {}, "ev-term-xfce-real")
                    self.assertTrue(ok_real)
                    self.assertEqual(res_real.get("terminal"), "xfce4-terminal")
                    mock_popen.assert_called_once()
                    self.assertEqual(mock_popen.call_args[0][0], ["xfce4-terminal"])

            # 3. Modo real: fallback para x-terminal-emulator se xfce4-terminal ausente
            with patch("shutil.which", side_effect=lambda x: f"/usr/bin/{x}" if x == "x-terminal-emulator" else None):
                with patch("subprocess.Popen") as mock_popen:
                    ok_fb, res_fb, erro_fb, msg_fb = self.agent.executar_acao("abrir_terminal", {}, "ev-term-xfce-fb")
                    self.assertTrue(ok_fb)
                    self.assertEqual(res_fb.get("terminal"), "x-terminal-emulator")
                    mock_popen.assert_called_once()
                    self.assertEqual(mock_popen.call_args[0][0], ["x-terminal-emulator"])

            # 4. Modo real: falha quando nenhum terminal é encontrado
            with patch("shutil.which", return_value=None):
                ok_none, res_none, erro_none, msg_none = self.agent.executar_acao("abrir_terminal", {}, "ev-term-xfce-none")
                self.assertFalse(ok_none)
                self.assertEqual(erro_none, "terminal_nao_encontrado")
        finally:
            self.agent.mock_mode = True

    def test_13_gnome_session_lock_mechanisms(self):
        """Valida que no GNOME os mecanismos confiáveis de bloqueio (dbus-send, loginctl, etc.) são invocados."""
        self.agent.desktop_env = "gnome"
        self.agent.mock_mode = False
        try:
            # 1. Prioridade dbus-send (org.gnome.ScreenSaver)
            with patch("shutil.which", side_effect=lambda x: f"/usr/bin/{x}" if x in ("dbus-send", "loginctl") else None):
                with patch("subprocess.Popen") as mock_popen:
                    ok, res, erro, msg = self.agent.executar_acao("bloquear", {}, "ev-lock-gnome-dbus")
                    self.assertTrue(ok)
                    mock_popen.assert_called_once()
                    chamada_cmd = mock_popen.call_args[0][0]
                    self.assertEqual(chamada_cmd[0], "dbus-send")
                    self.assertIn("org.gnome.ScreenSaver", " ".join(chamada_cmd))

            # 2. Fallback loginctl lock-session quando dbus-send ausente
            with patch("shutil.which", side_effect=lambda x: f"/usr/bin/{x}" if x == "loginctl" else None):
                with patch("subprocess.Popen") as mock_popen:
                    ok_login, res_login, erro_login, msg_login = self.agent.executar_acao("bloquear", {}, "ev-lock-gnome-loginctl")
                    self.assertTrue(ok_login)
                    mock_popen.assert_called_once()
                    self.assertEqual(mock_popen.call_args[0][0], ["loginctl", "lock-session"])

            # 3. Fallback gnome-screensaver-command -l
            with patch("shutil.which", side_effect=lambda x: f"/usr/bin/{x}" if x == "gnome-screensaver-command" else None):
                with patch("subprocess.Popen") as mock_popen:
                    ok_gsc, res_gsc, erro_gsc, msg_gsc = self.agent.executar_acao("bloquear", {}, "ev-lock-gnome-gsc")
                    self.assertTrue(ok_gsc)
                    mock_popen.assert_called_once()
                    self.assertEqual(mock_popen.call_args[0][0], ["gnome-screensaver-command", "-l"])

            # 4. Falha segura se nenhuma ferramenta de bloqueio existir
            with patch("shutil.which", return_value=None):
                ok_none, res_none, erro_none, msg_none = self.agent.executar_acao("bloquear", {}, "ev-lock-none")
                self.assertFalse(ok_none)
                self.assertEqual(erro_none, "bloqueador_nao_encontrado")
        finally:
            self.agent.mock_mode = True

    def test_14_xfce_session_lock_mechanisms(self):
        """Valida que no Xfce os mecanismos nativos de bloqueio (xflock4, xfce4-screensaver-command) são invocados."""
        self.agent.desktop_env = "xfce"
        self.agent.mock_mode = False
        try:
            # 1. Prioridade xflock4
            with patch("shutil.which", side_effect=lambda x: f"/usr/bin/{x}" if x in ("xflock4", "loginctl") else None):
                with patch("subprocess.Popen") as mock_popen:
                    ok, res, erro, msg = self.agent.executar_acao("bloquear", {}, "ev-lock-xfce-xflock4")
                    self.assertTrue(ok)
                    mock_popen.assert_called_once()
                    self.assertEqual(mock_popen.call_args[0][0], ["xflock4"])

            # 2. Fallback xfce4-screensaver-command -l
            with patch("shutil.which", side_effect=lambda x: f"/usr/bin/{x}" if x == "xfce4-screensaver-command" else None):
                with patch("subprocess.Popen") as mock_popen:
                    ok_xf, res_xf, erro_xf, msg_xf = self.agent.executar_acao("bloquear", {}, "ev-lock-xfce-screensaver")
                    self.assertTrue(ok_xf)
                    mock_popen.assert_called_once()
                    self.assertEqual(mock_popen.call_args[0][0], ["xfce4-screensaver-command", "-l"])

            # 3. Fallback loginctl lock-session
            with patch("shutil.which", side_effect=lambda x: f"/usr/bin/{x}" if x == "loginctl" else None):
                with patch("subprocess.Popen") as mock_popen:
                    ok_login, res_login, erro_login, msg_login = self.agent.executar_acao("bloquear", {}, "ev-lock-xfce-loginctl")
                    self.assertTrue(ok_login)
                    mock_popen.assert_called_once()
                    self.assertEqual(mock_popen.call_args[0][0], ["loginctl", "lock-session"])

            # 4. Fallback xdg-screensaver lock
            with patch("shutil.which", side_effect=lambda x: f"/usr/bin/{x}" if x == "xdg-screensaver" else None):
                with patch("subprocess.Popen") as mock_popen:
                    ok_xdg, res_xdg, erro_xdg, msg_xdg = self.agent.executar_acao("bloquear", {}, "ev-lock-xfce-xdg")
                    self.assertTrue(ok_xdg)
                    mock_popen.assert_called_once()
                    self.assertEqual(mock_popen.call_args[0][0], ["xdg-screensaver", "lock"])

            # 5. Falha segura se nenhuma ferramenta de bloqueio existir
            with patch("shutil.which", return_value=None):
                ok_none, res_none, erro_none, msg_none = self.agent.executar_acao("bloquear", {}, "ev-lock-xfce-none")
                self.assertFalse(ok_none)
                self.assertEqual(erro_none, "bloqueador_nao_encontrado")
        finally:
            self.agent.mock_mode = True

    def test_15_mostrar_area_de_trabalho_gnome_x11(self):
        """Valida execução de mostrar área de trabalho no GNOME Shell sobre X11 (wmctrl, xdotool e fallback libX11)."""
        self.agent.desktop_env = "gnome"
        self.agent.mock_mode = False
        try:
            # 1. Execução via wmctrl -k on
            with patch("shutil.which", side_effect=lambda x: f"/usr/bin/{x}" if x == "wmctrl" else None):
                with patch("subprocess.run") as mock_run:
                    mock_run.return_value = subprocess.CompletedProcess(["wmctrl", "-k", "on"], returncode=0)
                    ok, res, erro, msg = self.agent.executar_acao("mostrar_area_de_trabalho", {}, "ev-desk-wmctrl")
                    self.assertTrue(ok)
                    self.assertTrue(res.get("area_de_trabalho"))
                    mock_run.assert_called_once()
                    self.assertEqual(mock_run.call_args[0][0], ["wmctrl", "-k", "on"])

            # 2. Fallback para xdotool quando wmctrl falha
            with patch("shutil.which", side_effect=lambda x: f"/usr/bin/{x}" if x in ("wmctrl", "xdotool") else None):
                def fake_run_fail_wmctrl(cmd, *args, **kwargs):
                    if cmd[0] == "wmctrl":
                        raise subprocess.SubprocessError("wmctrl failed")
                    if cmd[0] == "xdotool":
                        return subprocess.CompletedProcess(cmd, returncode=0)
                    return subprocess.CompletedProcess(cmd, returncode=1)

                with patch("subprocess.run", side_effect=fake_run_fail_wmctrl) as mock_run:
                    ok_xd, res_xd, erro_xd, msg_xd = self.agent.executar_acao("mostrar_area_de_trabalho", {}, "ev-desk-xdotool")
                    self.assertTrue(ok_xd)

            # 3. Fallback nativo libX11 quando wmctrl e xdotool estão ausentes
            with patch("shutil.which", return_value=None):
                with patch.object(self.agent, "_mostrar_desktop_x11", return_value=True) as mock_x11:
                    ok_x11, res_x11, erro_x11, msg_x11 = self.agent.executar_acao("mostrar_area_de_trabalho", {}, "ev-desk-x11")
                    self.assertTrue(ok_x11)
                    mock_x11.assert_called_once()

            # 4. Falha segura se todas as ferramentas falharem
            with patch("shutil.which", return_value=None):
                with patch.object(self.agent, "_mostrar_desktop_x11", return_value=False):
                    ok_fail, res_fail, erro_fail, msg_fail = self.agent.executar_acao("mostrar_area_de_trabalho", {}, "ev-desk-fail")
                    self.assertFalse(ok_fail)
                    self.assertEqual(erro_fail, "ferramenta_desktop_ausente")
        finally:
            self.agent.mock_mode = True

    def test_16_fechar_navegador_detects_and_closes_edge(self):
        """Valida que janelas do Microsoft Edge no Linux são detectadas e fechadas graciosamente via wmctrl."""
        self.agent.mock_mode = False
        try:
            with patch("shutil.which", side_effect=lambda x: f"/usr/bin/{x}" if x in ("wmctrl", "pgrep") else None):
                wmctrl_output_edge = (
                    "0x01c00003 -1 Ananuke Controle por voz Arduino - Pessoal — Microsoft Edge\n"
                    "0x0520000a  0 Ananuke anakin@Ananuke: ~/jarvis-voice\n"
                )

                chamadas_run = []

                def fake_run_edge(cmd, *args, **kwargs):
                    chamadas_run.append(cmd)
                    if cmd[0] == "wmctrl" and cmd[1] == "-l":
                        # Na segunda chamada (confirmação pós-fechamento), janela já fechou
                        if any(c[0] == "wmctrl" and c[1:3] == ["-i", "-c"] for c in chamadas_run):
                            return subprocess.CompletedProcess(cmd, returncode=0, stdout="0x0520000a 0 Ananuke bash\n")
                        return subprocess.CompletedProcess(cmd, returncode=0, stdout=wmctrl_output_edge)
                    if cmd[0] == "wmctrl" and cmd[1:3] == ["-i", "-c"]:
                        return subprocess.CompletedProcess(cmd, returncode=0, stdout="")
                    if cmd[0] == "pgrep":
                        return subprocess.CompletedProcess(cmd, returncode=0, stdout="9999\n")
                    return subprocess.CompletedProcess(cmd, returncode=1, stdout="")

                with patch("subprocess.run", side_effect=fake_run_edge):
                    with patch("time.sleep", return_value=None):
                        ok, res, erro, msg = self.agent.executar_acao("fechar_navegador", {}, "ev-close-edge")
                        self.assertTrue(ok)
                        self.assertEqual(res.get("processos_fechados"), ["Microsoft Edge"])
                        self.assertIn("Microsoft Edge", msg)
        finally:
            self.agent.mock_mode = True

    def test_17_browser_launch_desktop_affinity(self):
        """Valida que abrir_navegador e abrir_youtube respeitam a afinidade de desktop (xdg-open no GNOME, exo-open no Xfce)."""
        self.agent.mock_mode = False
        try:
            # 1. No GNOME, prioriza xdg-open
            self.agent.desktop_env = "gnome"
            with patch("shutil.which", side_effect=lambda x: f"/usr/bin/{x}" if x in ("xdg-open", "exo-open") else None):
                with patch("subprocess.Popen") as mock_popen:
                    ok, res, erro, msg = self.agent.executar_acao("abrir_navegador", {}, "ev-gnome-browser")
                    self.assertTrue(ok)
                    mock_popen.assert_called_once()
                    self.assertEqual(mock_popen.call_args[0][0], ["xdg-open", "about:blank"])

            # 2. No GNOME, abrir_youtube via xdg-open
            with patch("shutil.which", side_effect=lambda x: f"/usr/bin/{x}" if x in ("xdg-open", "exo-open") else None):
                with patch("subprocess.Popen") as mock_popen:
                    ok_yt, res_yt, erro_yt, msg_yt = self.agent.executar_acao("abrir_youtube", {}, "ev-gnome-yt")
                    self.assertTrue(ok_yt)
                    mock_popen.assert_called_once()
                    self.assertEqual(mock_popen.call_args[0][0], ["xdg-open", "https://www.youtube.com"])

            # 3. No Xfce, prioriza exo-open
            self.agent.desktop_env = "xfce"
            with patch("shutil.which", side_effect=lambda x: f"/usr/bin/{x}" if x in ("exo-open", "xdg-open") else None):
                with patch("subprocess.Popen") as mock_popen:
                    ok_xf, res_xf, erro_xf, msg_xf = self.agent.executar_acao("abrir_navegador", {}, "ev-xfce-browser")
                    self.assertTrue(ok_xf)
                    mock_popen.assert_called_once()
                    self.assertEqual(mock_popen.call_args[0][0], ["exo-open", "--launch", "WebBrowser", "about:blank"])

            # 4. Falha segura se nenhum lançador existir
            with patch("shutil.which", return_value=None):
                ok_none, res_none, erro_none, msg_none = self.agent.executar_acao("abrir_navegador", {}, "ev-no-browser")
                self.assertFalse(ok_none)
                self.assertEqual(erro_none, "navegador_nao_encontrado")
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
        """Valida fallback de sys.path quando agente Linux é executado diretamente como script de qualquer diretório."""
        script_path = os.path.join(self.repo_root, "pc_control", "agents", "linux_agent.py")
        cmd = [sys.executable, script_path, "--help"]
        # Executa a partir de um diretório externo (/tmp) para certificar que o fallback de sys.path opera
        res = subprocess.run(cmd, cwd=tempfile.gettempdir(), capture_output=True, text=True, timeout=5)
        self.assertEqual(res.returncode, 0, f"Falha no script direto: {res.stderr}")
        self.assertIn("Agente Local do Jarvis PC Control para Linux", res.stdout)

    def test_04_windows_agent_standalone_script_startup(self):
        """Valida fallback de sys.path quando agente Windows é executado diretamente como script de qualquer diretório."""
        script_path = os.path.join(self.repo_root, "pc_control", "agents", "windows_agent.py")
        cmd = [sys.executable, script_path, "--help"]
        res = subprocess.run(cmd, cwd=tempfile.gettempdir(), capture_output=True, text=True, timeout=5)
        self.assertEqual(res.returncode, 0, f"Falha no script direto: {res.stderr}")
        self.assertIn("Agente Local do Jarvis PC Control para Windows", res.stdout)

    def test_05_linux_agent_module_startup_with_mock_flag(self):
        """Valida que o agente Linux suporta a flag --mock e opções de bind sem crash por AttributeError."""
        cmd = [sys.executable, "-m", "pc_control.agents.linux_agent", "--mock", "--port", "0"]
        p = subprocess.Popen(cmd, cwd=self.repo_root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            time.sleep(0.6)
            p.terminate()
            stdout, stderr = p.communicate(timeout=3)
            self.assertIn(p.returncode, (0, -15, 143))
            self.assertIn("Agente Linux (Ananuke) iniciado", stderr)
            self.assertNotIn("AttributeError", stderr)
        except Exception:
            p.kill()
            p.communicate()
            raise

    def test_06_linux_agent_cli_desktop_options(self):
        """Valida que a flag --desktop aceita auto, gnome e xfce e rejeita valores inválidos."""
        for valido in ("auto", "gnome", "xfce"):
            cmd = [sys.executable, "-m", "pc_control.agents.linux_agent", "--desktop", valido, "--help"]
            res = subprocess.run(cmd, cwd=self.repo_root, capture_output=True, text=True, timeout=5)
            self.assertEqual(res.returncode, 0)

        cmd_inv = [sys.executable, "-m", "pc_control.agents.linux_agent", "--desktop", "invalido"]
        res_inv = subprocess.run(cmd_inv, cwd=self.repo_root, capture_output=True, text=True, timeout=5)
        self.assertNotEqual(res_inv.returncode, 0)
        self.assertIn("invalid choice", res_inv.stderr)


if __name__ == "__main__":
    unittest.main()
