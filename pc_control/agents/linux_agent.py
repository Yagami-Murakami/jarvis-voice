#!/usr/bin/env python3
"""Agente Local do Jarvis PC Control para Linux (Computador Ananuke - Debian 13 / Xfce - 192.168.0.131).

Projetado para rodar na sessão de usuário do Xfce (não executa como root para GUI).
Verifica automaticamente se o backend de áudio é PipeWire (wpctl), PulseAudio (pactl) ou ALSA (amixer).
Permite apenas os 11 comandos autorizados na allowlist estrita.
"""
from __future__ import annotations

import argparse
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

from pc_control.agents.base_agent import PcAgentServer
from pc_control.config import get_agent_token, is_mock_mode

logger = logging.getLogger("JarvisLinuxAgent")

# Processos comuns de navegadores no Debian para fechamento normal (SIGTERM)
LINUX_BROWSER_PROCESSES = ["firefox-bin", "firefox", "chromium", "chrome", "brave"]


class LinuxPcAgent(PcAgentServer):
    """Agente de controle local para Debian 13 com Xfce (Ananuke)."""

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8092,
        auth_token: Optional[str] = None,
        mock_mode: bool = False,
    ) -> None:
        token = auth_token or get_agent_token()
        self.mock_mode = mock_mode or is_mock_mode()
        self.audio_backend = self._detectar_backend_audio()
        # Nível de volume simulado em testes (0.0 a 1.0)
        self._sim_volume = 0.50
        self._sim_muted = False
        super().__init__(host, port, target_id="ananuke", auth_token=token)

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

    # 1. Abrir navegador
    def _acao_abrir_navegador(self, params: Dict[str, Any]) -> Tuple[bool, Dict[str, Any], Optional[str], str]:
        url = "about:blank"
        if not self.mock_mode:
            env = self._obter_env_sessao()
            cmd = None
            if shutil.which("exo-open"):
                cmd = ["exo-open", "--launch", "WebBrowser", url]
            elif shutil.which("xdg-open"):
                cmd = ["xdg-open", url]
            elif shutil.which("x-www-browser"):
                cmd = ["x-www-browser", url]

            if not cmd:
                return False, {}, "navegador_nao_encontrado", "Nenhum navegador padrão encontrado no sistema."

            try:
                subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception as e:
                return False, {}, f"erro_abrir_navegador: {e}", "Falha ao abrir navegador no Ananuke."

        return True, {"url": url, "simulado": self.mock_mode}, None, "Navegador padrão aberto no Ananuke."

    # 2. Abrir YouTube
    def _acao_abrir_youtube(self, params: Dict[str, Any]) -> Tuple[bool, Dict[str, Any], Optional[str], str]:
        url = "https://www.youtube.com"
        if not self.mock_mode:
            env = self._obter_env_sessao()
            cmd = ["xdg-open", url] if shutil.which("xdg-open") else ["exo-open", "--launch", "WebBrowser", url]
            try:
                subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception as e:
                return False, {}, f"erro_abrir_youtube: {e}", "Falha ao abrir YouTube no Ananuke."

        return True, {"url": url, "simulado": self.mock_mode}, None, "YouTube aberto no Ananuke."

    # 3. Bloquear computador (sessão Xfce)
    def _acao_bloquear(self, params: Dict[str, Any]) -> Tuple[bool, Dict[str, Any], Optional[str], str]:
        if not self.mock_mode:
            env = self._obter_env_sessao()
            cmd = None
            if shutil.which("xflock4"):
                cmd = ["xflock4"]
            elif shutil.which("xfce4-screensaver-command"):
                cmd = ["xfce4-screensaver-command", "-l"]
            elif shutil.which("xdg-screensaver"):
                cmd = ["xdg-screensaver", "lock"]
            elif shutil.which("loginctl"):
                cmd = ["loginctl", "lock-session"]

            if not cmd:
                return False, {}, "bloqueador_nao_encontrado", "Nenhuma ferramenta de bloqueio de sessão encontrada."

            try:
                subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception as e:
                return False, {}, f"erro_bloqueio: {e}", "Falha ao bloquear a sessão no Ananuke."

        return True, {"bloqueado": True, "simulado": self.mock_mode}, None, "Computador Ananuke bloqueado com sucesso."

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

    # 8. Abrir terminal (xfce4-terminal)
    def _acao_abrir_terminal(self, params: Dict[str, Any]) -> Tuple[bool, Dict[str, Any], Optional[str], str]:
        terminal_cmd = "xfce4-terminal"
        if not self.mock_mode:
            env = self._obter_env_sessao()
            cmd = ["xfce4-terminal"] if shutil.which("xfce4-terminal") else ["x-terminal-emulator"]
            try:
                subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception as e:
                return False, {}, f"erro_terminal: {e}", "Falha ao abrir xfce4-terminal no Ananuke."

        return True, {"terminal": terminal_cmd, "simulado": self.mock_mode}, None, "Terminal aberto no Ananuke."

    # 9. Fechar navegador (após confirmação)
    def _acao_fechar_navegador(self, params: Dict[str, Any]) -> Tuple[bool, Dict[str, Any], Optional[str], str]:
        fechados = []
        if not self.mock_mode:
            # 1. Tenta fechar graciosamente por janela via wmctrl
            if shutil.which("wmctrl"):
                env = self._obter_env_sessao()
                for prog in ("Mozilla Firefox", "Chromium", "Google Chrome", "Brave"):
                    try:
                        subprocess.run(["wmctrl", "-c", prog], env=env, capture_output=True, timeout=2)
                    except Exception:
                        pass

            # 2. Envia SIGTERM (término normal gracioso, NÃO SIGKILL 9) aos processos do usuário
            for nome_proc in LINUX_BROWSER_PROCESSES:
                try:
                    # pkill -15 envia SIGTERM para processos pertencentes ao usuário corrente
                    res = subprocess.run(["pkill", "-15", "-u", str(os.getuid()), nome_proc], capture_output=True)
                    if res.returncode == 0:
                        fechados.append(nome_proc)
                except Exception:
                    pass
        else:
            fechados = ["firefox (simulado)"]

        return True, {"processos_fechados": fechados, "simulado": self.mock_mode}, None, "Navegador fechado no Ananuke."

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

    # 11. Mostrar área de trabalho (Xfce / EWMH)
    def _acao_mostrar_area_de_trabalho(self, params: Dict[str, Any]) -> Tuple[bool, Dict[str, Any], Optional[str], str]:
        if not self.mock_mode:
            env = self._obter_env_sessao()
            executou = False

            # 1. wmctrl -k on ativa o modo "Mostrar Área de Trabalho" no Xfce
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

            # 3. Fallback nativo via EWMH libX11 (sem necessidade de pacotes externos)
            if not executou:
                executou = self._mostrar_desktop_x11()

            if not executou:
                return False, {}, "ferramenta_desktop_ausente", "Ferramenta para alternar desktop não encontrada."

        return True, {"area_de_trabalho": True, "simulado": self.mock_mode}, None, "Área de trabalho exibida no Ananuke."

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
    parser.add_argument("--host", default="0.0.0.0", help="Endereço de bind (padrão: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8092, help="Porta HTTP (padrão: 8092)")
    parser.add_argument("--token", default=None, help="Token compartilhado de autenticação")
    parser.add_argument("--mock", action="store_true", help="Forçar modo simulado de ações")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] [%(levelname)s] [LinuxAgent] %(message)s",
    )
    server = LinuxPcAgent(host=args.host, port=args.port, auth_token=args.token, mock_mode=args.mock)
    logger.info("Agente Linux (Ananuke) iniciado em %s:%d (Backend: %s, Mock: %s)", args.host, args.port, server.audio_backend, server.mock_mode)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Agente Linux encerrado pelo usuário.")
        server.shutdown()


if __name__ == "__main__":
    main()
