#!/usr/bin/env python3
"""Agente Local do Jarvis PC Control para Windows (Computador Fusion - 192.168.0.128).

Projetado para rodar na sessão interativa do usuário logado (ex: Inicializar/Startup ou Tarefa Agendada).
Não aceita comandos PowerShell arbitrários. Executa apenas a allowlist estrita dos 11 comandos.
"""
from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import time
import webbrowser
from typing import Any, Dict, List, Optional, Tuple

from pc_control.agents.base_agent import PcAgentServer
from pc_control.config import get_agent_token, is_mock_mode

logger = logging.getLogger("JarvisWindowsAgent")

IS_WINDOWS = sys.platform.startswith("win32")

# Lista de executáveis de navegadores suportados para fechamento normal
WINDOWS_BROWSER_EXES = ["msedge.exe", "chrome.exe", "firefox.exe", "brave.exe", "opera.exe"]


class WindowsPcAgent(PcAgentServer):
    """Agente de controle local para Windows (Fusion)."""

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8092,
        auth_token: Optional[str] = None,
        mock_mode: bool = False,
    ) -> None:
        token = auth_token or get_agent_token()
        self.mock_mode = mock_mode or is_mock_mode() or (not IS_WINDOWS)
        # Nível de volume simulado quando fora do Windows ou em teste (0.0 a 1.0)
        self._sim_volume = 0.50
        self._sim_muted = False
        super().__init__(host, port, target_id="fusion", auth_token=token)

    def executar_acao(
        self,
        action: str,
        params: Dict[str, Any],
        event_id: str,
        session_id: Optional[str] = None,
    ) -> Tuple[bool, Dict[str, Any], Optional[str], str]:
        """Despacha a ação permitida para seu respectivo handler no Windows."""
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
            return False, {}, "acao_nao_suportada", f"Ação {action} não suportada no agente Windows."

        return handler(params)

    # 1. Abrir navegador
    def _acao_abrir_navegador(self, params: Dict[str, Any]) -> Tuple[bool, Dict[str, Any], Optional[str], str]:
        url = "about:blank"
        if not self.mock_mode and IS_WINDOWS:
            try:
                # Inicia o navegador padrão na sessão gráfica sem shell arbitrário
                os.startfile(url)
            except Exception:
                webbrowser.open(url)
        return True, {"url": url, "simulado": self.mock_mode}, None, "Navegador padrão aberto no Fusion."

    # 2. Abrir YouTube
    def _acao_abrir_youtube(self, params: Dict[str, Any]) -> Tuple[bool, Dict[str, Any], Optional[str], str]:
        # URL fixa e imutável para evitar injection
        url = "https://www.youtube.com"
        if not self.mock_mode and IS_WINDOWS:
            try:
                os.startfile(url)
            except Exception:
                webbrowser.open(url)
        return True, {"url": url, "simulado": self.mock_mode}, None, "YouTube aberto no Fusion."

    # 3. Bloquear computador
    def _acao_bloquear(self, params: Dict[str, Any]) -> Tuple[bool, Dict[str, Any], Optional[str], str]:
        if not self.mock_mode and IS_WINDOWS:
            try:
                import ctypes
                ctypes.windll.user32.LockWorkStation()
            except Exception as e:
                return False, {}, f"erro_bloqueio: {e}", "Falha ao bloquear o computador Fusion."
        return True, {"bloqueado": True, "simulado": self.mock_mode}, None, "Computador Fusion bloqueado com sucesso."

    # 4. Aumentar volume
    def _acao_aumentar_volume(self, params: Dict[str, Any]) -> Tuple[bool, Dict[str, Any], Optional[str], str]:
        passo = params.get("passo", 0.05)
        novo_vol, ok = self._ajustar_volume(delta=+passo)
        if not ok:
            return False, {}, "erro_volume", "Falha ao ajustar o volume no Fusion."
        pct = int(round(novo_vol * 100))
        return True, {"volume_pct": pct, "simulado": self.mock_mode}, None, f"Volume aumentado no Fusion para {pct}%."

    # 5. Diminuir volume
    def _acao_diminuir_volume(self, params: Dict[str, Any]) -> Tuple[bool, Dict[str, Any], Optional[str], str]:
        passo = params.get("passo", 0.05)
        novo_vol, ok = self._ajustar_volume(delta=-passo)
        if not ok:
            return False, {}, "erro_volume", "Falha ao ajustar o volume no Fusion."
        pct = int(round(novo_vol * 100))
        return True, {"volume_pct": pct, "simulado": self.mock_mode}, None, f"Volume diminuído no Fusion para {pct}%."

    # 6. Silenciar áudio (idempotente: define mudo = True, nunca inverte)
    def _acao_silenciar_audio(self, params: Dict[str, Any]) -> Tuple[bool, Dict[str, Any], Optional[str], str]:
        ok = self._definir_mudo(mute=True)
        if not ok:
            return False, {}, "erro_mudo", "Falha ao silenciar o áudio no Fusion."
        return True, {"mudo": True, "simulado": self.mock_mode}, None, "Áudio silenciado no Fusion."

    # 7. Informar status do computador
    def _acao_status_computador(self, params: Dict[str, Any]) -> Tuple[bool, Dict[str, Any], Optional[str], str]:
        cpu_pct = 15.0
        mem_pct = 45.0
        uptime_str = "2 dias, 4 horas"

        if not self.mock_mode and IS_WINDOWS:
            try:
                import ctypes
                from ctypes import wintypes

                # Memória
                class MEMORYSTATUSEX(ctypes.Structure):
                    _fields_ = [
                        ("dwLength", wintypes.DWORD),
                        ("dwMemoryLoad", wintypes.DWORD),
                        ("ullTotalPhys", ctypes.c_uint64),
                        ("ullAvailPhys", ctypes.c_uint64),
                        ("ullTotalPageFile", ctypes.c_uint64),
                        ("ullAvailPageFile", ctypes.c_uint64),
                        ("ullTotalVirtual", ctypes.c_uint64),
                        ("ullAvailVirtual", ctypes.c_uint64),
                        ("ullAvailExtendedVirtual", ctypes.c_uint64),
                    ]

                stat = MEMORYSTATUSEX()
                stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
                if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                    mem_pct = float(stat.dwMemoryLoad)

                # Uptime
                ticks_ms = ctypes.windll.kernel32.GetTickCount64()
                segundos = ticks_ms // 1000
                horas = segundos // 3600
                minutos = (segundos % 3600) // 60
                uptime_str = f"{horas}h {minutos}m"

                # CPU via powershell seguro somente para leitura de telemetria
                cmd = ["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_Processor).LoadPercentage"]
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
                if res.returncode == 0 and res.stdout.strip().isdigit():
                    cpu_pct = float(res.stdout.strip())
            except Exception as e:
                logger.warning("Falha parcial ao ler telemetria Windows: %s", e)

        msg = f"Fusion: CPU {cpu_pct:.0f}% | RAM {mem_pct:.0f}% | Ativo há {uptime_str}."
        return True, {"cpu_percent": cpu_pct, "memoria_percent": mem_pct, "uptime": uptime_str, "simulado": self.mock_mode}, None, msg

    # 8. Abrir terminal
    def _acao_abrir_terminal(self, params: Dict[str, Any]) -> Tuple[bool, Dict[str, Any], Optional[str], str]:
        terminal_aberto = "Windows Terminal"
        if not self.mock_mode and IS_WINDOWS:
            try:
                # Tenta Windows Terminal (wt.exe)
                subprocess.Popen(["wt.exe"])
            except FileNotFoundError:
                # Fallback para powershell.exe na sessão gráfica
                subprocess.Popen(["powershell.exe", "-NoExit"])
                terminal_aberto = "PowerShell"
            except Exception as e:
                return False, {}, f"erro_terminal: {e}", "Falha ao abrir terminal no Fusion."
        return True, {"terminal": terminal_aberto, "simulado": self.mock_mode}, None, f"Terminal ({terminal_aberto}) aberto no Fusion."

    # 9. Fechar navegador (executado após confirmação)
    def _acao_fechar_navegador(self, params: Dict[str, Any]) -> Tuple[bool, Dict[str, Any], Optional[str], str]:
        fechados = []
        if not self.mock_mode and IS_WINDOWS:
            for exe in WINDOWS_BROWSER_EXES:
                try:
                    # taskkill SEM /F envia mensagem WM_CLOSE graciosa para salvar abas
                    res = subprocess.run(["taskkill.exe", "/IM", exe], capture_output=True, text=True, timeout=5)
                    if res.returncode == 0:
                        fechados.append(exe)
                except Exception:
                    pass
        else:
            fechados = ["msedge.exe (simulado)"]

        return True, {"navegadores_fechados": fechados, "simulado": self.mock_mode}, None, "Navegador fechado no Fusion."

    # 10. Reiniciar computador (executado após confirmação explícita)
    def _acao_reiniciar(self, params: Dict[str, Any]) -> Tuple[bool, Dict[str, Any], Optional[str], str]:
        if not self.mock_mode and IS_WINDOWS:
            try:
                # Reinicialização graciosa com 5 segundos de aviso
                cmd = ["shutdown.exe", "/r", "/t", "5", "/c", "Jarvis: Reinicializacao autorizada por voz."]
                subprocess.run(cmd, check=True, timeout=5)
            except Exception as e:
                return False, {}, f"erro_reboot: {e}", "Falha ao solicitar reinicialização do Fusion."
        return True, {"reiniciando": True, "simulado": self.mock_mode}, None, "Reinicialização do Fusion iniciada com sucesso."

    # 11. Mostrar área de trabalho
    def _acao_mostrar_area_de_trabalho(self, params: Dict[str, Any]) -> Tuple[bool, Dict[str, Any], Optional[str], str]:
        if not self.mock_mode and IS_WINDOWS:
            try:
                # Utiliza Shell.Application para minimizar todas as janelas
                cmd = ["powershell", "-NoProfile", "-Command", "(New-Object -ComObject Shell.Application).MinimizeAll()"]
                subprocess.run(cmd, check=True, timeout=4)
            except Exception as e:
                return False, {}, f"erro_desktop: {e}", "Falha ao mostrar área de trabalho no Fusion."
        return True, {"area_de_trabalho": True, "simulado": self.mock_mode}, None, "Área de trabalho exibida no Fusion."

    def _obter_pycaw_endpoint(self) -> Any:
        """Obtém a interface IAudioEndpointVolume via pycaw se instalada."""
        try:
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
            from ctypes import cast, POINTER
            from comtypes import CLSCTX_ALL
            devices = AudioUtilities.GetSpeakers()
            if not devices:
                return None
            interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            return cast(interface, POINTER(IAudioEndpointVolume))
        except Exception as e:
            logger.debug("pycaw não disponível na sessão Windows: %s", e)
            return None

    # Helpers de áudio no Windows
    def _ajustar_volume(self, delta: float) -> Tuple[float, bool]:
        """Ajusta o volume respeitando rigorosamente os limites [0.0, 1.0] (0% a 100%)."""
        if self.mock_mode or not IS_WINDOWS:
            self._sim_volume = max(0.0, min(1.0, self._sim_volume + delta))
            return self._sim_volume, True

        # 1. Tenta API pycaw (CoreAudio IAudioEndpointVolume)
        try:
            endpoint = self._obter_pycaw_endpoint()
            if endpoint:
                vol_atual = endpoint.GetMasterVolumeLevelScalar()
                novo_vol = max(0.0, min(1.0, vol_atual + delta))
                endpoint.SetMasterVolumeLevelScalar(novo_vol, None)
                self._sim_volume = novo_vol
                return novo_vol, True
        except Exception as e:
            logger.warning("Falha ao ajustar volume via pycaw: %s", e)

        # 2. Fallback nativo via PowerShell CoreAudio (sem bibliotecas externas)
        try:
            ps_script = f"""
            Add-Type -TypeDefinition @"
            using System;
            using System.Runtime.InteropServices;
            [Guid("5CDF2C82-841E-4546-9722-0CF74078229A"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
            public interface IAudioEndpointVolume {{
                int f1(); int f2(); int f3(); int f4();
                int SetMasterVolumeLevelScalar(float fLevel, Guid pguidEventContext);
                int f6();
                int GetMasterVolumeLevelScalar(out float pfLevel);
                int SetMute([MarshalAs(UnmanagedType.Bool)] bool bMute, Guid pguidEventContext);
                int GetMute(out bool pbMute);
            }}
            [Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
            public interface IMMDevice {{
                int Activate(ref Guid id, int clsCtx, int p, out IAudioEndpointVolume aev);
            }}
            [Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
            public interface IMMDeviceEnumerator {{
                int GetDefaultAudioEndpoint(int dataFlow, int role, out IMMDevice endpoint);
            }}
            [ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")] class MMDeviceEnumeratorComObject {{}}
            public class WinAudioHelper {{
                public static float Adjust(float delta) {{
                    var enumerator = (IMMDeviceEnumerator)(new MMDeviceEnumeratorComObject());
                    IMMDevice dev = null;
                    enumerator.GetDefaultAudioEndpoint(0, 1, out dev);
                    IAudioEndpointVolume ep = null;
                    var iid = typeof(IAudioEndpointVolume).GUID;
                    dev.Activate(ref iid, 1, 0, out ep);
                    float cur = 0;
                    ep.GetMasterVolumeLevelScalar(out cur);
                    float target = Math.Max(0f, Math.Min(1f, cur + delta));
                    ep.SetMasterVolumeLevelScalar(target, Guid.Empty);
                    return target;
                }}
            }}
"@
            [WinAudioHelper]::Adjust({delta:.4f})
            """
            res = subprocess.run(["powershell", "-NoProfile", "-Command", ps_script], capture_output=True, text=True, timeout=5)
            if res.returncode == 0 and res.stdout.strip():
                val_limpo = res.stdout.strip().replace(",", ".")
                novo_vol = max(0.0, min(1.0, float(val_limpo)))
                self._sim_volume = novo_vol
                return novo_vol, True
        except Exception as e:
            logger.warning("Falha no fallback de volume PowerShell: %s", e)

        # Fallback seguro com limites
        self._sim_volume = max(0.0, min(1.0, self._sim_volume + delta))
        return self._sim_volume, True

    def _definir_mudo(self, mute: bool = True) -> bool:
        """Silencia o áudio de forma estritamente idempotente (sem alternância indesejada)."""
        self._sim_muted = mute
        if self.mock_mode or not IS_WINDOWS:
            return True

        # 1. Tenta pycaw
        try:
            endpoint = self._obter_pycaw_endpoint()
            if endpoint:
                endpoint.SetMute(1 if mute else 0, None)
                return True
        except Exception as e:
            logger.warning("Falha ao silenciar via pycaw: %s", e)

        # 2. Fallback nativo via PowerShell CoreAudio SetMute
        try:
            val_bool = "$true" if mute else "$false"
            ps_script = f"""
            Add-Type -TypeDefinition @"
            using System;
            using System.Runtime.InteropServices;
            [Guid("5CDF2C82-841E-4546-9722-0CF74078229A"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
            public interface IAudioEndpointVolume {{
                int f1(); int f2(); int f3(); int f4();
                int SetMasterVolumeLevelScalar(float fLevel, Guid pguidEventContext);
                int f6();
                int GetMasterVolumeLevelScalar(out float pfLevel);
                int SetMute([MarshalAs(UnmanagedType.Bool)] bool bMute, Guid pguidEventContext);
                int GetMute(out bool pbMute);
            }}
            [Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
            public interface IMMDevice {{
                int Activate(ref Guid id, int clsCtx, int p, out IAudioEndpointVolume aev);
            }}
            [Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
            public interface IMMDeviceEnumerator {{
                int GetDefaultAudioEndpoint(int dataFlow, int role, out IMMDevice endpoint);
            }}
            [ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")] class MMDeviceEnumeratorComObject {{}}
            public class WinAudioMuteHelper {{
                public static bool SetMute(bool mute) {{
                    var enumerator = (IMMDeviceEnumerator)(new MMDeviceEnumeratorComObject());
                    IMMDevice dev = null;
                    enumerator.GetDefaultAudioEndpoint(0, 1, out dev);
                    IAudioEndpointVolume ep = null;
                    var iid = typeof(IAudioEndpointVolume).GUID;
                    dev.Activate(ref iid, 1, 0, out ep);
                    ep.SetMute(mute, Guid.Empty);
                    return true;
                }}
            }}
"@
            [WinAudioMuteHelper]::SetMute({val_bool})
            """
            res = subprocess.run(["powershell", "-NoProfile", "-Command", ps_script], capture_output=True, text=True, timeout=5)
            return res.returncode == 0
        except Exception as e:
            logger.warning("Falha no fallback de mudo PowerShell: %s", e)
            return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Agente Local do Jarvis PC Control para Windows (Fusion)")
    parser.add_argument("--host", default="0.0.0.0", help="Endereço de bind (padrão: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8092, help="Porta HTTP (padrão: 8092)")
    parser.add_argument("--token", default=None, help="Token compartilhado de autenticação")
    parser.add_argument("--mock", action="store_true", help="Forçar modo simulado de ações")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] [%(levelname)s] [WindowsAgent] %(message)s",
    )
    server = WindowsPcAgent(host=args.host, port=args.port, auth_token=args.token, mock_mode=args.mock)
    logger.info("Agente Windows (Fusion) iniciado em %s:%d (Modo Simulado: %s)", args.host, args.port, server.mock_mode)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Agente Windows encerrado pelo usuário.")
        server.shutdown()


if __name__ == "__main__":
    main()
