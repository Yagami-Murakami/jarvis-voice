#!/usr/bin/env python3
"""Cliente HTTP Autenticado do Jarvis para os Agentes PC Control.

Envia requisições assinadas via HMAC-SHA256 para os agentes locais (Fusion/Ananuke),
trata timeouts e computadores inacessíveis e retorna o contrato de resposta padronizado.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import ssl
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

from pc_control.catalog import get_friendly_action_name, get_friendly_target_name
from pc_control.config import get_agent_timeout, get_agent_token, get_agent_url
from pc_control.security import calcular_assinatura, is_strong_token

logger = logging.getLogger("JarvisPcClient")


class PcControlClient:
    """Cliente HTTP com assinatura HMAC e timeout rigoroso para os agentes de PC."""

    def __init__(
        self,
        token: Optional[str] = None,
        tokens_por_alvo: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
        url_overrides: Optional[Dict[str, str]] = None,
        ca_cert: Optional[str] = None,
    ) -> None:
        self.token = token
        self.tokens_por_alvo = {k.lower(): v for k, v in (tokens_por_alvo or {}).items()}
        self.timeout = timeout or get_agent_timeout()
        self.url_overrides = url_overrides or {}
        self.ca_cert = ca_cert or os.getenv("JARVIS_PC_CA_CERT")

    def _obter_token(self, alvo: str) -> str:
        alvo_lower = alvo.lower()
        if alvo_lower in self.tokens_por_alvo:
            return self.tokens_por_alvo[alvo_lower]
        if self.token:
            return self.token
        return get_agent_token(alvo_lower)

    def _obter_url(self, alvo: str) -> str:
        alvo_lower = alvo.lower()
        if alvo_lower in self.url_overrides:
            return self.url_overrides[alvo_lower]
        return get_agent_url(alvo_lower)

    def enviar_comando(
        self,
        alvo: str,
        acao: str,
        event_id: str,
        session_id: Optional[str] = None,
        fonte: str = "me6s",
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Transmite comando autenticado para o agente local e padroniza a resposta."""
        alvo_normalizado = alvo.lower()
        nome_alvo = get_friendly_target_name(alvo_normalizado)
        nome_acao = get_friendly_action_name(acao)

        # 1. Validação de credencial forte obrigatória (fail-closed)
        token_efetivo = self._obter_token(alvo_normalizado)
        if not token_efetivo or not is_strong_token(token_efetivo):
            logger.warning("Falha de configuração de segurança: credencial forte ausente ou inválida para alvo %s", alvo_normalizado)
            return {
                "event_id": event_id,
                "session_id": session_id,
                "origem": fonte,
                "computador_alvo": alvo_normalizado,
                "acao": acao,
                "estado": "falha",
                "status": "falha",
                "resultado": {},
                "erro": "configuracao_incompleta_credencial_invalida_ou_ausente",
                "mensagem_amigavel": f"Configuração de segurança incompleta para {nome_alvo}.",
                "timestamp": time.time(),
            }

        try:
            base_url = self._obter_url(alvo_normalizado)
        except Exception as e:
            return {
                "event_id": event_id,
                "session_id": session_id,
                "origem": fonte,
                "computador_alvo": alvo_normalizado,
                "acao": acao,
                "estado": "falha",
                "status": "falha",
                "resultado": {},
                "erro": f"url_invalida: {e}",
                "mensagem_amigavel": f"Configuração de rede inválida para {nome_alvo}.",
                "timestamp": time.time(),
            }

        endpoint = f"{base_url.rstrip('/')}/execute"
        agora = time.time()
        nonce = f"nonce_{event_id}_{secrets.token_hex(4)}"

        payload_dict = {
            "event_id": event_id,
            "session_id": session_id,
            "target": alvo_normalizado,
            "action": acao,
            "params": params or {},
            "timestamp": agora,
            "nonce": nonce,
        }
        body_bytes = json.dumps(payload_dict, ensure_ascii=False).encode("utf-8")

        assinatura = calcular_assinatura(
            token=token_efetivo,
            timestamp=agora,
            nonce=nonce,
            target=alvo_normalizado,
            action=acao,
            body_bytes=body_bytes,
        )

        # Cabeçalhos estritos: JAMAIS transmitir o segredo em cabeçalhos HTTP
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "X-Jarvis-Signature": assinatura,
            "X-Jarvis-Timestamp": f"{agora:.4f}",
            "X-Jarvis-Nonce": nonce,
            "User-Agent": "JarvisController/1.0",
        }

        req = urllib.request.Request(endpoint, data=body_bytes, headers=headers, method="POST")

        # Contexto SSL para conexões HTTPS com verificação de certificados
        ssl_ctx = None
        if endpoint.lower().startswith("https://"):
            if self.ca_cert:
                if not os.path.exists(self.ca_cert) or not os.path.isfile(self.ca_cert):
                    return {
                        "event_id": event_id,
                        "session_id": session_id,
                        "origem": fonte,
                        "computador_alvo": alvo_normalizado,
                        "acao": acao,
                        "estado": "falha",
                        "status": "falha",
                        "resultado": {},
                        "erro": f"ca_cert_nao_encontrado: {self.ca_cert}",
                        "mensagem_amigavel": f"Falha de segurança TLS: Certificado CA não encontrado para {nome_alvo}.",
                        "timestamp": time.time(),
                    }
                try:
                    ssl_ctx = ssl.create_default_context(cafile=self.ca_cert)
                except Exception as e:
                    return {
                        "event_id": event_id,
                        "session_id": session_id,
                        "origem": fonte,
                        "computador_alvo": alvo_normalizado,
                        "acao": acao,
                        "estado": "falha",
                        "status": "falha",
                        "resultado": {},
                        "erro": f"ca_cert_invalido: {e}",
                        "mensagem_amigavel": f"Falha de segurança TLS: Certificado CA inválido para {nome_alvo}.",
                        "timestamp": time.time(),
                    }
            elif os.getenv("JARVIS_PC_TLS_INSECURE_SKIP_VERIFY") == "1":
                logger.warning(
                    "ALERTA DE SEGURANÇA: JARVIS_PC_TLS_INSECURE_SKIP_VERIFY=1 ativo. "
                    "Verificação de certificado HTTPS desativada temporariamente. "
                    "NÃO utilizar em produção!"
                )
                ssl_ctx = ssl._create_unverified_context()
            else:
                ssl_ctx = ssl.create_default_context()

        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=ssl_ctx) as resp:
                status_http = resp.status
                resp_bytes = resp.read()
                dados_resp = json.loads(resp_bytes.decode("utf-8"))

                st = dados_resp.get("status", "concluido")
                msg_amigavel = dados_resp.get("mensagem_amigavel") or f"{nome_acao} executado em {nome_alvo}."

                return {
                    "event_id": event_id,
                    "session_id": session_id,
                    "origem": fonte,
                    "computador_alvo": alvo_normalizado,
                    "acao": acao,
                    "estado": st,
                    "status": st,
                    "resultado": dados_resp.get("resultado", {}),
                    "erro": dados_resp.get("erro"),
                    "mensagem_amigavel": msg_amigavel,
                    "timestamp": dados_resp.get("timestamp", time.time()),
                }

        except urllib.error.HTTPError as http_err:
            detalhe = ""
            try:
                corpo_erro = json.loads(http_err.read().decode("utf-8"))
                detalhe = corpo_erro.get("erro", str(http_err))
            except Exception:
                detalhe = str(http_err)

            logger.warning("Agente retornou erro HTTP %d para %s (%s): %s", http_err.code, alvo_normalizado, acao, detalhe)
            return {
                "event_id": event_id,
                "session_id": session_id,
                "origem": fonte,
                "computador_alvo": alvo_normalizado,
                "acao": acao,
                "estado": "falha",
                "status": "falha",
                "resultado": {},
                "erro": f"http_error_{http_err.code}: {detalhe}",
                "mensagem_amigavel": f"Falha ao executar {nome_acao} em {nome_alvo} ({detalhe}).",
                "timestamp": time.time(),
            }

        except (urllib.error.URLError, TimeoutError, OSError) as net_err:
            is_ssl = (
                isinstance(net_err, ssl.SSLError)
                or isinstance(getattr(net_err, "reason", None), ssl.SSLError)
                or "certificate verify failed" in str(net_err).lower()
                or "ssl" in str(net_err).lower()
            )
            eh_timeout = isinstance(net_err, TimeoutError) or "timed out" in str(net_err).lower()

            if is_ssl:
                erro_tag = "falha_tls_verificacao"
                msg_texto = f"Falha de segurança TLS: certificado inválido ou não confiável para {nome_alvo}."
            elif eh_timeout:
                erro_tag = "timeout_comunicacao_agente"
                msg_texto = f"Tempo esgotado ao contatar {nome_alvo}."
            else:
                erro_tag = "computador_offline_ou_inacessivel"
                msg_texto = f"Computador {nome_alvo} está desligado ou com agente inacessível."

            logger.info("Falha de rede ao conectar ao agente %s: %s (%s)", alvo_normalizado, net_err, erro_tag)
            return {
                "event_id": event_id,
                "session_id": session_id,
                "origem": fonte,
                "computador_alvo": alvo_normalizado,
                "acao": acao,
                "estado": "falha",
                "status": "falha",
                "resultado": {},
                "erro": erro_tag,
                "mensagem_amigavel": msg_texto,
                "timestamp": time.time(),
            }
        except Exception as e:
            logger.error("Erro inesperado no cliente PC Control: %s", e, exc_info=True)
            return {
                "event_id": event_id,
                "session_id": session_id,
                "origem": fonte,
                "computador_alvo": alvo_normalizado,
                "acao": acao,
                "estado": "falha",
                "status": "falha",
                "resultado": {},
                "erro": f"erro_cliente: {e}",
                "mensagem_amigavel": f"Erro inesperado ao solicitar {nome_acao} em {nome_alvo}.",
                "timestamp": time.time(),
            }
