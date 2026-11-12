#!/usr/bin/env python3
"""Agente Local do Jarvis PC Control para Linux (Computador Ananuke - Debian 13 / GNOME e Xfce - 192.168.0.131).

Projetado para rodar na sessão de usuário gráfica (GNOME Shell sobre X11 ou Xfce, não executa como root).
Detecta automaticamente o ambiente de desktop (priorizando ferramentas nativas: gnome-terminal e D-Bus ScreenSaver no GNOME;
xfce4-terminal e xflock4 no Xfce) e o backend de áudio (PipeWire wpctl, PulseAudio pactl ou ALSA amixer).
Permite apenas os 11 comandos autorizados na allowlist estrita.
"""
from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Set, Tuple

if __package__ is None or __package__ == "":
    _pkg_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    if _pkg_root not in sys.path:
        sys.path.insert(0, _pkg_root)

from pc_control.agents.base_agent import PcAgentServer
from pc_control.config import get_agent_token, is_mock_mode

logger = logging.getLogger("JarvisLinuxAgent")

# Processos comuns de navegadores no Debian para fechamento gracioso (SIGTERM / wmctrl)
LINUX_BROWSER_PROCESSES = [
    "firefox-bin",
    "firefox",
    "chromium",
    "chromium-browser",
    "chrome",
    "google-chrome",
    "google-chrome-stable",
    "brave",
    "brave-browser",
    "msedge",
    "microsoft-edge",
]


