#!/usr/bin/env python3
"""Configurações e Feature Flags do Jarvis PC Control V1.

Por padrão seguro, o módulo permanece desativado (JARVIS_PC_CONTROL_ENABLED=0)
e não interfere nas variáveis existentes de WoL e SSH.
"""
from __future__ import annotations

import os


from typing import Optional, Set


def is_pc_control_enabled() -> bool:
    """Retorna True se o controle de PC estiver ativado via feature flag."""
    return os.getenv("JARVIS_PC_CONTROL_ENABLED", "0") == "1"


def get_agent_url(target: str) -> str:
    """Retorna a URL HTTP/HTTPS configurada para o agente do computador-alvo."""
    t = target.lower()
    if t == "fusion":
        # Suporta JARVIS_PC_FUSION_URL (padrão documentado) e JARVIS_FUSION_AGENT_URL (retrocompatível)
        return os.getenv("JARVIS_PC_FUSION_URL") or os.getenv("JARVIS_FUSION_AGENT_URL") or "http://192.168.0.128:8092"
    if t == "ananuke":
        # Suporta JARVIS_PC_ANANUKE_URL (padrão documentado) e JARVIS_ANANUKE_AGENT_URL (retrocompatível)
        return os.getenv("JARVIS_PC_ANANUKE_URL") or os.getenv("JARVIS_ANANUKE_AGENT_URL") or "http://192.168.0.131:8092"
    raise ValueError(f"Computador alvo desconhecido para PC Control: {target}")


def get_agent_token(target: Optional[str] = None) -> str:
    """Retorna o segredo para autenticação HMAC dos agentes locais.

    Exige credencial configurada explicitamente por variável de ambiente.
    Dá preferência a credenciais individuais por computador:
    - Fusion: JARVIS_PC_FUSION_TOKEN
    - Ananuke: JARVIS_PC_ANANUKE_TOKEN
    Fallback para credencial compartilhada:
    - JARVIS_PC_AGENT_TOKEN ou JARVIS_PC_CONTROL_TOKEN.

    NUNCA retorna segredo padrão de desenvolvimento embutido.
    Se não configurado, retorna string vazia (fail-closed seguro).
    """
    if target:
        t = target.lower()
        if t == "fusion":
            tok = os.getenv("JARVIS_PC_FUSION_TOKEN")
            if tok:
                return tok.strip()
        elif t == "ananuke":
            tok = os.getenv("JARVIS_PC_ANANUKE_TOKEN")
            if tok:
                return tok.strip()

    # Fallback para token compartilhado explícito
    tok_shared = os.getenv("JARVIS_PC_AGENT_TOKEN") or os.getenv("JARVIS_PC_CONTROL_TOKEN")
    if tok_shared:
        return tok_shared.strip()

    return ""


def get_agent_timeout() -> float:
    """Retorna o tempo limite em segundos para requisições aos agentes locais."""
    val = os.getenv("JARVIS_PC_HTTP_TIMEOUT") or os.getenv("JARVIS_PC_CONTROL_TIMEOUT")
    if val:
        try:
            return float(val)
        except (ValueError, TypeError):
            pass
    return 2.5


DEFAULT_ALLOWED_IPS = {"127.0.0.1", "::1", "192.168.0.126"}


def get_allowed_client_ips() -> Set[str]:
    """Retorna o conjunto de IPs autorizados para conectar aos agentes locais.

    Por padrão de segurança defensiva, restringe a porta 8092 estritamente ao
    controlador central (192.168.0.126) e localhost (127.0.0.1, ::1).
    Caso JARVIS_PC_ALLOWED_IPS seja definido, utiliza a lista especificada.
    """
    raw = os.getenv("JARVIS_PC_ALLOWED_IPS")
    if raw:
        return {ip.strip() for ip in raw.split(",") if ip.strip()}
    return set(DEFAULT_ALLOWED_IPS)


def is_mock_mode() -> bool:
    """Retorna True se os agentes devem operar em modo simulado estrito."""
    return os.getenv("JARVIS_MOCK_ACTIONS", "0") == "1" or os.getenv("JARVIS_PC_MOCK_MODE", "0") == "1"

