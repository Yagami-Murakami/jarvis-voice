#!/usr/bin/env python3
"""Jarvis Display Server: Servidor HTTP e SSE leve para a central visual inteligente.

Fornece:
- Interface web estática moderna otimizada para o terminal Android (LG-X230).
- Stream SSE (Server-Sent Events) para atualização em tempo real de eventos do Jarvis Voice.
- Fallback para polling HTTP em navegadores legados.
- Integração somente-leitura com os logs do serviço jarvis-voice via journalctl.
- Cache inteligente da previsão do tempo (Open-Meteo).
- Modo de demonstração interativo para simulação e homologação.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import queue
import re
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
CONFIG_DEFAULT_PATH = BASE_DIR / "config.json"
CONFIG_EXAMPLE_PATH = BASE_DIR / "config.example.json"

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [JarvisDisplay] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("JarvisDisplay")


def carregar_config(caminho_config: Optional[Path] = None) -> Dict[str, Any]:
    caminho = caminho_config or CONFIG_DEFAULT_PATH
    if not caminho.is_file():
        if CONFIG_EXAMPLE_PATH.is_file():
            logger.info("Configuração personalizada não encontrada. Carregando %s", CONFIG_EXAMPLE_PATH)
            caminho = CONFIG_EXAMPLE_PATH
        else:
            return {}
    try:
        with caminho.open(encoding="utf-8") as f:
            return json.load(f)
    except Exception as erro:
        logger.warning("Falha ao ler %s: %s. Usando padrões.", caminho, erro)
        return {}


class WeatherService:
    """Consulta Open-Meteo com cache local de 20 minutos e tolerância a falhas."""

    def __init__(self, latitude: float = -22.6953, longitude: float = -43.2753, cache_ttl: int = 1200):
        self.latitude = latitude
        self.longitude = longitude
        self.cache_ttl = cache_ttl
        self._cache: Optional[Dict[str, Any]] = None
        self._last_fetch = 0.0
        self._lock = threading.Lock()

    def obter_clima(self) -> Dict[str, Any]:
        agora = time.time()
        with self._lock:
            if self._cache and (agora - self._last_fetch) < self.cache_ttl:
                return self._cache

        url = (
            f"https://api.open-meteo.com/v1/forecast?"
            f"latitude={self.latitude}&longitude={self.longitude}&"
            f"current=temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m&"
            f"timezone=America%2FSao_Paulo"
        )

        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "JarvisVoiceDisplay/1.0 (HomeAutomation)"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    current = data.get("current", {})
                    weather_code = current.get("weather_code", 0)
                    descricao, icone = self._interpretar_codigo(weather_code)

                    resultado = {
                        "sucesso": True,
                        "temperatura": round(current.get("temperature_2m", 0)),
                        "umidade": current.get("relative_humidity_2m", 0),
                        "vento": current.get("wind_speed_10m", 0),
                        "codigo": weather_code,
                        "descricao": descricao,
                        "icone": icone,
                        "cidade": "Santa Cruz da Serra, Duque de Caxias",
                        "atualizado_em": agora,
                    }
                    with self._lock:
                        self._cache = resultado
                        self._last_fetch = agora
                    return resultado
        except Exception as erro:
            logger.warning("Erro ao consultar Open-Meteo: %s", erro)

        with self._lock:
            if self._cache:
                cache_antigo = dict(self._cache)
                cache_antigo["desatualizado"] = True
                return cache_antigo

        return {
            "sucesso": False,
            "temperatura": "--",
            "descricao": "Dados meteorológicos indisponíveis",
            "icone": "desconhecido",
            "cidade": "Santa Cruz da Serra, Duque de Caxias",
            "atualizado_em": 0,
        }

    @staticmethod
    def _interpretar_codigo(codigo: int) -> tuple[str, str]:
        # Tabela oficial WMO Weather interpretation codes
        if codigo == 0:
            return ("Céu limpo", "sol")
        if codigo in (1, 2):
            return ("Parcialmente nublado", "parcial")
        if codigo == 3:
            return ("Nublado", "nublado")
        if codigo in (45, 48):
            return ("Nevoeiro", "nevoeiro")
        if codigo in (51, 53, 55):
            return ("Chuvisco", "chuvisco")
        if codigo in (61, 63, 65):
            return ("Chuva", "chuva")
        if codigo in (71, 73, 75):
            return ("Neve", "neve")
        if codigo in (80, 81, 82):
            return ("Pancadas de chuva", "pancadas")
        if codigo in (95, 96, 99):
            return ("Tempestade", "tempestade")
        return ("Tempo estável", "sol")


class JarvisDisplayState:
    """Gerenciador de estado em memória com suporte a SSE."""

    def __init__(self):
        self._lock = threading.Lock()
        self._listeners: List[queue.Queue] = []
        self._state: Dict[str, Any] = {
            "id": 1,
            "status": "idle",  # idle, listening, processing, confirming, success, cancelled, timeout, error, offline
            "titulo": "JARVIS VOICE",
            "mensagem": "Aguardando palavra de ativação...",
            "detalhe": "",
            "alvo": None,
            "acao": None,
            "timestamp": time.time(),
            "auto_reset_s": 0,
        }

    def registrar_ouvinte(self) -> queue.Queue:
        q = queue.Queue(maxsize=32)
        with self._lock:
            self._listeners.append(q)
            # Envia estado inicial imediato
            q.put_nowait(self._state)
        return q

    def remover_ouvinte(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._listeners:
                self._listeners.remove(q)

    def obter_estado(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def atualizar(
        self,
        status: str,
        mensagem: str,
        detalhe: str = "",
        alvo: Optional[str] = None,
        acao: Optional[str] = None,
        auto_reset_s: float = 0,
    ) -> None:
        with self._lock:
            self._state["id"] += 1
            self._state["status"] = status
            self._state["mensagem"] = mensagem
            self._state["detalhe"] = detalhe
            self._state["alvo"] = alvo
            self._state["acao"] = acao
            self._state["timestamp"] = time.time()
            self._state["auto_reset_s"] = auto_reset_s
            snapshot = dict(self._state)

            mortos = []
            for q in self._listeners:
                try:
                    q.put_nowait(snapshot)
                except queue.Full:
                    mortos.append(q)
            for m in mortos:
                self._listeners.remove(m)

        if auto_reset_s > 0:
            def _reset_t():
                time.sleep(auto_reset_s)
                with self._lock:
                    # Só reseta se o estado não mudou nesse intervalo
                    if self._state["timestamp"] == snapshot["timestamp"]:
                        self._reset_para_idle_desprotegido()
            threading.Thread(target=_reset_t, daemon=True).start()

    def _reset_para_idle_desprotegido(self) -> None:
        self._state["id"] += 1
        self._state["status"] = "idle"
        self._state["mensagem"] = "Aguardando palavra de ativação..."
        self._state["detalhe"] = ""
        self._state["alvo"] = None
        self._state["acao"] = None
        self._state["timestamp"] = time.time()
        self._state["auto_reset_s"] = 0
        snapshot = dict(self._state)

        mortos = []
        for q in self._listeners:
            try:
                q.put_nowait(snapshot)
            except queue.Full:
                mortos.append(q)
        for m in mortos:
            self._listeners.remove(m)


class JarvisLogMonitor:
    """Monitor de logs em tempo real que acompanha o serviço jarvis-voice.service via journalctl."""

    def __init__(self, display_state: JarvisDisplayState, unit_name: str = "jarvis-voice.service"):
        self.state = display_state
        self.unit_name = unit_name
        self.running = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self.running = True
        self._thread = threading.Thread(target=self._loop, name="jarvis-journal-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.running = False

    def _loop(self) -> None:
        logger.info("Iniciando monitoramento de eventos do systemd para '%s'", self.unit_name)
        cmd = ["journalctl", "-u", self.unit_name, "-f", "-o", "cat", "-n", "0"]
        while self.running:
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                )
                for line in iter(proc.stdout.readline, ""):
                    if not self.running:
                        break
                    linha = line.strip()
                    if linha:
                        self.processar_linha(linha)
                proc.stdout.close()
                proc.wait()
            except Exception as erro:
                logger.warning("Erro no leitor de journalctl: %s. Tentando novamente em 3s...", erro)
                time.sleep(3)

    def processar_linha(self, linha: str) -> None:
        logger.debug("Journal line: %s", linha)

        # 1. Ativação
        if "ATIVADO! Aguardando comando." in linha:
            self.state.atualizar(
                status="listening",
                mensagem="Estou ouvindo...",
                detalhe="Pode falar o comando",
                auto_reset_s=14.0,  # segurança caso não chegue timeout
            )
            return

        # 2. Comando ouvido
        if linha.startswith("Ouvi:"):
            texto = linha.replace("Ouvi:", "").strip()
            if texto == "assistente":
                self.state.atualizar(
                    status="listening",
                    mensagem="Estou ouvindo...",
                    detalhe="",
                )
            elif texto == "cancelar":
                self.state.atualizar(
                    status="cancelled",
                    mensagem="Operação cancelada.",
                    detalhe="",
                    auto_reset_s=4.0,
                )
            else:
                self.state.atualizar(
                    status="processing",
                    mensagem=f"Comando: {texto.capitalize()}",
                    detalhe="Processando...",
                )
            return

        # 3. Computador alvo identificado
        if linha.startswith("Computador:"):
            comp = linha.replace("Computador:", "").strip()
            self.state.atualizar(
                status="processing",
                mensagem=f"Computador: {comp}",
                detalhe="Verificando conectividade...",
                alvo=comp,
            )
            return

        # 4. Wake-on-LAN enviado
        if (
            "Pacote Wake-on-LAN enviado." in linha
            or "Wake-on-LAN enviado." in linha
            or "SIMULAÇÃO: enviaria Wake-on-LAN." in linha
            or "SIMULACAO: Wake-on-LAN." in linha
        ):
            simul = " (Simulação)" if ("SIMULAÇÃO" in linha or "SIMULACAO" in linha) else ""
            self.state.atualizar(
                status="success",
                mensagem="Wake-on-LAN enviado!",
                detalhe=f"Pacote de inicialização transmitido com sucesso{simul}.",
                auto_reset_s=6.0,
            )
            return

        # 5. Já está acessível
        if "Computador já está acessível." in linha or "Computador ja esta acessivel." in linha:
            self.state.atualizar(
                status="success",
                mensagem="Computador já ligado",
                detalhe="Host respondeu na rede local.",
                auto_reset_s=5.0,
            )
            return

        # 6. Confirmação de desligamento
        if "Confirme desligamento de" in linha:
            alvo_match = re.search(r"Confirme desligamento de ([^:]+):", linha)
            alvo = alvo_match.group(1).strip() if alvo_match else "Computador"
            self.state.atualizar(
                status="confirming",
                mensagem=f"Confirme o desligamento do {alvo}",
                detalhe='Diga "confirmar desligamento" ou "cancelar"',
                alvo=alvo,
                acao="desligar",
                auto_reset_s=22.0,
            )
            return

        # 6b. Confirmação de PC Control (reiniciar e fechar navegador)
        if "Confirme reinicialização de" in linha or "Confirme reinicializacao de" in linha:
            alvo_match = re.search(r"Confirme reinicializa[çc][ãa]o de ([^:]+):", linha)
            alvo = alvo_match.group(1).strip() if alvo_match else "Computador"
            self.state.atualizar(
                status="confirming",
                mensagem=f"Confirme a reinicialização de {alvo}",
                detalhe='Diga "confirmar" ou "cancelar"',
                alvo=alvo,
                acao="reiniciar",
                auto_reset_s=22.0,
            )
            return

        if "Confirme fechar navegador em" in linha:
            alvo_match = re.search(r"Confirme fechar navegador em ([^:]+):", linha)
            alvo = alvo_match.group(1).strip() if alvo_match else "Computador"
            self.state.atualizar(
                status="confirming",
                mensagem=f"Confirme fechar navegador em {alvo}",
                detalhe='Diga "confirmar" ou "cancelar"',
                alvo=alvo,
                acao="fechar_navegador",
                auto_reset_s=22.0,
            )
            return

        # 6c. Eventos concluídos do PC Control
        if "PC Control:" in linha:
            msg = linha.split("PC Control:", 1)[1].strip()
            msg_lower = msg.lower()
            is_sucesso_bloqueio = "bloqueado com sucesso" in msg_lower
            is_falha = (not is_sucesso_bloqueio) and any(
                term in msg_lower for term in ("falha", "erro", "offline", "desativado", "bloqueado por", "falha ao bloquear")
            )
            self.state.atualizar(
                status="error" if is_falha else "success",
                mensagem=msg,
                detalhe="Jarvis PC Control V1",
                auto_reset_s=6.0,
            )
            return

        # 7. Desligamento realizado via SSH
        if (
            "SSH: código de retorno 0" in linha
            or "SSH: codigo de retorno 0" in linha
            or "SSH enviado com sucesso" in linha
            or "SIMULAÇÃO: enviaria desligamento SSH." in linha
            or "SIMULACAO SSH para" in linha
        ):
            simul = " (Simulação)" if ("SIMULAÇÃO" in linha or "SIMULACAO" in linha) else ""
            self.state.atualizar(
                status="success",
                mensagem="Desligamento solicitado!",
                detalhe=f"Comando SSH executado com sucesso{simul}.",
                auto_reset_s=6.0,
            )
            return

        # 8. Desligamento bloqueado
        if (
            "Desligamento bloqueado" in linha
            or "DESLIGAMENTO BLOQUEADO" in linha
            or "computador inacessivel" in linha
            or "computador nao acessivel" in linha
        ):
            self.state.atualizar(
                status="error",
                mensagem="Desligamento bloqueado",
                detalhe="Host não responde na rede local.",
                auto_reset_s=5.0,
            )
            return

        # 9. Cancelamentos e timeouts
        if "Cancelado." in linha or "Pedido cancelado." in linha:
            self.state.atualizar(
                status="cancelled",
                mensagem="Operação cancelada.",
                detalhe="",
                auto_reset_s=4.0,
            )
            return

        if "Tempo esgotado; pedido cancelado." in linha:
            self.state.atualizar(
                status="timeout",
                mensagem="Tempo esgotado.",
                detalhe="Nenhum comando foi recebido.",
                auto_reset_s=4.0,
            )
            return

        # 10. Falhas de microfone ou áudio
        if "Captura de áudio encerrada." in linha:
            self.state.atualizar(
                status="offline",
                mensagem="Microfone desconectado",
                detalhe="Captura de áudio ALSA foi finalizada.",
            )
            return


class JarvisDisplayHandler(BaseHTTPRequestHandler):
    """Manipulador HTTP para servir o frontend, dados do clima e o stream SSE."""

    server_version = "JarvisDisplay/1.0"

    def do_GET(self) -> None:
        url_parsed = urllib.parse.urlparse(self.path)
        caminho = url_parsed.path

        if caminho == "/" or caminho == "/index.html":
            self._servir_arquivo(STATIC_DIR / "index.html", "text/html; charset=utf-8")
        elif caminho == "/style.css":
            self._servir_arquivo(STATIC_DIR / "style.css", "text/css; charset=utf-8")
        elif caminho == "/app.js":
            self._servir_arquivo(STATIC_DIR / "app.js", "application/javascript; charset=utf-8")
        elif caminho == "/api/status":
            self._servir_json(self.server.display_state.obter_estado())
        elif caminho == "/api/weather":
            self._servir_json(self.server.weather_service.obter_clima())
        elif caminho == "/api/mics":
            self._servir_mics()
        elif caminho == "/api/events":
            self._servir_sse()
        elif caminho.startswith("/static/"):
            rel = caminho.replace("/static/", "", 1)
            f_path = (STATIC_DIR / rel).resolve()
            if str(f_path).startswith(str(STATIC_DIR.resolve())) and f_path.is_file():
                mime = self._descobrir_mime(f_path)
                self._servir_arquivo(f_path, mime)
            else:
                self.send_error(HTTPStatus.NOT_FOUND, "Arquivo não encontrado")
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Recurso não encontrado")

    def do_POST(self) -> None:
        url_parsed = urllib.parse.urlparse(self.path)
        caminho = url_parsed.path

        if caminho == "/api/demo/trigger" and getattr(self.server, "demo_mode", False):
            comprimento = int(self.headers.get("Content-Length", 0))
            corpo = self.rfile.read(comprimento).decode("utf-8") if comprimento > 0 else "{}"
            try:
                dados = json.loads(corpo)
            except Exception:
                dados = {}

            evento = dados.get("evento", "wake")
            self._processar_trigger_demo(evento, dados)
            self._servir_json({"sucesso": True, "evento": evento, "estado": self.server.display_state.obter_estado()})
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Endpoint não encontrado")

    def _processar_trigger_demo(self, evento: str, dados: Dict[str, Any]) -> None:
        state = self.server.display_state
        logger.info("Trigger Demo recebido: %s", evento)

        if evento == "wake":
            state.atualizar(
                status="listening",
                mensagem="Estou ouvindo...",
                detalhe="Pode falar o comando",
                auto_reset_s=12.0,
            )
        elif evento == "cmd_wol_fusion":
            state.atualizar(
                status="processing",
                mensagem="Comando: Ligar Fusion",
                detalhe="Enviando comando Wake-on-LAN...",
                alvo="Fusion",
                acao="ligar",
            )
        elif evento == "wol_sent_fusion":
            state.atualizar(
                status="success",
                mensagem="Wake-on-LAN enviado!",
                detalhe="Pacote de inicialização transmitido para Fusion.",
                alvo="Fusion",
                acao="ligar",
                auto_reset_s=6.0,
            )
        elif evento == "cmd_wol_ananuke":
            state.atualizar(
                status="processing",
                mensagem="Comando: Ligar Ananuke",
                detalhe="Enviando comando Wake-on-LAN...",
                alvo="Ananuke",
                acao="ligar",
            )
        elif evento == "wol_sent_ananuke":
            state.atualizar(
                status="success",
                mensagem="Wake-on-LAN enviado!",
                detalhe="Pacote de inicialização transmitido para Ananuke.",
                alvo="Ananuke",
                acao="ligar",
                auto_reset_s=6.0,
            )
        elif evento == "cmd_shutdown_fusion":
            state.atualizar(
                status="confirming",
                mensagem="Confirme o desligamento do Fusion",
                detalhe='Diga "confirmar desligamento" ou "cancelar"',
                alvo="Fusion",
                acao="desligar",
                auto_reset_s=20.0,
            )
        elif evento == "cmd_shutdown_ananuke":
            state.atualizar(
                status="confirming",
                mensagem="Confirme o desligamento do Ananuke",
                detalhe='Diga "confirmar desligamento" ou "cancelar"',
                alvo="Ananuke",
                acao="desligar",
                auto_reset_s=20.0,
            )
        elif evento == "shutdown_confirmed":
            alvo = dados.get("alvo", "Computador")
            state.atualizar(
                status="success",
                mensagem="Desligamento solicitado!",
                detalhe=f"Solicitação de desligamento enviada para {alvo}.",
                alvo=alvo,
                acao="desligar",
                auto_reset_s=6.0,
            )
        elif evento == "cancel":
            state.atualizar(
                status="cancelled",
                mensagem="Operação cancelada.",
                detalhe="",
                auto_reset_s=4.0,
            )
        elif evento == "timeout":
            state.atualizar(
                status="timeout",
                mensagem="Tempo esgotado.",
                detalhe="Nenhum comando foi recebido.",
                auto_reset_s=4.0,
            )
        elif evento == "mic_error":
            state.atualizar(
                status="offline",
                mensagem="Microfone desconectado",
                detalhe="Verifique a conexão USB do microfone ME6S.",
            )
        elif evento == "reset":
            state.atualizar(
                status="idle",
                mensagem="Aguardando palavra de ativação...",
                detalhe="",
            )

    def _servir_mics(self) -> None:
        try:
            req = urllib.request.Request("http://127.0.0.1:8089/api/mics")
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                conteudo = resp.read()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(conteudo)))
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(conteudo)
        except Exception as e:
            self._servir_json({
                "me6s": {"active": True, "error": str(e)},
                "lg_mic": {"connected": False, "error": str(e)},
                "gateway_online": False,
            })

    def _servir_sse(self) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        fila = self.server.display_state.registrar_ouvinte()
        logger.info("Cliente SSE conectado: %s", self.client_address[0])

        try:
            while True:
                try:
                    estado = fila.get(timeout=15.0)
                    corpo = f"data: {json.dumps(estado, ensure_ascii=False)}\n\n"
                    self.wfile.write(corpo.encode("utf-8"))
                    self.wfile.flush()
                except queue.Empty:
                    # Envia comentário de heartbeat para manter a conexão TCP viva
                    self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            logger.info("Cliente SSE desconectado: %s", self.client_address[0])
        finally:
            self.server.display_state.remover_ouvinte(fila)

    def _servir_json(self, obj: Any) -> None:
        conteudo = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(conteudo)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(conteudo)

    def _servir_arquivo(self, caminho: Path, mime_type: str) -> None:
        if not caminho.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "Arquivo não encontrado")
            return
        try:
            conteudo = caminho.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", mime_type)
            self.send_header("Content-Length", str(len(conteudo)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(conteudo)
        except Exception as erro:
            logger.error("Erro ao ler arquivo %s: %s", caminho, erro)
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, "Erro interno ao ler arquivo")

    @staticmethod
    def _descobrir_mime(caminho: Path) -> str:
        ext = caminho.suffix.lower()
        mimes = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".svg": "image/svg+xml",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".ico": "image/x-icon",
            ".wav": "audio/wav",
            ".mp3": "audio/mpeg",
        }
        return mimes.get(ext, "application/octet-stream")

    def log_message(self, format: str, *args: Any) -> None:
        # Silencia logs repetitivos de polling e SSE no stdout padrão para manter log limpo
        try:
            msg = format % args
            if "/api/events" in msg or "/api/status" in msg:
                return
            logger.debug("%s - %s", self.client_address[0], msg)
        except Exception:
            pass


def rodar_servidor():
    parser = argparse.ArgumentParser(description="Jarvis Voice Display Server")
    parser.add_argument("--host", default="0.0.0.0", help="Endereço de escuta (padrão 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8080, help="Porta TCP (padrão 8080)")
    parser.add_argument("--demo", action="store_true", help="Inicia em modo de demonstração/simulador")
    parser.add_argument("--config", type=Path, default=None, help="Caminho do arquivo de configuração JSON")
    args = parser.parse_args()

    cfg = carregar_config(args.config)
    host = args.host or cfg.get("host", "0.0.0.0")
    porta = args.port or cfg.get("port", 8080)
    modo_demo = args.demo or cfg.get("demo_mode", False) or os.getenv("JARVIS_DISPLAY_DEMO", "0") == "1"
    unit_service = cfg.get("systemd_unit", "jarvis-voice.service")

    lat = cfg.get("weather_latitude", -22.6953)
    lon = cfg.get("weather_longitude", -43.2753)

    server = ThreadingHTTPServer((host, porta), JarvisDisplayHandler)
    server.demo_mode = bool(modo_demo)
    server.display_state = JarvisDisplayState()
    server.weather_service = WeatherService(latitude=lat, longitude=lon)

    if modo_demo:
        logger.info("MODO DEMO ATIVADO. Eventos reais de journalctl desligados.")
    else:
        monitor = JarvisLogMonitor(server.display_state, unit_name=unit_service)
        monitor.start()

    logger.info("==================================================")
    logger.info("  JARVIS VOICE DISPLAY SERVER INICIADO COM SUCESSO")
    logger.info("  Acesse a interface em: http://%s:%d", "127.0.0.1" if host == "0.0.0.0" else host, porta)
    logger.info("  Modo: %s", "DEMO / SIMULAÇÃO" if modo_demo else "PRODUÇÃO (Journalctl)")
    logger.info("==================================================")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Encerrando servidor...")
    finally:
        server.server_close()


if __name__ == "__main__":
    rodar_servidor()