class LinuxPcAgent(PcAgentServer):
    """Agente de controle local para Debian 13 com detecção automática GNOME / Xfce (Ananuke)."""

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8092,
        auth_token: Optional[str] = None,
        mock_mode: bool = False,
        allowed_ips: Optional[Set[str]] = None,
        certfile: Optional[str] = None,
        keyfile: Optional[str] = None,
        desktop_env: Optional[str] = None,
    ) -> None:
        token = auth_token or get_agent_token("ananuke")
        self.mock_mode = mock_mode or is_mock_mode()
        self.audio_backend = self._detectar_backend_audio()
        if desktop_env and desktop_env.strip().lower() not in ("auto", ""):
            self.desktop_env = desktop_env.strip().lower()
        else:
            self.desktop_env = self._detectar_ambiente_desktop().lower()
        # Nível de volume simulado em testes (0.0 a 1.0)
        self._sim_volume = 0.50
        self._sim_muted = False
        super().__init__(
            host,
            port,
            target_id="ananuke",
            auth_token=token,
            allowed_ips=allowed_ips,
            certfile=certfile,
            keyfile=keyfile,
        )

    def _detectar_ambiente_desktop(self) -> str:
        """Detecta automaticamente se o ambiente gráfico é GNOME ou Xfce (ou desconhecido)."""
        # 1. Variável de override explícita
        env_override = os.getenv("JARVIS_DESKTOP_ENV", "").strip().lower()
        if env_override in ("gnome", "xfce"):
            return env_override

        # 2. Variáveis de sessão do freedesktop / gerenciador de display
        sess_vars = [
            os.getenv("XDG_CURRENT_DESKTOP", ""),
            os.getenv("DESKTOP_SESSION", ""),
            os.getenv("GDMSESSION", ""),
        ]
        sess_str = " ".join(sess_vars).lower()
        if "gnome" in sess_str:
            return "gnome"
        if "xfce" in sess_str:
            return "xfce"

        # 3. Processos ativos da sessão gráfica do usuário atual
        uid_str = str(os.getuid())
        try:
            res_gnome = subprocess.run(
                ["pgrep", "-u", uid_str, "-x", "gnome-shell"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if res_gnome.returncode == 0 and res_gnome.stdout.strip():
                return "gnome"
        except Exception:
            pass

        try:
            for xfce_proc in ("xfce4-session", "xfwm4"):
                res_xfce = subprocess.run(
                    ["pgrep", "-u", uid_str, "-x", xfce_proc],
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
                if res_xfce.returncode == 0 and res_xfce.stdout.strip():
                    return "xfce"
        except Exception:
            pass

        # Heurística complementar por processo ativo caso UID seja root ou isolado
        try:
            res_all_gnome = subprocess.run(
                ["pgrep", "-x", "gnome-shell"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if res_all_gnome.returncode == 0 and res_all_gnome.stdout.strip():
                return "gnome"
        except Exception:
            pass

        # 4. Binários instalados no sistema como heurística final
        if shutil.which("gnome-shell") and not shutil.which("xfce4-session"):
            return "gnome"
        if shutil.which("xfce4-session") and not shutil.which("gnome-shell"):
            return "xfce"

        return "desconhecido"

    def _detectar_backend_audio(self) -> str:
        """Detecta o backend de áudio instalado no Debian 13."""
        if shutil.which("wpctl") is not None:
            return "pipewire"
        if shutil.which("pactl") is not None:
            return "pulseaudio"
        if shutil.which("amixer") is not None:
            return "alsa"
        return "desconhecido"

    def _obter_env_sessao(self) -> Dict[str, str]:
        """Obtém variáveis de ambiente da sessão gráfica do usuário."""
        env = os.environ.copy()
        uid = os.getuid()
        if "DISPLAY" not in env:
            env["DISPLAY"] = ":0"
        if "XDG_RUNTIME_DIR" not in env:
            env["XDG_RUNTIME_DIR"] = f"/run/user/{uid}"
        if "DBUS_SESSION_BUS_ADDRESS" not in env:
            bus_path = f"/run/user/{uid}/bus"
            if os.path.exists(bus_path):
                env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={bus_path}"
        if uid == 0:
            logger.warning("Agente Linux executando como root: aplicações gráficas devem ser invocadas na sessão de usuário.")
        return env

    def executar_acao(
        self,
        action: str,
        params: Dict[str, Any],
        event_id: str,
        session_id: Optional[str] = None,
    ) -> Tuple[bool, Dict[str, Any], Optional[str], str]:
        """Despacha a ação permitida para seu respectivo handler no Linux."""
        handlers = {
            "abrir_navegador": self._acao_abrir_navegador,
            "abrir_youtube": self._acao_abrir_youtube,
            "bloquear": self._acao_bloquear,
            "aumentar_volume": self._acao_aumentar_volume,
            "diminuir_volume": self._acao_diminuir_volume,
            "silenciar_audio": self._acao_silenciar_audio,
            "status_computador": self._acao_status_computador,
            "abrir_terminal": self._acao_abrir_terminal,
            "fechar_navegador": self._acao_fechar_navegador,
            "reiniciar": self._acao_reiniciar,
            "mostrar_area_de_trabalho": self._acao_mostrar_area_de_trabalho,
        }

        handler = handlers.get(action)
        if not handler:
            return False, {}, "acao_nao_suportada", f"Ação {action} não suportada no agente Linux."

        return handler(params)

    def _obter_navegador_padrao_desktop(self) -> Optional[str]:
        """Obtém dinamicamente o arquivo .desktop do navegador padrão configurado no sistema.

        Prioriza xdg-settings (padrão freedesktop/GNOME) e faz fallback para xdg-mime e gio mime.
        Não fixa 'firefox-esr.desktop' no código para manter suporte dinâmico a Chrome, Chromium, Brave, Edge, etc.
        """
        env = self._obter_env_sessao()

        # 1. xdg-settings get default-web-browser
        if shutil.which("xdg-settings"):
            try:
                res = subprocess.run(
                    ["xdg-settings", "get", "default-web-browser"],
                    capture_output=True,
                    text=True,
                    timeout=2,
                    env=env,
                )
                if res.returncode == 0:
                    app = res.stdout.strip().strip("'\"")
                    if app:
                        return app if app.endswith(".desktop") else f"{app}.desktop"
            except Exception as e:
                logger.debug("Falha ao consultar xdg-settings: %s", e)

        # 2. xdg-mime query default x-scheme-handler/http
        if shutil.which("xdg-mime"):
            try:
                res = subprocess.run(
                    ["xdg-mime", "query", "default", "x-scheme-handler/http"],
                    capture_output=True,
                    text=True,
                    timeout=2,
                    env=env,
                )
                if res.returncode == 0:
                    app = res.stdout.strip().strip("'\"")
                    if app:
                        return app if app.endswith(".desktop") else f"{app}.desktop"
            except Exception as e:
                logger.debug("Falha ao consultar xdg-mime (x-scheme-handler/http): %s", e)

        # 3. xdg-mime query default text/html
        if shutil.which("xdg-mime"):
            try:
                res = subprocess.run(
                    ["xdg-mime", "query", "default", "text/html"],
                    capture_output=True,
                    text=True,
                    timeout=2,
                    env=env,
                )
                if res.returncode == 0:
                    app = res.stdout.strip().strip("'\"")
                    if app:
                        return app if app.endswith(".desktop") else f"{app}.desktop"
            except Exception as e:
                logger.debug("Falha ao consultar xdg-mime (text/html): %s", e)

        # 4. gio mime x-scheme-handler/http
        if shutil.which("gio"):
            try:
                res = subprocess.run(
                    ["gio", "mime", "x-scheme-handler/http"],
                    capture_output=True,
                    text=True,
                    timeout=2,
                    env=env,
                )
                if res.returncode == 0 and res.stdout:
                    for line in res.stdout.splitlines():
                        if ".desktop" in line:
                            for token in line.split():
                                token = token.strip(":,()“”\"'")
                                if token.endswith(".desktop"):
                                    return token
            except Exception as e:
                logger.debug("Falha ao consultar gio mime: %s", e)

        return None

    def _executar_comando_abertura(
        self, cmd: List[str], env: Dict[str, str], timeout_check: float = 0.4
    ) -> Tuple[bool, Optional[str]]:
        """Executa um comando lançador/navegador e valida retorno e erros imediatos."""
        is_launcher = bool(cmd and cmd[0] in ("gtk-launch", "xdg-open", "exo-open", "gio"))
        try:
            proc = subprocess.Popen(
                cmd,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE if is_launcher else subprocess.DEVNULL,
                text=True,
            )
        except Exception as e:
            logger.warning("Falha ao invocar processo %s: %s", cmd, e)
            return False, str(e)

        try:
            if is_launcher:
                comm_res = proc.communicate(timeout=timeout_check)
                if isinstance(comm_res, tuple) and len(comm_res) == 2:
                    stdout_text, stderr_text = comm_res
                else:
                    stdout_text, stderr_text = "", ""

                rc = proc.returncode
                if isinstance(rc, int) and rc != 0:
                    err_msg = str(stderr_text or stdout_text or f"código de saída {rc}").strip()
                    logger.warning("Lançador %s falhou com código %d: %s", cmd, rc, err_msg)
                    return False, f"codigo_{rc}: {err_msg}"
                return True, None
            else:
                try:
                    rc = proc.wait(timeout=timeout_check)
                    if isinstance(rc, int) and rc != 0:
                        logger.warning("Processo %s falhou com código %d", cmd, rc)
                        return False, f"codigo_{rc}"
                    return True, None
                except subprocess.TimeoutExpired:
                    # Processo direto continua em execução saudável após timeout
                    return True, None
        except subprocess.TimeoutExpired:
            logger.warning("Timeout aguardando lançador %s", cmd)
            return False, f"timeout aguardando lançador {cmd[0]}"
        except Exception as e:
            logger.warning("Erro ao executar %s: %s", cmd, e)
            return False, str(e)

    # 1. Abrir navegador
    def _acao_abrir_navegador(self, params: Dict[str, Any]) -> Tuple[bool, Dict[str, Any], Optional[str], str]:
        url = params.get("url", "about:blank")
        if not self.mock_mode:
            env = self._obter_env_sessao()
            default_app = self._obter_navegador_padrao_desktop()

            candidatos: List[List[str]] = []

            def _adicionar_candidato(c: List[str]):
                if c and c not in candidatos:
                    candidatos.append(c)

            # Argumentos para gtk-launch: abre sem URL se for about:blank para evitar erro GIO/gvfs
            gtk_args = [default_app] if default_app else []
            if default_app and url and url != "about:blank":
                gtk_args.append(url)

            if self.desktop_env == "xfce":
                # No Xfce, prioriza exo-open
                if shutil.which("exo-open"):
                    _adicionar_candidato(["exo-open", "--launch", "WebBrowser", url])
                if default_app and shutil.which("gtk-launch"):
                    _adicionar_candidato(["gtk-launch"] + gtk_args)
                if shutil.which("x-www-browser"):
                    _adicionar_candidato(["x-www-browser"])
                if default_app:
                    bin_cand = default_app[:-8] if default_app.endswith(".desktop") else default_app
                    if shutil.which(bin_cand):
                        _adicionar_candidato([bin_cand])
                if shutil.which("xdg-open"):
                    _adicionar_candidato(["xdg-open", url])
                if shutil.which("gio"):
                    _adicionar_candidato(["gio", "open", url])
            elif self.desktop_env == "gnome":
                # 1. No GNOME, prioriza lançador padrão configurado no sistema (xdg-settings + gtk-launch)
                if default_app and shutil.which("gtk-launch"):
                    _adicionar_candidato(["gtk-launch"] + gtk_args)
                # 2. x-www-browser (alternativas Debian sem URL problemática)
                if shutil.which("x-www-browser"):
                    _adicionar_candidato(["x-www-browser"])
                # 3. Binário direto derivado do .desktop se presente no PATH
                if default_app:
                    bin_cand = default_app[:-8] if default_app.endswith(".desktop") else default_app
                    if shutil.which(bin_cand):
                        _adicionar_candidato([bin_cand])
                # 4. Fallbacks freedesktop (tratando erro para impedir falso positivo se GIO rejeitar url)
                if shutil.which("xdg-open"):
                    _adicionar_candidato(["xdg-open", url])
                if shutil.which("gio"):
                    _adicionar_candidato(["gio", "open", url])
                if shutil.which("exo-open"):
                    _adicionar_candidato(["exo-open", "--launch", "WebBrowser", url])
            else:
                # Ambientes não especificados ou desconhecidos
                if default_app and shutil.which("gtk-launch"):
                    _adicionar_candidato(["gtk-launch"] + gtk_args)
                if shutil.which("exo-open"):
                    _adicionar_candidato(["exo-open", "--launch", "WebBrowser", url])
                if shutil.which("x-www-browser"):
                    _adicionar_candidato(["x-www-browser"])
                if default_app:
                    bin_cand = default_app[:-8] if default_app.endswith(".desktop") else default_app
                    if shutil.which(bin_cand):
                        _adicionar_candidato([bin_cand])
                if shutil.which("xdg-open"):
                    _adicionar_candidato(["xdg-open", url])
                if shutil.which("gio"):
                    _adicionar_candidato(["gio", "open", url])

            if not candidatos:
                return False, {}, "navegador_nao_encontrado", "Nenhum navegador padrão encontrado no sistema."

            sucesso = False
            comando_usado = None
            ultimo_erro = None

            for cmd in candidatos:
                ok, err = self._executar_comando_abertura(cmd, env)
                if ok:
                    sucesso = True
                    comando_usado = cmd
                    break
                else:
                    ultimo_erro = err

            if not sucesso:
                return (
                    False,
                    {},
                    f"erro_abrir_navegador: {ultimo_erro}",
                    "Falha ao abrir navegador no Ananuke.",
                )

            return (
                True,
                {"url": url, "comando": comando_usado, "simulado": False},
                None,
                "Navegador padrão aberto no Ananuke.",
            )

        return True, {"url": url, "simulado": self.mock_mode}, None, "Navegador padrão aberto no Ananuke."

    # 2. Abrir YouTube
    def _acao_abrir_youtube(self, params: Dict[str, Any]) -> Tuple[bool, Dict[str, Any], Optional[str], str]:
        url = "https://www.youtube.com"
        if not self.mock_mode:
            env = self._obter_env_sessao()
            cmd = None
            if self.desktop_env == "xfce" and shutil.which("exo-open"):
                cmd = ["exo-open", "--launch", "WebBrowser", url]
            elif shutil.which("xdg-open"):
                cmd = ["xdg-open", url]
            elif shutil.which("gio"):
                cmd = ["gio", "open", url]
            elif shutil.which("exo-open"):
                cmd = ["exo-open", "--launch", "WebBrowser", url]
            elif shutil.which("x-www-browser"):
                cmd = ["x-www-browser", url]

            if not cmd:
                return False, {}, "navegador_nao_encontrado", "Nenhum navegador encontrado no sistema para abrir YouTube."

            try:
                subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception as e:
                return False, {}, f"erro_abrir_youtube: {e}", "Falha ao abrir YouTube no Ananuke."

        return True, {"url": url, "simulado": self.mock_mode}, None, "YouTube aberto no Ananuke."

    # 3. Bloquear computador (GNOME Shell / Xfce / loginctl)
    def _acao_bloquear(self, params: Dict[str, Any]) -> Tuple[bool, Dict[str, Any], Optional[str], str]:
        if not self.mock_mode:
            env = self._obter_env_sessao()
            candidatos: List[List[str]] = []

            if self.desktop_env == "gnome":
                # Mecanismos de bloqueio confiáveis para GNOME Shell
                candidatos = [
                    [
                        "dbus-send",
                        "--session",
                        "--type=method_call",
                        "--dest=org.gnome.ScreenSaver",
                        "/org/gnome/ScreenSaver",
                        "org.gnome.ScreenSaver.Lock",
                    ],
                    ["loginctl", "lock-session"],
                    ["gnome-screensaver-command", "-l"],
                    ["xdg-screensaver", "lock"],
                ]
            elif self.desktop_env == "xfce":
                # Mecanismos de bloqueio para Xfce
                candidatos = [
                    ["xflock4"],
                    ["xfce4-screensaver-command", "-l"],
                    ["loginctl", "lock-session"],
                    ["xdg-screensaver", "lock"],
                ]
            else:
                # Fallback genérico para outros ambientes
                candidatos = [
                    [
                        "dbus-send",
                        "--session",
                        "--type=method_call",
                        "--dest=org.gnome.ScreenSaver",
                        "/org/gnome/ScreenSaver",
                        "org.gnome.ScreenSaver.Lock",
                    ],
                    ["xflock4"],
                    ["loginctl", "lock-session"],
                    ["gnome-screensaver-command", "-l"],
                    ["xfce4-screensaver-command", "-l"],
                    ["xdg-screensaver", "lock"],
                ]

            valid_candidates = [c for c in candidatos if shutil.which(c[0])]
            if not valid_candidates:
                return False, {}, "bloqueador_nao_encontrado", "Nenhuma ferramenta de bloqueio de sessão encontrada."

            sucesso = False
            ultimo_erro = None
            for cmd in valid_candidates:
                try:
                    subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    sucesso = True
                    break
                except Exception as e:
                    ultimo_erro = e
                    logger.warning("Falha ao invocar comando de bloqueio %s: %s", cmd[0], e)

            if not sucesso:
                return False, {}, f"erro_bloqueio: {ultimo_erro}", "Falha ao bloquear a sessão no Ananuke."

        return (
            True,
            {"bloqueado": True, "desktop": self.desktop_env, "simulado": self.mock_mode},
            None,
            "Computador Ananuke bloqueado com sucesso.",
        )

    # 4. Aumentar volume
    def _acao_aumentar_volume(self, params: Dict[str, Any]) -> Tuple[bool, Dict[str, Any], Optional[str], str]:
        passo = params.get("passo", 0.05)
        novo_vol, ok = self._ajustar_volume(delta=+passo)
        if not ok:
            return False, {}, "erro_volume", "Falha ao aumentar o volume no Ananuke."
        pct = int(round(novo_vol * 100))
        return True, {"volume_pct": pct, "simulado": self.mock_mode, "backend": self.audio_backend}, None, f"Volume aumentado no Ananuke para {pct}%."

    # 5. Diminuir volume
    def _acao_diminuir_volume(self, params: Dict[str, Any]) -> Tuple[bool, Dict[str, Any], Optional[str], str]:
        passo = params.get("passo", 0.05)
        novo_vol, ok = self._ajustar_volume(delta=-passo)
        if not ok:
            return False, {}, "erro_volume", "Falha ao diminuir o volume no Ananuke."
        pct = int(round(novo_vol * 100))
        return True, {"volume_pct": pct, "simulado": self.mock_mode, "backend": self.audio_backend}, None, f"Volume diminuído no Ananuke para {pct}%."

    # 6. Silenciar áudio (idempotente: define mudo = True, nunca inverte)
    def _acao_silenciar_audio(self, params: Dict[str, Any]) -> Tuple[bool, Dict[str, Any], Optional[str], str]:
        ok = self._definir_mudo(mute=True)
        if not ok:
            return False, {}, "erro_mudo", "Falha ao silenciar o áudio no Ananuke."
        return True, {"mudo": True, "simulado": self.mock_mode, "backend": self.audio_backend}, None, "Áudio silenciado no Ananuke."

    # 7. Informar status do computador (CPU, Memória, Uptime)
    def _acao_status_computador(self, params: Dict[str, Any]) -> Tuple[bool, Dict[str, Any], Optional[str], str]:
        cpu_pct, mem_pct, uptime_str = self._ler_metricas_linux()
        msg = f"Ananuke: CPU {cpu_pct:.0f}% | RAM {mem_pct:.0f}% | Ativo há {uptime_str}."
        return (
            True,
            {"cpu_percent": cpu_pct, "memoria_percent": mem_pct, "uptime": uptime_str, "simulado": self.mock_mode},
            None,
            msg,
        )

    # 8. Abrir terminal (gnome-terminal no GNOME, xfce4-terminal no Xfce)
    def _acao_abrir_terminal(self, params: Dict[str, Any]) -> Tuple[bool, Dict[str, Any], Optional[str], str]:
        if self.desktop_env == "gnome":
            candidatos = ["gnome-terminal", "x-terminal-emulator", "xfce4-terminal"]
        elif self.desktop_env == "xfce":
            candidatos = ["xfce4-terminal", "x-terminal-emulator", "gnome-terminal"]
        else:
            candidatos = ["gnome-terminal", "xfce4-terminal", "x-terminal-emulator"]

        if self.mock_mode:
            terminal_cmd = candidatos[0]
            return (
                True,
                {"terminal": terminal_cmd, "desktop": self.desktop_env, "simulado": True},
                None,
                "Terminal aberto no Ananuke.",
            )

        env = self._obter_env_sessao()
        terminal_cmd = None
        for cand in candidatos:
            if shutil.which(cand):
                terminal_cmd = cand
                break

        if not terminal_cmd:
            return False, {}, "terminal_nao_encontrado", "Nenhum emulador de terminal encontrado no Ananuke."

        try:
            subprocess.Popen([terminal_cmd], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            return False, {}, f"erro_terminal: {e}", f"Falha ao abrir {terminal_cmd} no Ananuke."

        return (
            True,
            {"terminal": terminal_cmd, "desktop": self.desktop_env, "simulado": False},
            None,
            "Terminal aberto no Ananuke.",
        )

    # 9. Fechar navegador (após confirmação)
    def _acao_fechar_navegador(self, params: Dict[str, Any]) -> Tuple[bool, Dict[str, Any], Optional[str], str]:
        if self.mock_mode:
            if params.get("simular_nenhum"):
                return False, {"processos_fechados": [], "simulado": True}, "nenhum_navegador_encontrado", "Nenhum navegador aberto para fechar no Ananuke."
            if params.get("simular_nao_confirmado"):
                return False, {"processos_fechados": [], "simulado": True}, "fechamento_nao_confirmado", "Nenhum navegador foi fechado ou confirmado no Ananuke."
            if params.get("simular_sem_wmctrl"):
                return False, {"processos_fechados": [], "simulado": True}, "fechamento_gracioso_indisponivel", "Não foi possível fechar as janelas graciosamente: utilitário wmctrl não disponível no sistema."
            return True, {"processos_fechados": ["firefox (simulado)"], "simulado": True}, None, "Navegador fechado no Ananuke."

        env = self._obter_env_sessao()
        fechados: List[str] = []

        # 1. Detectar janelas de navegadores via wmctrl ou processos ativos do usuário
        janelas_encontradas: List[Tuple[str, str]] = []  # (win_id, canonical_name)
        browser_patterns = [
            ("Mozilla Firefox", "Firefox"),
            ("Google Chrome", "Google Chrome"),
            ("Chromium", "Chromium"),
            ("Brave", "Brave"),
            ("Microsoft Edge", "Microsoft Edge"),
            ("Edge", "Microsoft Edge"),
            ("Firefox", "Firefox"),
            ("Chrome", "Google Chrome"),
        ]
        wmctrl_disponivel = bool(shutil.which("wmctrl"))
        wmctrl_list_ok = False
        if wmctrl_disponivel:
            try:
                res_list = subprocess.run(["wmctrl", "-l"], env=env, capture_output=True, text=True, timeout=2)
                if res_list.returncode == 0:
                    wmctrl_list_ok = True
                    for linha in res_list.stdout.splitlines():
                        tokens = linha.split(None, 3)
                        if len(tokens) >= 4:
                            win_id = tokens[0]
                            title = tokens[3]
                            for pattern, canonical in browser_patterns:
                                if pattern.lower() in title.lower():
                                    janelas_encontradas.append((win_id, canonical))
                                    break
                else:
                    logger.warning("wmctrl retornou código %d ao listar janelas: %s", res_list.returncode, res_list.stderr.strip())
            except Exception as e:
                logger.warning("Falha ao executar wmctrl: %s", e)

        # Se wmctrl não achou janelas, checar processos gráficos do usuário
        processos_ativos: List[str] = []
        uid_str = str(os.getuid())
        for nome_proc in LINUX_BROWSER_PROCESSES:
            try:
                res_pg = subprocess.run(["pgrep", "-u", uid_str, "-x", nome_proc], capture_output=True, text=True, timeout=2)
                if res_pg.returncode == 0 and res_pg.stdout.strip():
                    processos_ativos.append(nome_proc)
            except Exception:
                pass

        # Se nenhum navegador está em execução nem tem janela aberta, falha sem executar ação
        if not janelas_encontradas and not processos_ativos:
            return False, {"processos_fechados": []}, "nenhum_navegador_encontrado", "Nenhum navegador aberto para fechar no Ananuke."

        # 2. Fechamento gracioso prioritário via wmctrl (envia _NET_CLOSE_WINDOW, permitindo salvar estado e abas)
        # Jamais invocar pkill ou sinais de processo: encerramento indiscriminado por sinal é estritamente proibido!
        if wmctrl_disponivel and wmctrl_list_ok and janelas_encontradas:
            for win_id, canonical in janelas_encontradas:
                try:
                    subprocess.run(["wmctrl", "-i", "-c", win_id], env=env, capture_output=True, timeout=2)
                except Exception as e:
                    logger.debug("Falha ao solicitar fechamento gracioso via wmctrl (%s): %s", win_id, e)

            # Confirmação pós-fechamento: aguarda até 1.5s verificando se as janelas realmente fecharam
            t_limite = time.time() + 1.5
            janelas_alvo_ids = {w[0] for w in janelas_encontradas}
            while time.time() < t_limite:
                time.sleep(0.3)
                try:
                    res_chk = subprocess.run(["wmctrl", "-l"], env=env, capture_output=True, text=True, timeout=2)
                    if res_chk.returncode == 0:
                        janelas_restantes = {l.split(None, 3)[0] for l in res_chk.stdout.splitlines() if len(l.split(None, 3)) >= 4}
                        fechados_ids = janelas_alvo_ids - janelas_restantes
                        if fechados_ids:
                            fechados = [w[1] for w in janelas_encontradas if w[0] in fechados_ids]
                            if len(fechados) == len(janelas_encontradas):
                                break
                except Exception:
                    pass

        # 3. Se wmctrl não estiver disponível ou falhar na sessão gráfica, encerramento gracioso não é possível
        # Jamais invocar pkill ou sinais de processo: retorna erro seguro fail-closed
        elif not wmctrl_disponivel or not wmctrl_list_ok:
            motivo_detalhe = (
                "utilitário wmctrl não disponível no sistema."
                if not wmctrl_disponivel
                else "falha ao comunicar com o servidor gráfico via wmctrl."
            )
            logger.warning("Fechamento gracioso indisponível no Ananuke: %s", motivo_detalhe)
            return (
                False,
                {"processos_fechados": []},
                "fechamento_gracioso_indisponivel",
                f"Não foi possível fechar as janelas graciosamente: {motivo_detalhe}",
            )

        # 4. Se wmctrl está disponível e funcionou, mas nenhuma janela gráfica de navegador foi localizada
        elif not janelas_encontradas:
            logger.warning("wmctrl disponível, mas nenhuma janela gráfica de navegador foi localizada.")
            return (
                False,
                {"processos_fechados": []},
                "nenhuma_janela_localizada",
                "Nenhuma janela de navegador aberta foi localizada para fechamento gracioso no Ananuke.",
            )

        # 5. Validação de confirmação: nunca reporta sucesso se nada foi fechado
        if not fechados:
            return False, {"processos_fechados": []}, "fechamento_nao_confirmado", "Nenhum navegador foi fechado ou confirmado no Ananuke."

        fechados_unicos = list(dict.fromkeys(fechados))
        return True, {"processos_fechados": fechados_unicos, "simulado": False}, None, f"Navegador fechado no Ananuke ({', '.join(fechados_unicos)})."

    # 10. Reiniciar computador (após confirmação explícita)
    def _acao_reiniciar(self, params: Dict[str, Any]) -> Tuple[bool, Dict[str, Any], Optional[str], str]:
        if not self.mock_mode:
            try:
                # systemctl reboot sem sudo se usuário tiver permissão polkit ou com sudo
                cmd = ["systemctl", "reboot"]
                subprocess.run(cmd, check=True, timeout=5)
            except Exception as e:
                return False, {}, f"erro_reboot: {e}", "Falha ao solicitar reinicialização do Ananuke."
        return True, {"reiniciando": True, "simulado": self.mock_mode}, None, "Reinicialização do Ananuke solicitada com sucesso."

    def _mostrar_desktop_x11(self) -> bool:
        """Envia mensagem ClientMessage _NET_SHOWING_DESKTOP via libX11 (EWMH padrão)."""
        try:
            import ctypes

            class XClientMsg(ctypes.Structure):
                _fields_ = [
                    ("type", ctypes.c_int),
                    ("serial", ctypes.c_ulong),
                    ("send_event", ctypes.c_int),
                    ("display", ctypes.c_void_p),
                    ("window", ctypes.c_ulong),
                    ("message_type", ctypes.c_ulong),
                    ("format", ctypes.c_int),
                    ("data", ctypes.c_long * 5),
                ]

            class XEv(ctypes.Union):
                _fields_ = [("type", ctypes.c_int), ("xclient", XClientMsg), ("pad", ctypes.c_long * 24)]

            x11 = ctypes.cdll.LoadLibrary("libX11.so.6")
            x11.XOpenDisplay.restype = ctypes.c_void_p
            disp = x11.XOpenDisplay(None)
            if not disp:
                return False
            root = x11.XDefaultRootWindow(disp)
            atom = x11.XInternAtom(disp, b"_NET_SHOWING_DESKTOP", False)
            if not atom:
                x11.XCloseDisplay(disp)
                return False

            ev = XEv()
            ev.type = 33  # ClientMessage
            ev.xclient.type = 33
            ev.xclient.window = root
            ev.xclient.message_type = atom
            ev.xclient.format = 32
            ev.xclient.data[0] = 1  # 1 = show desktop
            mask = (1 << 19) | (1 << 20)  # SubstructureNotify | SubstructureRedirect
            x11.XSendEvent(disp, root, False, mask, ctypes.byref(ev))
            x11.XFlush(disp)
            x11.XCloseDisplay(disp)
            return True
        except Exception as e:
            logger.debug("Falha no método EWMH libX11: %s", e)
            return False

    # 11. Mostrar área de trabalho (GNOME Shell / Xfce / EWMH sobre X11)
    def _acao_mostrar_area_de_trabalho(self, params: Dict[str, Any]) -> Tuple[bool, Dict[str, Any], Optional[str], str]:
        if not self.mock_mode:
            env = self._obter_env_sessao()
            executou = False

            # 1. wmctrl -k on ativa o modo "Mostrar Área de Trabalho" via EWMH (_NET_SHOWING_DESKTOP)
            # Suportado nativamente pelo Mutter (GNOME Shell sobre X11) e Xfwm4 (Xfce)
            if shutil.which("wmctrl"):
                try:
                    subprocess.run(["wmctrl", "-k", "on"], env=env, check=True, timeout=3)
                    executou = True
                except Exception:
                    pass

            # 2. Atalho de teclado via xdotool
            if not executou and shutil.which("xdotool"):
                try:
                    subprocess.run(["xdotool", "key", "Super_L+d"], env=env, check=True, timeout=3)
                    executou = True
                except Exception:
                    pass

            # 3. Fallback nativo via EWMH libX11 (envio direto do ClientMessage _NET_SHOWING_DESKTOP)
            if not executou:
                executou = self._mostrar_desktop_x11()

            if not executou:
                return False, {}, "ferramenta_desktop_ausente", "Ferramenta para alternar desktop não encontrada."

        return (
            True,
            {"area_de_trabalho": True, "desktop": self.desktop_env, "simulado": self.mock_mode},
            None,
            "Área de trabalho exibida no Ananuke.",
        )

    # Métodos auxiliares de áudio e telemetria Linux
    def _ajustar_volume(self, delta: float) -> Tuple[float, bool]:
        """Ajusta o volume respeitando rigorosamente [0.0, 1.0] (0% a 100%)."""
        if self.mock_mode:
            self._sim_volume = max(0.0, min(1.0, self._sim_volume + delta))
            return self._sim_volume, True

        sinal = "+" if delta > 0 else "-"
        pct_str = f"{int(abs(delta) * 100)}%{sinal}"
        env = self._obter_env_sessao()

        try:
            if self.audio_backend == "pipewire":
                # -l 1.0 garante limite rígido de segurança em 100% de volume
                cmd = ["wpctl", "set-volume", "-l", "1.0", "@DEFAULT_AUDIO_SINK@", pct_str]
                subprocess.run(cmd, env=env, check=True, timeout=3)
                # Consulta volume real
                try:
                    res_vol = subprocess.run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"], env=env, capture_output=True, text=True, timeout=2)
                    if res_vol.returncode == 0 and "Volume:" in res_vol.stdout:
                        vol_val = float(res_vol.stdout.split("Volume:")[1].split()[0])
                        self._sim_volume = max(0.0, min(1.0, vol_val))
                        return self._sim_volume, True
                except Exception:
                    pass
            elif self.audio_backend == "pulseaudio":
                cmd = ["pactl", "set-sink-volume", "@DEFAULT_SINK@", pct_str]
                subprocess.run(cmd, env=env, check=True, timeout=3)
                try:
                    res_vol = subprocess.run(["pactl", "get-sink-volume", "@DEFAULT_SINK@"], env=env, capture_output=True, text=True, timeout=2)
                    if res_vol.returncode == 0 and "%" in res_vol.stdout:
                        import re
                        m = re.search(r"(\d+)%", res_vol.stdout)
                        if m:
                            self._sim_volume = max(0.0, min(1.0, float(m.group(1)) / 100.0))
                            return self._sim_volume, True
                except Exception:
                    pass
            elif self.audio_backend == "alsa":
                cmd = ["amixer", "set", "Master", pct_str]
                subprocess.run(cmd, env=env, check=True, timeout=3)

            self._sim_volume = max(0.0, min(1.0, self._sim_volume + delta))
            return self._sim_volume, True
        except Exception as e:
            logger.error("Falha ao ajustar volume no backend %s: %s", self.audio_backend, e)
            return self._sim_volume, False

    def _definir_mudo(self, mute: bool = True) -> bool:
        """Silencia o áudio de forma estritamente idempotente (sem alternância indesejada)."""
        self._sim_muted = mute
        if self.mock_mode:
            return True

        env = self._obter_env_sessao()
        val_str = "1" if mute else "0"

        try:
            if self.audio_backend == "pipewire":
                # 1 = mute, 0 = unmute (idempotente)
                subprocess.run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", val_str], env=env, check=True, timeout=3)
            elif self.audio_backend == "pulseaudio":
                subprocess.run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", val_str], env=env, check=True, timeout=3)
            elif self.audio_backend == "alsa":
                arg = "mute" if mute else "unmute"
                subprocess.run(["amixer", "set", "Master", arg], env=env, check=True, timeout=3)
            return True
        except Exception as e:
            logger.error("Falha ao definir mudo no backend %s: %s", self.audio_backend, e)
            return False

    def _ler_metricas_linux(self) -> Tuple[float, float, str]:
        """Lê CPU, memória e uptime diretamente dos arquivos /proc do Linux."""
        cpu_pct = 10.0
        mem_pct = 30.0
        uptime_str = "indisponível"

        try:
            # Uptime
            if os.path.exists("/proc/uptime"):
                with open("/proc/uptime", "r", encoding="utf-8") as f:
                    up_s = float(f.read().split()[0])
                horas = int(up_s // 3600)
                minutos = int((up_s % 3600) // 60)
                uptime_str = f"{horas}h {minutos}m"

            # Memória
            if os.path.exists("/proc/meminfo"):
                mem = {}
                with open("/proc/meminfo", "r", encoding="utf-8") as f:
                    for line in f:
                        parts = line.split(":")
                        if len(parts) == 2:
                            val = parts[1].strip().split()[0]
                            if val.isdigit():
                                mem[parts[0].strip()] = int(val)
                total = mem.get("MemTotal", 1)
                avail = mem.get("MemAvailable", mem.get("MemFree", 0))
                mem_pct = round(((total - avail) / total) * 100, 1)

            # CPU (load average de 1 min normalizado)
            if os.path.exists("/proc/loadavg"):
                with open("/proc/loadavg", "r", encoding="utf-8") as f:
                    load1 = float(f.read().split()[0])
                n_cpus = os.cpu_count() or 1
                cpu_pct = min(100.0, round((load1 / n_cpus) * 100, 1))
        except Exception as e:
            logger.warning("Falha ao ler métricas de /proc: %s", e)

        return cpu_pct, mem_pct, uptime_str


def main() -> None:
    parser = argparse.ArgumentParser(description="Agente Local do Jarvis PC Control para Linux (Ananuke)")
    parser.add_argument("--host", default=os.getenv("JARVIS_PC_AGENT_HOST", "0.0.0.0"), help="Endereço de bind (padrão: 0.0.0.0 ou 127.0.0.1 em túnel)")
    parser.add_argument("--port", type=int, default=8092, help="Porta HTTP (padrão: 8092)")
    parser.add_argument("--token", default=None, help="Token compartilhado de autenticação")
    parser.add_argument("--allowed-ips", default=None, help="IPs autorizados separados por vírgula (ex: 192.168.0.126,127.0.0.1)")
    parser.add_argument("--cert", default=None, help="Caminho do certificado TLS (.crt)")
    parser.add_argument("--key", default=None, help="Caminho da chave privada TLS (.key)")
    parser.add_argument("--mock", action="store_true", help="Forçar modo simulado de ações")
    parser.add_argument(
        "--desktop",
        choices=["auto", "gnome", "xfce"],
        default="auto",
        help="Ambiente de desktop (padrão: auto-detecção entre GNOME e Xfce)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] [%(levelname)s] [LinuxAgent] %(message)s",
    )
    allowed_ips = {ip.strip() for ip in args.allowed_ips.split(",") if ip.strip()} if args.allowed_ips else None
    desktop_env = None if args.desktop == "auto" else args.desktop
    server = LinuxPcAgent(
        host=args.host,
        port=args.port,
        auth_token=args.token,
        mock_mode=args.mock,
        allowed_ips=allowed_ips,
        certfile=args.cert,
        keyfile=args.key,
        desktop_env=desktop_env,
    )
    logger.info(
        "Agente Linux (Ananuke) iniciado em %s:%d (Desktop: %s, Backend: %s, Mock: %s, TLS: %s)",
        args.host,
        args.port,
        server.desktop_env,
        server.audio_backend,
        server.mock_mode,
        bool(server.certfile),
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Agente Linux encerrado pelo usuário.")
        server.shutdown()


if __name__ == "__main__":
    main()
