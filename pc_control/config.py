#!/usr/bin/env python3
"""Configurações e Feature Flags do Jarvis PC Control V1.

Por padrão seguro, o módulo permanece desativado (JARVIS_PC_CONTROL_ENABLED=0)
e não interfere nas variáveis existentes de WoL e SSH.
"""
from __future__ import annotations

import os


def is_pc_control_enabled() -> bool:
    """Retorna True se o controle de PC estiver ativado via feature flag."""
    return os.getenv("JARVIS_PC_CONTROL_ENABLED", "0") == "1"


def get_agent_url(target: str) -> str:
    """Retorna a URL HTTP/HTTPS configurada para o agente do computador-alvo."""
    t = target.lower()
    if t == "fusion":
        return os.getenv("JARVIS_FUSION_AGENT_URL", "http://192.168.0.128:8092")
    if t == "ananuke":
        return os.getenv("JARVIS_ANANUKE_AGENT_URL", "http://192.168.0.131:8092")
    raise ValueError(f"Computador alvo desconhecido para PC Control: {target}")


def get_agent_token() -> str:
    """Retorna o segredo compartilhado para autenticação HMAC dos agentes locais."""
    return os.getenv("JARVIS_PC_CONTROL_TOKEN", "jarvis-pc-control-secret-v1-dev")


def get_agent_timeout() -> float:
    """Retorna o tempo limite em segundos para requisições aos agentes locais."""
    try:
        return float(os.getenv("JARVIS_PC_CONTROL_TIMEOUT", "4.0"))
    except (ValueError, TypeError):
        return 4.0


def is_mock_mode() -> bool:
    """Retorna True se os agentes devem operar em modo simulado estrito."""
    return os.getenv("JARVIS_MOCK_ACTIONS", "0") == "1"
