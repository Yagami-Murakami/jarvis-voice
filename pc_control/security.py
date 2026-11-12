#!/usr/bin/env python3
"""Mecanismos de Segurança, Autenticação e Proteção contra Replay do Jarvis PC Control.

Garante:
- Autenticação por HMAC-SHA256 com token pré-compartilhado.
- Validação temporal (timestamp drift máximo de 5.0s).
- Prevenção rigorosa contra Replay Attack por rastreamento de nonces.
- Validação estrita de allowlists para alvo e ação.
- Ausência total de shell arbitrário ou injeção de comandos.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import threading
import time
from typing import Dict, Optional, Tuple

from pc_control.catalog import ALLOWED_PC_ACTIONS, ALLOWED_TARGETS

logger = logging.getLogger("JarvisPCSecurity")

MAX_TIMESTAMP_DRIFT_S = 5.0
NONCE_EXPIRY_S = 60.0
MIN_TOKEN_LENGTH = 16

WEAK_DEFAULT_TOKENS = {
    "jarvis-pc-control-secret-v1-dev",
    "chave_compartilhada_secreta_aqui",
    "chave-compartilhada-secreta-aqui",
    "secret",
    "password",
    "12345678",
    "1234567890",
    "admin",
    "token",
}


def is_strong_token(token: Optional[str]) -> bool:
    """Verifica se o token atende aos critérios mínimos de segurança e entropia."""
    if not token or not isinstance(token, str):
        return False
    tok = token.strip()
    if len(tok) < MIN_TOKEN_LENGTH:
        return False
    if tok.lower() in WEAK_DEFAULT_TOKENS:
        return False
    return True


def calcular_assinatura(
    token: str,
    timestamp: float,
    nonce: str,
    target: str,
    action: str,
    body_bytes: bytes = b"",
) -> str:
    """Calcula HMAC-SHA256 para autenticação da requisição."""
    mensagem = f"{timestamp:.4f}:{nonce}:{target}:{action}:".encode("utf-8") + body_bytes
    return hmac.new(token.encode("utf-8"), mensagem, hashlib.sha256).hexdigest()


def verificar_assinatura(
    token: str,
    assinatura: str,
    timestamp: float,
    nonce: str,
    target: str,
    action: str,
    body_bytes: bytes = b"",
) -> bool:
    """Valida a assinatura HMAC em tempo constante."""
    if not token or not assinatura:
        return False
    esperada = calcular_assinatura(token, timestamp, nonce, target, action, body_bytes)
    return hmac.compare_digest(esperada.lower(), assinatura.lower())


class NonceCache:
    """Cache thread-safe para expiração e deduplicação de nonces contra Replay Attack."""

    def __init__(self, expiry_seconds: float = NONCE_EXPIRY_S) -> None:
        self.expiry_seconds = expiry_seconds
        self._nonces: Dict[str, float] = {}
        self._lock = threading.Lock()

    def registrar_ou_validar(self, nonce: str) -> bool:
        """Registra um nonce.

        Retorna True se o nonce for novo (válido); retorna False se já tiver
        sido visto recentemente (replay).
        """
        if not nonce:
            return False
        agora = time.time()
        with self._lock:
            # Limpa nonces expirados
            expirados = [n for n, ts in self._nonces.items() if agora - ts > self.expiry_seconds]
            for n in expirados:
                del self._nonces[n]

            if nonce in self._nonces:
                return False  # Replay detectado

            self._nonces[nonce] = agora
            return True

    def reset(self) -> None:
        """Limpa o cache (usado em testes)."""
        with self._lock:
            self._nonces.clear()


# Instância global do validador de nonces
_GLOBAL_NONCE_CACHE = NonceCache()


def validar_requisicao(
    token_esperado: str,
    target_esperado: str,
    target_recebido: str,
    action_recebida: str,
    timestamp: float,
    nonce: str,
    assinatura: str,
    body_bytes: bytes = b"",
    nonce_cache: Optional[NonceCache] = None,
    max_drift: float = MAX_TIMESTAMP_DRIFT_S,
) -> Tuple[bool, str]:
    """Realiza a validação completa de segurança para uma requisição de comando."""
    cache = nonce_cache or _GLOBAL_NONCE_CACHE

    # 0. Validação de credencial forte no agente (fail-closed)
    if not is_strong_token(token_esperado):
        return False, "configuracao_incompleta_credencial_invalida_ou_fraca"

    # 1. Validação estrita de computador-alvo
    if target_recebido not in ALLOWED_TARGETS:
        return False, "alvo_invalido"

    if target_recebido.lower() != target_esperado.lower():
        return False, f"alvo_incompativel: recebido {target_recebido}, esperado {target_esperado}"

    # 2. Allowlist estrita de ações
    if action_recebida not in ALLOWED_PC_ACTIONS:
        return False, f"acao_nao_permitida: {action_recebida}"

    # 3. Validação temporal contra ataques de atraso/replay
    agora = time.time()
    drift = abs(agora - timestamp)
    if drift > max_drift:
        return False, f"timestamp_expirado: drift={drift:.2f}s > {max_drift}s"

    # 4. Verificação de Replay por Nonce
    if not cache.registrar_ou_validar(nonce):
        return False, "nonce_reutilizado_replay_detectado"

    # 5. Verificação da assinatura HMAC
    if not verificar_assinatura(token_esperado, assinatura, timestamp, nonce, target_recebido, action_recebida, body_bytes):
        return False, "autenticacao_invalida_hmac_incorreto"

    return True, "ok"
