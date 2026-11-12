#!/usr/bin/env python3
"""Despachante e Orquestrador de Comandos do Jarvis PC Control V1.

Responsabilidades:
- Verificar a Feature Flag JARVIS_PC_CONTROL_ENABLED (desativada por padrão).
- Gerenciar o fluxo de confirmação em duas etapas para fechar navegador e reiniciar.
- Garantir deduplicação e idempotência temporal entre fontes e requisições repetidas.
- Vincular estritamente a confirmação à mesma sessão, computador e ação.
- Fornecer respostas no contrato unificado exigido pelo sistema.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, Optional, Tuple

from pc_control.catalog import (
    get_friendly_action_name,
    get_friendly_target_name,
    is_pc_control_action,
    is_valid_confirmation,
    requires_confirmation,
)
from pc_control.client import PcControlClient
from pc_control.config import is_pc_control_enabled

logger = logging.getLogger("JarvisPcDispatcher")


class PcControlDispatcher:
    """Orquestrador thread-safe para comandos e confirmações do PC Control."""

    def __init__(
        self,
        client: Optional[PcControlClient] = None,
        dedup_window_s: float = 3.5,
        confirmation_timeout_s: float = 20.0,
    ) -> None:
        self.client = client or PcControlClient()
        self.dedup_window_s = dedup_window_s
        self.confirmation_timeout_s = confirmation_timeout_s

        self._lock = threading.RLock()
        self._recent_actions: Dict[Tuple[str, str], float] = {}
        self.pending_confirmation: Optional[Dict[str, Any]] = None

    def processar_comando_ou_confirmacao(
        self,
        acao: str,
        alvo: str,
        texto_falado: str,
        session_id: Optional[str],
        event_id: str,
        fonte: str = "me6s",
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Ponto de entrada para processamento de intenções de controle de PC."""
        with self._lock:
            # 1. Verificação da Feature Flag (desativada por padrão)
            if not is_pc_control_enabled():
                logger.info(
                    "[PC-CONTROL-DESATIVADO] Ação '%s' para '%s' ignorada pois JARVIS_PC_CONTROL_ENABLED=0.",
                    acao,
                    alvo,
                )
                nome_alvo = get_friendly_target_name(alvo)
                return {
                    "sucesso": False,
                    "event_id": event_id,
                    "session_id": session_id,
                    "origem": fonte,
                    "computador_alvo": alvo,
                    "acao": acao,
                    "estado": "cancelado",
                    "status": "cancelado",
                    "erro": "pc_control_desativado",
                    "resultado": {},
                    "mensagem_amigavel": f"Controle de PC para {nome_alvo} desativado por configuração.",
                    "processado": True,
                }

            # 2. Tratamento de Confirmação Pendente
            if self.pending_confirmation is not None:
                pend = self.pending_confirmation
                agora = time.monotonic()

                # 2.1 Verificação de timeout da confirmação
                if agora > pend.get("limite", 0.0):
                    self.pending_confirmation = None
                    return {
                        "sucesso": False,
                        "event_id": event_id,
                        "session_id": session_id,
                        "origem": fonte,
                        "computador_alvo": pend.get("alvo"),
                        "acao": pend.get("acao"),
                        "estado": "falha",
                        "status": "falha",
                        "erro": "confirmacao_expirada",
                        "resultado": {},
                        "mensagem_amigavel": "Tempo esgotado para confirmação.",
                        "processado": True,
                    }

                # 2.2 Verificação estrita de session_id (sessão obrigatória e idêntica)
                if not session_id or not pend.get("session_id") or session_id != pend["session_id"]:
                    logger.warning(
                        "Confirmação rejeitada por divergência de sessão: recebido %s, esperado %s",
                        session_id,
                        pend.get("session_id"),
                    )
                    return {
                        "sucesso": False,
                        "event_id": event_id,
                        "session_id": session_id,
                        "origem": fonte,
                        "computador_alvo": pend.get("alvo"),
                        "acao": pend.get("acao"),
                        "estado": "falha",
                        "status": "falha",
                        "erro": "session_id_divergente",
                        "resultado": {},
                        "mensagem_amigavel": "Sessão inválida para esta confirmação.",
                        "processado": False,
                    }

                # 2.3 Verificação estrita de computador-alvo (se informado na confirmação)
                if alvo and alvo.lower() != pend.get("alvo", "").lower():
                    logger.warning(
                        "Confirmação rejeitada por divergência de computador-alvo: recebido %s, esperado %s",
                        alvo,
                        pend.get("alvo"),
                    )
                    return {
                        "sucesso": False,
                        "event_id": event_id,
                        "session_id": session_id,
                        "origem": fonte,
                        "computador_alvo": pend.get("alvo"),
                        "acao": pend.get("acao"),
                        "estado": "falha",
                        "status": "falha",
                        "erro": "computador_alvo_divergente",
                        "resultado": {},
                        "mensagem_amigavel": "Computador-alvo incompatível com a confirmação pendente.",
                        "processado": False,
                    }

                # 2.4 Cancelamento explícito
                if texto_falado == "cancelar":
                    self.pending_confirmation = None
                    nome_alvo = get_friendly_target_name(pend.get("alvo", ""))
                    return {
                        "sucesso": True,
                        "event_id": event_id,
                        "session_id": session_id,
                        "origem": fonte,
                        "computador_alvo": pend.get("alvo"),
                        "acao": "cancelar",
                        "estado": "cancelado",
                        "status": "cancelado",
                        "resultado": {},
                        "mensagem_amigavel": f"Operação cancelada no {nome_alvo}.",
                        "processado": True,
                    }

                # 2.5 Verificação estrita de ação na confirmação
                if acao != "confirmar" and is_pc_control_action(acao) and acao != pend.get("acao"):
                    logger.warning(
                        "Confirmação rejeitada por divergência de ação: recebido %s, esperado %s",
                        acao,
                        pend.get("acao"),
                    )
                    return {
                        "sucesso": False,
                        "event_id": event_id,
                        "session_id": session_id,
                        "origem": fonte,
                        "computador_alvo": pend.get("alvo"),
                        "acao": pend.get("acao"),
                        "estado": "falha",
                        "status": "falha",
                        "erro": "acao_divergente",
                        "resultado": {},
                        "mensagem_amigavel": "Ação incompatível com a confirmação pendente.",
                        "processado": False,
                    }

                # 2.6 Confirmação válida para a ação pendente
                if is_valid_confirmation(texto_falado, pend.get("acao")):
                    acao_confirmada = pend["acao"]
                    alvo_confirmado = pend["alvo"]
                    sid_confirmado = pend["session_id"]
                    self.pending_confirmation = None  # Consumo one-shot estrito

                    logger.info(
                        "Confirmação autorizada para '%s' em '%s' (sessão %s)",
                        acao_confirmada,
                        alvo_confirmado,
                        sid_confirmado,
                    )
                    # Despacha a ação confirmada para o agente
                    resp = self.client.enviar_comando(
                        alvo=alvo_confirmado,
                        acao=acao_confirmada,
                        event_id=event_id,
                        session_id=sid_confirmado,
                        fonte=fonte,
                        params=params,
                    )
                    resp["sucesso"] = resp.get("status") == "concluido"
                    resp["processado"] = True
                    return resp
                else:
                    logger.warning("Confirmação incompatível para ação pendente '%s': '%s'", pend.get("acao"), texto_falado)
                    return {
                        "sucesso": False,
                        "event_id": event_id,
                        "session_id": session_id,
                        "origem": fonte,
                        "computador_alvo": pend.get("alvo"),
                        "acao": pend.get("acao"),
                        "estado": "falha",
                        "status": "falha",
                        "erro": "confirmacao_insuficiente",
                        "resultado": {},
                        "mensagem_amigavel": "Confirmação incompatível com a operação pendente.",
                        "processado": False,
                    }

            # Se for tentativa de confirmação mas NÃO há confirmação pendente válida (rejeitar confirmação sem sessão)
            if acao == "confirmar" or (texto_falado and is_valid_confirmation(texto_falado)):
                logger.warning("Tentativa de confirmação sem sessão pendente válida: '%s'", texto_falado)
                return {
                    "sucesso": False,
                    "event_id": event_id,
                    "session_id": session_id,
                    "origem": fonte,
                    "computador_alvo": alvo,
                    "acao": acao,
                    "estado": "falha",
                    "status": "falha",
                    "erro": "nenhuma_confirmacao_pendente",
                    "resultado": {},
                    "mensagem_amigavel": "Nenhuma operação pendente para confirmação.",
                    "processado": False,
                }

            # 3. Tratamento de nova ação
            if requires_confirmation(acao):
                # Rejeitar comandos que requerem confirmação sem session_id válido
                if not session_id:
                    logger.warning("Comando que requer confirmação rejeitado por ausência de session_id: %s em %s", acao, alvo)
                    return {
                        "sucesso": False,
                        "event_id": event_id,
                        "session_id": None,
                        "origem": fonte,
                        "computador_alvo": alvo,
                        "acao": acao,
                        "estado": "falha",
                        "status": "falha",
                        "erro": "sessao_obrigatoria_para_confirmacao",
                        "resultado": {},
                        "mensagem_amigavel": "Comando rejeitado: sessão de voz necessária para confirmação.",
                        "processado": False,
                    }

                # Armazena estado de confirmação pendente vinculado estritamente à sessão, computador e ação
                self.pending_confirmation = {
                    "acao": acao,
                    "alvo": alvo,
                    "session_id": session_id,
                    "event_id": event_id,
                    "fonte": fonte,
                    "limite": time.monotonic() + self.confirmation_timeout_s,
                }
                nome_alvo = get_friendly_target_name(alvo)
                msg_aviso = (
                    f"Confirme fechar navegador em {nome_alvo}: diga 'confirmar' ou 'cancelar'."
                    if acao == "fechar_navegador"
                    else f"Confirme reinicialização de {nome_alvo}: diga 'confirmar' ou 'cancelar'."
                )
                return {
                    "sucesso": True,
                    "event_id": event_id,
                    "session_id": session_id,
                    "origem": fonte,
                    "computador_alvo": alvo,
                    "acao": acao,
                    "estado": "confirmacao_pendente",
                    "status": "confirmacao_pendente",
                    "resultado": {"confirmacao_requerida": True},
                    "erro": None,
                    "mensagem_amigavel": msg_aviso,
                    "processado": True,
                }

            # 4. Deduplicação e Idempotência para ações imediatas
            agora_m = time.monotonic()
            delta = agora_m - self._recent_actions.get((acao, alvo), 0.0)
            if delta <= self.dedup_window_s:
                logger.info("Ação '%s' em '%s' deduplicada por idempotência (%.2fs atrás).", acao, alvo, delta)
                nome_alvo = get_friendly_target_name(alvo)
                nome_acao = get_friendly_action_name(acao)
                return {
                    "sucesso": True,
                    "event_id": event_id,
                    "session_id": session_id,
                    "origem": fonte,
                    "computador_alvo": alvo,
                    "acao": acao,
                    "estado": "concluido",
                    "status": "concluido",
                    "deduplicado": True,
                    "resultado": {"idempotente": True},
                    "erro": None,
                    "mensagem_amigavel": f"{nome_acao} já executado em {nome_alvo}.",
                    "processado": True,
                }

            self._recent_actions[(acao, alvo)] = agora_m

            # 5. Despacho imediato para o agente local
            resp = self.client.enviar_comando(
                alvo=alvo,
                acao=acao,
                event_id=event_id,
                session_id=session_id,
                fonte=fonte,
                params=params,
            )
            resp["sucesso"] = resp.get("status") == "concluido"
            resp["processado"] = True
            return resp

    def cancelar_pendente(self) -> Optional[Dict[str, Any]]:
        """Cancela explicitamente qualquer confirmação pendente."""
        with self._lock:
            pend = self.pending_confirmation
            self.pending_confirmation = None
            return pend
