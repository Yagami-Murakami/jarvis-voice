#!/usr/bin/env python3
"""Testes automatizados para o Jarvis Display Server."""

import json
import os
import threading
import time
import unittest
from http.client import HTTPConnection
from pathlib import Path
import sys

# Adiciona o diretório display ao path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from server import (
    JarvisDisplayHandler,
    JarvisDisplayState,
    JarvisLogMonitor,
    ThreadingHTTPServer,
    WeatherService,
)


class TestWeatherService(unittest.TestCase):
    def test_weather_interpretation(self):
        ws = WeatherService()
        self.assertEqual(ws._interpretar_codigo(0), ("Céu limpo", "sol"))
        self.assertEqual(ws._interpretar_codigo(2), ("Parcialmente nublado", "parcial"))
        self.assertEqual(ws._interpretar_codigo(61), ("Chuva", "chuva"))
        self.assertEqual(ws._interpretar_codigo(95), ("Tempestade", "tempestade"))

    def test_weather_cache(self):
        ws = WeatherService(cache_ttl=60)
        ws._cache = {
            "sucesso": True,
            "temperatura": 25,
            "descricao": "Céu limpo",
            "icone": "sol",
            "cidade": "Santa Cruz da Serra, Duque de Caxias",
            "atualizado_em": time.time(),
        }
        ws._last_fetch = time.time()
        res = ws.obter_clima()
        self.assertTrue(res["sucesso"])
        self.assertEqual(res["temperatura"], 25)


class TestDisplayState(unittest.TestCase):
    def test_state_transitions_and_listeners(self):
        state = JarvisDisplayState()
        q = state.registrar_ouvinte()

        # Estado inicial enviado
        st0 = q.get(timeout=1.0)
        self.assertEqual(st0["status"], "idle")

        # Atualização para listening
        state.atualizar(status="listening", mensagem="Estou ouvindo...", detalhe="Pode falar")
        st1 = q.get(timeout=1.0)
        self.assertEqual(st1["status"], "listening")
        self.assertEqual(st1["mensagem"], "Estou ouvindo...")

        # Atualização para success com auto-reset
        state.atualizar(status="success", mensagem="WOL enviado!", auto_reset_s=0.2)
        st2 = q.get(timeout=1.0)
        self.assertEqual(st2["status"], "success")

        # Verifica auto-reset para idle
        st3 = q.get(timeout=1.0)
        self.assertEqual(st3["status"], "idle")

        state.remover_ouvinte(q)


class TestLogMonitor(unittest.TestCase):
    def test_log_line_parsing(self):
        state = JarvisDisplayState()
        monitor = JarvisLogMonitor(state)

        # 1. Ativação
        monitor.processar_linha("ATIVADO! Aguardando comando.")
        self.assertEqual(state.obter_estado()["status"], "listening")

        # 2. Comando ouvido
        monitor.processar_linha("Ouvi: ligar fusion")
        self.assertEqual(state.obter_estado()["status"], "processing")
        self.assertIn("Ligar fusion", state.obter_estado()["mensagem"])

        # 3. Computador alvo
        monitor.processar_linha("Computador: Fusion")
        self.assertEqual(state.obter_estado()["alvo"], "Fusion")

        # 4. Wake-on-LAN enviado
        monitor.processar_linha("Pacote Wake-on-LAN enviado.")
        self.assertEqual(state.obter_estado()["status"], "success")

        # 5. Confirmação
        monitor.processar_linha('Confirme desligamento de Ananuke: diga "confirmar desligamento" ou "cancelar".')
        self.assertEqual(state.obter_estado()["status"], "confirming")
        self.assertEqual(state.obter_estado()["alvo"], "Ananuke")

        # 6. Cancelamento
        monitor.processar_linha("Cancelado.")
        self.assertEqual(state.obter_estado()["status"], "cancelled")

        # 7. Timeout
        monitor.processar_linha("Tempo esgotado; pedido cancelado.")
        self.assertEqual(state.obter_estado()["status"], "timeout")

        # 8. Microfone offline
        monitor.processar_linha("Captura de áudio encerrada.")
        self.assertEqual(state.obter_estado()["status"], "offline")


class TestHTTPServerIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), JarvisDisplayHandler)
        cls.server.display_state = JarvisDisplayState()
        cls.server.weather_service = WeatherService()
        cls.server.demo_mode = True
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    def setUp(self):
        self.server.display_state = JarvisDisplayState()

    def test_get_index(self):
        conn = HTTPConnection("127.0.0.1", self.port)
        conn.request("GET", "/")
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        self.assertIn("text/html", resp.getheader("Content-Type"))
        body = resp.read().decode("utf-8")
        self.assertIn("JARVIS VOICE DISPLAY", body)
        conn.close()

    def test_get_status(self):
        conn = HTTPConnection("127.0.0.1", self.port)
        conn.request("GET", "/api/status")
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        data = json.loads(resp.read().decode("utf-8"))
        self.assertIn("status", data)
        self.assertEqual(data["status"], "idle")
        conn.close()

    def test_demo_trigger_post(self):
        conn = HTTPConnection("127.0.0.1", self.port)
        payload = json.dumps({"evento": "wake"})
        conn.request("POST", "/api/demo/trigger", body=payload, headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        data = json.loads(resp.read().decode("utf-8"))
        self.assertTrue(data["sucesso"])
        self.assertEqual(data["estado"]["status"], "listening")
        conn.close()


if __name__ == "__main__":
    unittest.main()
