#!/usr/bin/env python3
"""Servidor HTTP Base para Agentes Locais do Jarvis PC Control.

Implementa:
- Autenticação por HMAC e headers de segurança.
- Endpoints padronizados /health e /execute.
- ThreadingHTTPServer para permitir execução concorrente ou isolada.
- Registro auditável de ações executadas e erros.
"""
from __future__ import annotations

import json
import logging
import os
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional, Set, Tuple

from pc_control.config import get_allowed_client_ips
from pc_control.security import NonceCache, is_strong_token, validar_requisicao

logger = logging.getLogger("JarvisBaseAgent")


class PcAgentHandler(BaseHTTPRequestHandler):
    """Handler HTTP com autenticação HMAC para comandos do PC Control."""

    server: PcAgentServer

    def log_message(self, format: str, *args: Any) -> None:
        """Suprime logs verbosos de acesso padrão no console."""
        logger.debug("%s - - [%s] %s", self.address_string(), self.log_date_time_string(), format % args)

    def _responder_json(self, status_code: int, dados: Dict[str, Any]) -> None:
        payload = json.dumps(dados, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _verificar_ip_autorizado(self) -> bool:
        """Restringe a exposição validando o IP de origem contra a allowlist configurada."""
        if self.server.allowed_ips is not None:
            if "*" in self.server.allowed_ips or "all" in self.server.allowed_ips:
                return True
            client_ip = self.client_address[0]
            if client_ip.startswith("::ffff:"):
                client_ip = client_ip.replace("::ffff:", "")
            if client_ip not in self.server.allowed_ips:
                logger.warning(
                    "Conexão rejeitada de IP não autorizado: %s (Alvo: %s)",
                    client_ip,
                    self.server.target_id,
                )
                self._responder_json(
                    HTTPStatus.FORBIDDEN,
                    {"erro": "ip_nao_autorizado", "status": "falha"},
                )
                return False
        return True

    def do_GET(self) -> None:
        """Endpoint de saúde e telemetria."""
        if not self._verificar_ip_autorizado():
            return

        if self.path in ("/health", "/status"):
            uptime = time.time() - self.server.start_time
            self._responder_json(
                HTTPStatus.OK,
                {
                    "status": "online",
                    "target": self.server.target_id,
                    "versao": "1.0.0",
                    "uptime_segundos": round(uptime, 2),
                },
            )
        else:
            self._responder_json(
                HTTPStatus.NOT_FOUND,
                {"erro": "endpoint_nao_encontrado"},
            )

    def do_POST(self) -> None:
        """Recebe e processa comandos autenticados."""
        if not self._verificar_ip_autorizado():
            return

        if self.path != "/execute":
            self._responder_json(
                HTTPStatus.NOT_FOUND,
                {"erro": "endpoint_nao_encontrado"},
            )
            return

        # Validação fail-closed de credencial forte no próprio servidor
        if not is_strong_token(self.server.auth_token):
            logger.error("Agente [%s] rejeitou comando: token do servidor é fraco ou ausente.", self.server.target_id)
            self._responder_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {
                    "status": "falha",
                    "erro": "configuracao_incompleta_credencial_servidor_invalida",
                    "mensagem_amigavel": "Falha de segurança: agente com configuração incompleta.",
                },
            )
            return

        try:
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length <= 0 or content_length > 65536:
                self._responder_json(
                    HTTPStatus.BAD_REQUEST,
                    {"erro": "tamanho_corpo_invalido"},
                )
                return

            body_bytes = self.rfile.read(content_length)
            dados = json.loads(body_bytes.decode("utf-8"))
        except Exception as e:
            self._responder_json(
                HTTPStatus.BAD_REQUEST,
                {"erro": f"corpo_invalido: {e}"},
            )
            return

        event_id = dados.get("event_id") or "ev-desconhecido"
        session_id = dados.get("session_id")
        target_recebido = dados.get("target", "")
        action_recebida = dados.get("action", "")
        params = dados.get("params") or {}

        # Leitura dos cabeçalhos de segurança
        timestamp_str = self.headers.get("X-Jarvis-Timestamp") or str(dados.get("timestamp", 0))
        nonce = self.headers.get("X-Jarvis-Nonce") or dados.get("nonce", "")
        assinatura = self.headers.get("X-Jarvis-Signature") or dados.get("signature", "")

        try:
            timestamp = float(timestamp_str)
        except (ValueError, TypeError):
            self._responder_json(
                HTTPStatus.BAD_REQUEST,
                {"status": "falha", "erro": "timestamp_invalido"},
            )
            return

        # Validação de segurança estrita
        valido, motivo = validar_requisicao(
            token_esperado=self.server.auth_token,
            target_esperado=self.server.target_id,
            target_recebido=target_recebido,
            action_recebida=action_recebida,
            timestamp=timestamp,
            nonce=nonce,
            assinatura=assinatura,
            body_bytes=body_bytes,
            nonce_cache=self.server.nonce_cache,
        )

        if not valido:
            logger.warning(
                "Requisição rejeitada pelo agente [%s]: motivo='%s', acao='%s', event_id='%s'",
                self.server.target_id,
                motivo,
                action_recebida,
                event_id,
            )
            codigo_http = HTTPStatus.UNAUTHORIZED
            if motivo.startswith("alvo_") or motivo.startswith("acao_"):
                codigo_http = HTTPStatus.FORBIDDEN
            elif "nonce" in motivo:
                codigo_http = HTTPStatus.CONFLICT

            self._responder_json(
                codigo_http,
                {
                    "event_id": event_id,
                    "session_id": session_id,
                    "target": self.server.target_id,
                    "action": action_recebida,
                    "status": "falha",
                    "erro": motivo,
                    "mensagem_amigavel": f"Falha de segurança: {motivo}",
                },
            )
            return

        # Execução da ação no agente concreto
        try:
            sucesso, resultado, erro, msg_amigavel = self.server.executar_acao(
                action=action_recebida,
                params=params,
                event_id=event_id,
                session_id=session_id,
            )
            status_final = "concluido" if sucesso else "falha"
            self._responder_json(
                HTTPStatus.OK,
                {
                    "event_id": event_id,
                    "session_id": session_id,
                    "target": self.server.target_id,
                    "action": action_recebida,
                    "status": status_final,
                    "resultado": resultado,
                    "erro": erro,
                    "mensagem_amigavel": msg_amigavel,
                    "timestamp": time.time(),
                },
            )
        except Exception as e:
            logger.error("Erro inesperado ao executar ação %s: %s", action_recebida, e, exc_info=True)
            self._responder_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {
                    "event_id": event_id,
                    "session_id": session_id,
                    "target": self.server.target_id,
                    "action": action_recebida,
                    "status": "falha",
                    "erro": f"erro_interno: {e}",
                    "mensagem_amigavel": "Erro interno no agente local",
                },
            )


