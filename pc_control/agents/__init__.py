"""Agentes Locais do Jarvis PC Control para Windows e Linux."""
from __future__ import annotations

from typing import TYPE_CHECKING

from pc_control.agents.base_agent import PcAgentServer

if TYPE_CHECKING:
    from pc_control.agents.linux_agent import LinuxPcAgent
    from pc_control.agents.windows_agent import WindowsPcAgent

__all__ = ["PcAgentServer", "WindowsPcAgent", "LinuxPcAgent"]


def __getattr__(name: str):
    if name == "WindowsPcAgent":
        from pc_control.agents.windows_agent import WindowsPcAgent
        return WindowsPcAgent
    if name == "LinuxPcAgent":
        from pc_control.agents.linux_agent import LinuxPcAgent
        return LinuxPcAgent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

