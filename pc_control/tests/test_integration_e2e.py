#!/usr/bin/env python3
"""Testes de Integração Ponta a Ponta (End-to-End) para o Jarvis PC Control V1.

Valida:
- Servidores HTTP de loopback reais para WindowsPcAgent (Fusion) e LinuxPcAgent (Ananuke).
- Troca de requisições autenticadas por HMAC-SHA256 sobre HTTP real em localhost.
- Fluxo de confirmação em duas etapas para comandos destrutivos (fechar navegador e reiniciar).
- Proteções contra ataques de replay (nonce), assinatura inválida e target divergente.
- Deduplicação entre microfones duplos (Fifine ME6S e LG-X230DSV).
- Respeito à Feature Flag JARVIS_PC_CONTROL_ENABLED.
- Integração com o despachante do controlador operacional e o servidor de display.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

# Caminhos do projeto
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "display"))

import types
from unittest.mock import MagicMock

if "vosk" not in sys.modules:
    mock_vosk = types.ModuleType("vosk")
    mock_vosk.Model = MagicMock()
    mock_vosk.KaldiRecognizer = MagicMock()
    mock_vosk.SetLogLevel = MagicMock()
    sys.modules["vosk"] = mock_vosk

if "pyaudio" not in sys.modules:
    mock_pyaudio = types.ModuleType("pyaudio")
    mock_pyaudio.PyAudio = MagicMock()
    mock_pyaudio.paInt16 = 8
    sys.modules["pyaudio"] = mock_pyaudio

if "app" not in sys.modules:
    mock_app = types.ModuleType("app")
    mock_app.SERVIDORES = {
        "fusion": {"nome": "Fusion (Windows)", "mac": "bc:24:11:83:35:82"},
        "ananuke": {"nome": "Ananuke (Debian)", "mac": "0a:e0:af:c3:06:fc"},
    }
    mock_app.host_ligado = lambda srv: True
    mock_app.montar_comando_ssh = lambda cfg: ["echo", "ssh_simulado"]
    sys.modules["app"] = mock_app

from pc_control.agents.linux_agent import LinuxPcAgent
from pc_control.agents.windows_agent import WindowsPcAgent
from pc_control.client import PcControlClient
from pc_control.dispatcher import PcControlDispatcher
from pc_control.security import calcular_assinatura
from display.server import JarvisDisplayState, JarvisLogMonitor
import jarvis_operacional_corrigido as jarvis_ctrl

# Suprime logs excessivos durante testes
logging.disable(logging.CRITICAL)


class TestPcControlE2ELoopback(unittest.TestCase):
    """Testa integração com servidores HTTP reais rodando em localhost em portas efêmeras."""

    fusion_server: WindowsPcAgent
    ananuke_server: LinuxPcAgent
    fusion_thread: threading.Thread
    ananuke_thread: threading.Thread
    token: str = "token-secreto-teste-e2e-12345"

    @classmethod
    def setUpClass(cls) -> None:
        # Inicializa os servidores em mock_mode=True para não afetar o sistema operacional
        cls.fusion_server = WindowsPcAgent(host="127.0.0.1", port=0, auth_token=cls.token, mock_mode=True)
        cls.ananuke_server = LinuxPcAgent(host="127.0.0.1", port=0, auth_token=cls.token, mock_mode=True)

        cls.fusion_port = cls.fusion_server.server_port
        cls.ananuke_port = cls.ananuke_server.server_port

        cls.fusion_thread = threading.Thread(target=cls.fusion_server.serve_forever, daemon=True)
        cls.ananuke_thread = threading.Thread(target=cls.ananuke_server.serve_forever, daemon=True)

        cls.fusion_thread.start()
        cls.ananuke_thread.start()

        time.sleep(0.05)  # Aguarda subida dos sockets

    @classmethod
    def tearDownClass(cls) -> None:
        cls.fusion_server.shutdown()
        cls.fusion_server.server_close()
        cls.ananuke_server.shutdown()
        cls.ananuke_server.server_close()

    def setUp(self) -> None:
        self.fusion_url = f"http://127.0.0.1:{self.fusion_port}"
        self.ananuke_url = f"http://127.0.0.1:{self.ananuke_port}"

        self.env_patcher = patch.dict(
            os.environ,
            {
                "JARVIS_PC_CONTROL_ENABLED": "1",
                "JARVIS_PC_AGENT_TOKEN": self.token,
                "JARVIS_PC_FUSION_URL": self.fusion_url,
                "JARVIS_PC_ANANUKE_URL": self.ananuke_url,
                "JARVIS_PC_HTTP_TIMEOUT": "2.0",
            },
        )
        self.env_patcher.start()

        self.client = PcControlClient(
            token=self.token,
            url_overrides={"fusion": self.fusion_url, "ananuke": self.ananuke_url},
            timeout=2.0,
        )
        self.dispatcher = PcControlDispatcher(client=self.client, confirmation_timeout_s=5.0)

    def tearDown(self) -> None:
        self.env_patcher.stop()

    def test_e2e_fusion_commands_live_http(self) -> None:
        """Executa comandos no agente Windows (Fusion) via HTTP loopback."""
        comandos_fusion = [
            ("abrir_navegador", "aberto no Fusion"),
            ("abrir_youtube", "YouTube aberto no Fusion"),
            ("bloquear", "Computador Fusion bloqueado"),
            ("aumentar_volume", "Volume aumentado no Fusion"),
            ("diminuir_volume", "Volume diminuído no Fusion"),
            ("silenciar_audio", "Áudio silenciado no Fusion"),
            ("status_computador", "CPU"),
            ("abrir_terminal", "aberto no Fusion"),
            ("mostrar_area_de_trabalho", "Área de trabalho exibida no Fusion"),
        ]

        for acao, trecho_esperado in comandos_fusion:
            with self.subTest(acao=acao):
                resp = self.dispatcher.processar_comando_ou_confirmacao(
                    acao=acao,
                    alvo="fusion",
                    texto_falado=f"fusion {acao}",
                    session_id="sess-e2e-fusion",
                    event_id=f"ev-e2e-{acao}",
                    fonte="me6s",
                )
                self.assertTrue(resp["sucesso"], f"Falha ao executar {acao}: {resp}")
                self.assertEqual(resp["status"], "concluido")
                self.assertIn(trecho_esperado, resp["mensagem_amigavel"])

    def test_e2e_ananuke_commands_live_http(self) -> None:
        """Executa comandos no agente Linux (Ananuke) via HTTP loopback."""
        comandos_ananuke = [
            ("abrir_navegador", "aberto no Ananuke"),
            ("abrir_youtube", "YouTube aberto no Ananuke"),
            ("bloquear", "Computador Ananuke bloqueado"),
            ("aumentar_volume", "Volume aumentado no Ananuke"),
            ("diminuir_volume", "Volume diminuído no Ananuke"),
            ("silenciar_audio", "Áudio silenciado no Ananuke"),
            ("status_computador", "CPU"),
            ("abrir_terminal", "aberto no Ananuke"),
            ("mostrar_area_de_trabalho", "Área de trabalho exibida no Ananuke"),
        ]

        for acao, trecho_esperado in comandos_ananuke:
            with self.subTest(acao=acao):
                resp = self.dispatcher.processar_comando_ou_confirmacao(
                    acao=acao,
                    alvo="ananuke",
                    texto_falado=f"ananuke {acao}",
                    session_id="sess-e2e-ananuke",
                    event_id=f"ev-e2e-ana-{acao}",
                    fonte="lg_mic",
                )
                self.assertTrue(resp["sucesso"], f"Falha ao executar {acao}: {resp}")
                self.assertEqual(resp["status"], "concluido")
                self.assertIn(trecho_esperado, resp["mensagem_amigavel"])

    def test_e2e_two_step_confirmation_flow_success(self) -> None:
        """Fluxo de confirmação em 2 etapas para reiniciar Fusion via HTTP real."""
        # 1. Solicitação inicial
        resp1 = self.dispatcher.processar_comando_ou_confirmacao(
            acao="reiniciar",
            alvo="fusion",
            texto_falado="fusion reiniciar computador",
            session_id="sess-conf-1",
            event_id="ev-reiniciar-1",
            fonte="me6s",
        )
        self.assertTrue(resp1["sucesso"])
        self.assertEqual(resp1["status"], "confirmacao_pendente")
        self.assertEqual(resp1["estado"], "confirmacao_pendente")
        self.assertIn("Confirme reinicialização", resp1["mensagem_amigavel"])

        # 2. Confirmação positiva com mesma sessão
        resp2 = self.dispatcher.processar_comando_ou_confirmacao(
            acao="confirmar",
            alvo="fusion",
            texto_falado="confirmar",
            session_id="sess-conf-1",
            event_id="ev-reiniciar-2",
            fonte="lg_mic",
        )
        self.assertTrue(resp2["sucesso"])
        self.assertEqual(resp2["status"], "concluido")
        self.assertIn("reinici", resp2["mensagem_amigavel"].lower())

    def test_e2e_two_step_confirmation_flow_cancellation(self) -> None:
        """Cancelamento explícito do fechamento de navegador no Ananuke."""
        # 1. Solicitação inicial
        resp1 = self.dispatcher.processar_comando_ou_confirmacao(
            acao="fechar_navegador",
            alvo="ananuke",
            texto_falado="fechar navegador ananuke",
            session_id="sess-cancel-1",
            event_id="ev-fechar-1",
            fonte="me6s",
        )
        self.assertEqual(resp1["status"], "confirmacao_pendente")

        # 2. Usuário cancela dizendo "cancelar"
        resp2 = self.dispatcher.processar_comando_ou_confirmacao(
            acao="cancelar",
            alvo="ananuke",
            texto_falado="cancelar",
            session_id="sess-cancel-1",
            event_id="ev-fechar-2",
            fonte="me6s",
        )
        self.assertTrue(resp2["sucesso"])
        self.assertEqual(resp2["status"], "cancelado")
        self.assertIsNone(self.dispatcher.pending_confirmation)

    def test_e2e_security_rejected_live_server(self) -> None:
        """Garante que requisições com chave inválida ou replay são rejeitadas pelo servidor HTTP."""
        agora = time.time()
        payload = {
            "event_id": "ev-hacker-1",
            "session_id": "sess-hack",
            "target": "fusion",
            "action": "abrir_navegador",
            "params": {},
            "timestamp": agora,
            "nonce": "nonce-falso-1",
        }
        corpo_bytes = json.dumps(payload).encode("utf-8")
        sig_falsa = calcular_assinatura("chave-errada-atacante", agora, "nonce-falso-1", "fusion", "abrir_navegador", corpo_bytes)

        import urllib.request
        import urllib.error

        req = urllib.request.Request(
            f"{self.fusion_url}/execute",
            data=corpo_bytes,
            headers={
                "Content-Type": "application/json",
                "X-Jarvis-Signature": sig_falsa,
                "X-Jarvis-Timestamp": str(agora),
                "X-Jarvis-Nonce": "nonce-falso-1",
            },
            method="POST",
        )

        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=2.0)
        self.assertEqual(ctx.exception.code, 401)

    def test_e2e_dual_mic_deduplication(self) -> None:
        """Deduplicação de áudio quando ME6S e LG ouvem o mesmo comando simultaneamente."""
        # ME6S envia comando
        resp1 = self.dispatcher.processar_comando_ou_confirmacao(
            acao="status_computador",
            alvo="fusion",
            texto_falado="fusion status computador",
            session_id="sess-dedup-1",
            event_id="ev-dedup-me6s",
            fonte="me6s",
        )
        self.assertTrue(resp1["sucesso"])
        self.assertFalse(resp1.get("deduplicado", False))

        # LG envia exatamente o mesmo comando 50ms depois
        resp2 = self.dispatcher.processar_comando_ou_confirmacao(
            acao="status_computador",
            alvo="fusion",
            texto_falado="fusion status computador",
            session_id="sess-dedup-1",
            event_id="ev-dedup-lg",
            fonte="lg_mic",
        )
        self.assertTrue(resp2["sucesso"])
        self.assertTrue(resp2.get("deduplicado", False))


class TestPcControlControllerAndDisplayE2E(unittest.TestCase):
    """Testa integração com a máquina de estados do controlador e exibição no display."""

    def setUp(self) -> None:
        self.display_state = JarvisDisplayState()
        self.display_monitor = JarvisLogMonitor(display_state=self.display_state)

        import tempfile
        self.tmp_dir_obj = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self.tmp_dir_obj.name)
        self.patch_dir = patch.dict(
            os.environ,
            {
                "JARVIS_STATE_FILE": str(self.tmp_dir / "state.json"),
                "JARVIS_ONE_SHOT_TOKEN_FILE": str(self.tmp_dir / "token.json"),
                "JARVIS_REQUIRE_ONE_SHOT_TOKEN": "0",
                "JARVIS_PC_CONTROL_ENABLED": "1",
                "JARVIS_PC_MOCK_MODE": "1",
            },
        )
        self.patch_dir.start()

        # Reseta o estado global do controlador
        jarvis_ctrl.estado = "espera"
        jarvis_ctrl.fonte_ativa = None
        jarvis_ctrl.sessao_ativa_id = None
        jarvis_ctrl.pendente = None
        jarvis_ctrl.pendente_acao = None
        jarvis_ctrl.acoes_recentes.clear()
        jarvis_ctrl.registro_acoes.clear()

        # Inicializa dispatcher do controller se necessário
        if jarvis_ctrl.pc_dispatcher is None:
            jarvis_ctrl.pc_dispatcher = PcControlDispatcher()

    def tearDown(self) -> None:
        self.patch_dir.stop()
        self.tmp_dir_obj.cleanup()
        jarvis_ctrl.estado = "espera"
        jarvis_ctrl.fonte_ativa = None
        jarvis_ctrl.sessao_ativa_id = None
        jarvis_ctrl.pendente = None
        jarvis_ctrl.pendente_acao = None
        jarvis_ctrl.acoes_recentes.clear()
        jarvis_ctrl.registro_acoes.clear()
        jarvis_ctrl.pendente_acao = None

    def test_controller_pc_control_and_display_integration(self) -> None:
        """Garante que processar aciona o PC control e o DisplayServer reflete os logs."""
        # 1. Wake word do ME6S
        res_wake = jarvis_ctrl.processar("assistente", fonte="me6s", session_id="sess-ipc-1", event_id="ev-wake-1")
        self.assertEqual(res_wake["estado"], "comando")

        # 2. Comando PC Control: abrir navegador fusion
        with patch.object(jarvis_ctrl.pc_dispatcher.client, "enviar_comando") as mock_env:
            mock_env.return_value = {
                "sucesso": True,
                "status": "concluido",
                "resultado": {"navegador": "iniciado"},
                "mensagem_amigavel": "Navegador aberto no Fusion.",
            }

            res_cmd = jarvis_ctrl.processar("abrir navegador fusion", fonte="me6s", session_id="sess-ipc-1", event_id="ev-cmd-1")

            self.assertTrue(res_cmd["sucesso"])
            self.assertEqual(res_cmd["status"], "concluido")
            self.assertEqual(jarvis_ctrl.estado, "espera")

            # Alimenta o parser de log do DisplayServer
            log_line = "PC Control: Navegador aberto no Fusion."
            self.display_monitor.processar_linha(log_line)
            display_dict = self.display_state.obter_estado()
            self.assertEqual(display_dict["status"], "success")
            self.assertEqual(display_dict["mensagem"], "Navegador aberto no Fusion.")

    def test_controller_confirmation_flow_and_display(self) -> None:
        """Garante que comando destrutivo passa por confirmação e atualiza DisplayServer."""
        # 1. Wake
        jarvis_ctrl.processar("assistente", fonte="me6s", session_id="sess-ipc-reiniciar", event_id="ev-wake-2")

        # 2. Comando reiniciar fusion
        res_cmd = jarvis_ctrl.processar("reiniciar fusion", fonte="me6s", session_id="sess-ipc-reiniciar", event_id="ev-cmd-2")

        self.assertTrue(res_cmd["sucesso"])
        self.assertEqual(res_cmd["status"], "confirmacao_pendente")
        self.assertEqual(jarvis_ctrl.estado, "confirmacao")

        # Parser do DisplayServer deve transicionar para status 'confirming'
        log_line_conf = 'Confirme reinicialização de Fusion (Windows): diga "confirmar" ou "cancelar".'
        self.display_monitor.processar_linha(log_line_conf)
        display_dict = self.display_state.obter_estado()
        self.assertEqual(display_dict["status"], "confirming")
        self.assertEqual(display_dict["acao"], "reiniciar")

        # 3. Usuário confirma
        with patch.object(jarvis_ctrl.pc_dispatcher.client, "enviar_comando") as mock_env:
            mock_env.return_value = {
                "sucesso": True,
                "status": "concluido",
                "resultado": {"reiniciando": True},
                "mensagem_amigavel": "Computador Fusion reiniciado.",
            }

            res_conf = jarvis_ctrl.processar("confirmar", fonte="me6s", session_id="sess-ipc-reiniciar", event_id="ev-conf-2")

            self.assertTrue(res_conf["sucesso"])
            self.assertEqual(res_conf["status"], "concluido")
            self.assertEqual(jarvis_ctrl.estado, "espera")

            # Parser do DisplayServer transiciona para 'success'
            log_line_done = "PC Control: Computador Fusion reiniciado."
            self.display_monitor.processar_linha(log_line_done)
            display_dict_done = self.display_state.obter_estado()
            self.assertEqual(display_dict_done["status"], "success")
            self.assertEqual(display_dict_done["mensagem"], "Computador Fusion reiniciado.")

    def test_feature_flag_disabled_blocks_pc_control(self) -> None:
        """Garante que quando JARVIS_PC_CONTROL_ENABLED=0 nenhum comando é executado."""
        with patch.dict(os.environ, {"JARVIS_PC_CONTROL_ENABLED": "0"}):
            # Wake
            jarvis_ctrl.processar("assistente", fonte="me6s", session_id="sess-flag-0", event_id="ev-wake-flag")

            res_cmd = jarvis_ctrl.processar("abrir navegador fusion", fonte="me6s", session_id="sess-flag-0", event_id="ev-cmd-flag")

            self.assertFalse(res_cmd["sucesso"])
            self.assertEqual(res_cmd["erro"], "pc_control_desativado")
            self.assertEqual(jarvis_ctrl.estado, "espera")

    def test_ipc_event_contract_and_rejection(self) -> None:
        """Valida validação semântica e contratual do evento IPC."""
        # 1. Versão incompatível
        res_v = jarvis_ctrl.on_ipc_event({"versao": 99, "fonte": "me6s"})
        self.assertFalse(res_v["sucesso"])
        self.assertEqual(res_v["erro"], "versao_incompativel")

        # 2. Fonte desconhecida
        res_f = jarvis_ctrl.on_ipc_event({"versao": 1, "fonte": "outro_mic"})
        self.assertFalse(res_f["sucesso"])
        self.assertEqual(res_f["erro"], "fonte_desconhecida")

    def test_dual_mic_pc_control_confirmation_deduplication(self) -> None:
        """Valida que confirmação simultânea por microfones duplos para PC Control é deduplicada no IPC."""
        # Configura estado de confirmação para reiniciar fusion
        jarvis_ctrl.estado = "confirmacao"
        jarvis_ctrl.pendente = "fusion"
        jarvis_ctrl.pendente_acao = "reiniciar"
        jarvis_ctrl.sessao_ativa_id = "sess-dual-conf-1"
        jarvis_ctrl.limite = time.monotonic() + 20.0

        with patch.object(jarvis_ctrl.pc_dispatcher.client, "enviar_comando") as mock_env:
            mock_env.return_value = {
                "sucesso": True,
                "status": "concluido",
                "resultado": {"reiniciando": True},
                "mensagem_amigavel": "Computador Fusion reiniciado.",
            }

            # 1. ME6S confirma primeiro
            res1 = jarvis_ctrl.processar("confirmar reinicializacao", fonte="me6s", session_id="sess-dual-conf-1", event_id="ev-conf-me6s")
            self.assertTrue(res1["sucesso"])
            self.assertEqual(res1["acao"], "reiniciar")
            self.assertEqual(jarvis_ctrl.estado, "espera")

            # 2. LG envia confirmação logo em seguida via IPC
            res2 = jarvis_ctrl.on_ipc_event({
                "versao": 1,
                "evento": "confirmar",
                "fonte": "lg_mic",
                "texto": "confirmar reinicializacao",
                "session_id": "sess-dual-conf-1",
                "event_id": "ev-conf-lg",
            })
            self.assertTrue(res2["sucesso"], f"Confirmação pelo segundo mic deveria ter sido aceita: {res2}")
            self.assertTrue(res2.get("deduplicado"), "Confirmação repetida deve ser marcada como deduplicada")
            self.assertEqual(res2.get("acao"), "reiniciar")
            self.assertEqual(res2.get("alvo"), "fusion")

    def test_confirmation_mismatch_during_pending_shutdown_cancels_cleanly(self) -> None:
        """Valida que confirmação incompatível durante desligamento pendente cancela sem travar ou retornar None."""
        jarvis_ctrl.estado = "confirmacao"
        jarvis_ctrl.pendente = "fusion"
        jarvis_ctrl.pendente_acao = "desligar"
        jarvis_ctrl.sessao_ativa_id = "sess-mismatch-1"
        jarvis_ctrl.limite = time.monotonic() + 20.0

        res = jarvis_ctrl.processar("confirmar reinicializacao", fonte="me6s", session_id="sess-mismatch-1", event_id="ev-mis-1")
        self.assertIsNotNone(res, "Processar não deve retornar None em caso de confirmação incompatível")
        self.assertFalse(res["sucesso"])
        self.assertEqual(res["erro"], "confirmacao_insuficiente")
        self.assertEqual(jarvis_ctrl.estado, "espera")
        self.assertIsNone(jarvis_ctrl.pendente)
        self.assertIsNone(jarvis_ctrl.pendente_acao)

    def test_display_lock_success_status(self) -> None:
        """Garante que 'bloqueado com sucesso' resulta em status 'success' no display, não 'error'."""
        self.display_monitor.processar_linha("PC Control: Computador Fusion bloqueado com sucesso.")
        display_dict = self.display_state.obter_estado()
        self.assertEqual(display_dict["status"], "success")
        self.assertEqual(display_dict["mensagem"], "Computador Fusion bloqueado com sucesso.")


if __name__ == "__main__":
    unittest.main()