class PcAgentServer(ThreadingHTTPServer):
    """Servidor HTTP multithread para os agentes do Jarvis PC Control."""

    def __init__(
        self,
        host: str,
        port: int,
        target_id: str,
        auth_token: str,
        nonce_cache: Optional[NonceCache] = None,
        allowed_ips: Optional[Set[str]] = None,
        certfile: Optional[str] = None,
        keyfile: Optional[str] = None,
    ) -> None:
        self.target_id = target_id.lower()
        self.auth_token = auth_token or ""
        self.nonce_cache = nonce_cache or NonceCache()
        self.allowed_ips = allowed_ips if allowed_ips is not None else get_allowed_client_ips()
        self.certfile = certfile or os.getenv("JARVIS_PC_TLS_CERT")
        self.keyfile = keyfile or os.getenv("JARVIS_PC_TLS_KEY")
        self.start_time = time.time()
        # Determina se TLS foi explicitamente configurado via argumentos ou ambiente
        tls_configurado = bool(
            certfile
            or keyfile
            or os.getenv("JARVIS_PC_TLS_CERT")
            or os.getenv("JARVIS_PC_TLS_KEY")
            or os.getenv("JARVIS_PC_TLS_ENABLED") in ("1", "true", "True")
            or os.getenv("JARVIS_PC_ENABLE_TLS") in ("1", "true", "True")
        )

        super().__init__((host, port), PcAgentHandler)

        # Se TLS estiver configurado, validação estrita (fail-closed, sem fallback silencioso para HTTP)
        if tls_configurado:
            if not self.certfile or not self.keyfile:
                self.server_close()
                msg = f"Configuração TLS incompleta no agente [{self.target_id}]: certfile e keyfile são ambos obrigatórios."
                logger.error(msg)
                raise ValueError(msg)

            if not os.path.exists(self.certfile):
                self.server_close()
                msg = f"Certificado TLS não encontrado no agente [{self.target_id}]: {self.certfile}"
                logger.error(msg)
                raise FileNotFoundError(msg)

            if not os.path.exists(self.keyfile):
                self.server_close()
                msg = f"Chave privada TLS não encontrada no agente [{self.target_id}]: {self.keyfile}"
                logger.error(msg)
                raise FileNotFoundError(msg)

            try:
                import ssl
                ssl_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
                ssl_ctx.load_cert_chain(certfile=self.certfile, keyfile=self.keyfile)
                if hasattr(ssl, "TLSVersion"):
                    ssl_ctx.minimum_version = ssl.TLSVersion.TLSv1_2
                self.socket = ssl_ctx.wrap_socket(self.socket, server_side=True)
                logger.info("TLS 1.2+ ativo no agente local [%s]", self.target_id)
            except Exception as e:
                self.server_close()
                logger.critical(
                    "Falha ao carregar certificados TLS no agente [%s]: %s. Falha na inicialização sem fallback para HTTP.",
                    self.target_id,
                    e,
                )
                raise

    def executar_acao(
        self,
        action: str,
        params: Dict[str, Any],
        event_id: str,
        session_id: Optional[str] = None,
    ) -> Tuple[bool, Dict[str, Any], Optional[str], str]:
        """Método a ser implementado por cada agente de plataforma específica.

        Retorna: (sucesso: bool, resultado: dict, erro: str | None,
        mensagem_amigavel: str)
        """
        raise NotImplementedError("Subclasses devem implementar executar_acao.")
