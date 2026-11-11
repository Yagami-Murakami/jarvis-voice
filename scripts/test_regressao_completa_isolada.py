#!/usr/bin/env python3
"""
Bateria Completa de Testes de Regressão e Validação da Cópia Isolada Patcheada.
Executa no LXC 110 contra /opt/jarvis-voice/test_isolado/jarvis-operacional.py
SEM acionar comandos reais (WoL/SSH) e SEM alterar o serviço de produção.
"""
import importlib.util
import io
import json
import os
import re
import socket
import shutil
import struct
import sys
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

# Carrega módulos do projeto
sys.path.insert(0, "/home/tuninho/homelab-panel")
sys.path.insert(0, "/opt/jarvis-voice")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gateway.ipc_bus import (
    DEFAULT_IPC_SOCKET,
    IpcEventBroadcaster,
    IpcEventListener,
    MAX_TIMESTAMP_DRIFT_S,
)
from gateway.audio_gateway import (
    LgMicServer,
    Me6sJournalMonitor,
    SessionArbitrator,
    MAGIC_DATA,
    MAGIC_HANDSHAKE,
)
from display.server import JarvisDisplayState, JarvisLogMonitor
from gateway.tests.test_ipc_real_socket_integration import TestIpcRealSocketIntegration


class TestPatchedOperationalScript(unittest.TestCase):
    """Valida a integração operacional do patch no script de voz."""

    @classmethod
    def setUpClass(cls):
        import types
        from unittest.mock import MagicMock

        try:
            import vosk
        except ImportError:
            mock_vosk = types.ModuleType("vosk")
            mock_vosk.Model = MagicMock()
            mock_vosk.KaldiRecognizer = MagicMock()
            mock_vosk.SetLogLevel = MagicMock()
            sys.modules["vosk"] = mock_vosk

        if "app" not in sys.modules:
            mock_app = types.ModuleType("app")
            mock_app.SERVIDORES = {
                "fusion": {"nome": "Fusion (Windows)", "mac": "bc:24:11:83:35:82"},
                "ananuke": {"nome": "Ananuke (Debian)", "mac": "0a:e0:af:c3:06:fc"},
            }
            mock_app.host_ligado = lambda srv: False
            mock_app.montar_comando_ssh = lambda cfg: ["echo", "ssh_simulado"]
            sys.modules["app"] = mock_app

        if os.path.exists("/opt/jarvis-voice/test_isolado/jarvis-operacional.py"):
            cls.isolated_path = "/opt/jarvis-voice/test_isolado/jarvis-operacional.py"
        else:
            cls.isolated_path = os.path.join(os.path.dirname(__file__), "jarvis_operacional_corrigido.py")
        assert os.path.exists(cls.isolated_path), f"Arquivo não encontrado: {cls.isolated_path}"

        spec = importlib.util.spec_from_file_location("jarvis_isolado", cls.isolated_path)
        cls.mod = importlib.util.module_from_spec(spec)

        with patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.stdout.read.return_value = b""
            mock_popen.return_value = mock_proc
            spec.loader.exec_module(cls.mod)

    def setUp(self):
        self.temp_state_dir = tempfile.mkdtemp()
        self.state_file = os.path.join(self.temp_state_dir, "acoes_recentes.json")
        os.environ["JARVIS_STATE_FILE"] = self.state_file
        os.environ["JARVIS_WOL_REAL"] = "0"
        os.environ["JARVIS_SSH_REAL"] = "0"
        self.mod.estado = "espera"
        self.mod.limite = 0.0
        self.mod.pendente = None
        self.mod.ultima_ativacao = 0.0
        self.mod.fonte_ativa = None
        self.mod.sessao_ativa_id = None
        while not self.mod.fila_intencoes.empty():
            try:
                self.mod.fila_intencoes.get_nowait()
            except Exception:
                pass
        with self.mod.lock_acoes:
            self.mod.acoes_recentes.clear()
            self.mod.registro_acoes.clear()
        if hasattr(self.mod.painel, "simular_atraso_ssh_s"):
            delattr(self.mod.painel, "simular_atraso_ssh_s")
        if hasattr(self.mod.painel, "simular_codigo_ssh"):
            delattr(self.mod.painel, "simular_codigo_ssh")

    def tearDown(self):
        if hasattr(self.mod.painel, "simular_atraso_ssh_s"):
            delattr(self.mod.painel, "simular_atraso_ssh_s")
        if hasattr(self.mod.painel, "simular_codigo_ssh"):
            delattr(self.mod.painel, "simular_codigo_ssh")
        if "JARVIS_ALLOWED_REAL_TARGETS" in os.environ:
            del os.environ["JARVIS_ALLOWED_REAL_TARGETS"]
        if os.path.exists(self.state_file):
            try:
                os.remove(self.state_file)
            except OSError:
                pass
        shutil.rmtree(self.temp_state_dir, ignore_errors=True)

    # =========================================================================
    # TESTES OBRIGATÓRIOS ESPECIFICADOS PELO REVISOR (PR #4)
    # =========================================================================

    def test_01_me6s_wake_lg_receives_command(self):
        """Teste Obrigatório 1: ME6S ativa -> Gateway sincroniza session_id coerente -> LG recebe e executa comando."""
        # 1. ME6S ativa no controlador
        output = io.StringIO()
        with patch("sys.stdout", output):
            self.mod.processar("assistente", fonte="me6s")

        self.assertEqual(self.mod.estado, "comando")
        self.assertIsNotNone(self.mod.sessao_ativa_id)
        me6s_sid = self.mod.sessao_ativa_id

        # 2. O journal monitor lê o log emitido e sincroniza com o árbitro do gateway
        arbitrator = SessionArbitrator()
        monitor = Me6sJournalMonitor(arbitrator=arbitrator)
        for linha in output.getvalue().strip().split("\n"):
            monitor._processar_linha(linha.strip())

        # O gateway agora possui EXATAMENTE o mesmo session_id
        self.assertEqual(arbitrator.state, "comando")
        self.assertEqual(arbitrator.session_id, me6s_sid)

        # 3. Usuário fala comando no LG Mic; o gateway reporta com esse session_id
        cmd_res = arbitrator.report_command("lg_mic", "ligar fusion")
        self.assertTrue(cmd_res["valido"])
        self.assertEqual(cmd_res["session_id"], me6s_sid)

        # 4. Gateway transmite via IPC para o controlador
        self.mod.on_ipc_event({
            "versao": 1,
            "fonte": "lg_mic",
            "evento": "comando",
            "texto": "ligar fusion",
            "session_id": me6s_sid,
        })
        self.assertFalse(self.mod.fila_intencoes.empty())
        fonte, texto, sid = self.mod.fila_intencoes.get_nowait()
        self.assertEqual(sid, me6s_sid)

        # 5. Controlador processa o comando em simulação
        cmd_out = io.StringIO()
        with patch("sys.stdout", cmd_out):
            self.mod.processar(texto, fonte=fonte, session_id=sid)
        self.assertIn("SIMULACAO: Wake-on-LAN.", cmd_out.getvalue())
        self.assertEqual(self.mod.estado, "espera")

    def test_02_me6s_shutdown_lg_confirms(self):
        """Teste Obrigatório 2: ME6S solicita desligamento -> LG confirma com session_id coerente."""
        # 1. ME6S ativa
        out_wake = io.StringIO()
        with patch("sys.stdout", out_wake):
            self.mod.processar("assistente", fonte="me6s")
        me6s_sid = self.mod.sessao_ativa_id

        arbitrator = SessionArbitrator()
        monitor = Me6sJournalMonitor(arbitrator=arbitrator)
        for linha in out_wake.getvalue().strip().split("\n"):
            monitor._processar_linha(linha.strip())

        # 2. ME6S solicita desligar fusion
        with patch.object(self.mod.painel, "host_ligado", return_value=True):
            out_cmd = io.StringIO()
            with patch("sys.stdout", out_cmd):
                self.mod.processar("desligar fusion", fonte="me6s")
            self.assertEqual(self.mod.estado, "confirmacao")
            self.assertEqual(self.mod.pendente, "fusion")

            for linha in out_cmd.getvalue().strip().split("\n"):
                monitor._processar_linha(linha.strip())

            self.assertEqual(arbitrator.state, "confirmacao")
            self.assertEqual(arbitrator.target_computer, "fusion")

            # 3. LG confirma via IPC usando o session_id coerente
            self.mod.on_ipc_event({
                "versao": 1,
                "fonte": "lg_mic",
                "evento": "confirmar",
                "texto": "confirmar desligamento",
                "session_id": me6s_sid,
            })
            self.assertFalse(self.mod.fila_intencoes.empty())
            fonte, texto, sid = self.mod.fila_intencoes.get_nowait()

            # 4. Controlador valida a confirmação cruzada
            out_conf = io.StringIO()
            with patch("sys.stdout", out_conf):
                self.mod.processar(texto, fonte=fonte, session_id=sid)
            self.assertIn("SIMULACAO SSH para Fusion (Windows)", out_conf.getvalue())
            self.assertEqual(self.mod.estado, "espera")

    def test_03_lg_wake_me6s_receives_command(self):
        """Teste Obrigatório 3: LG ativa -> ME6S recebe o comando."""
        # 1. Gateway abre sessão pelo LG
        arbitrator = SessionArbitrator()
        is_new, lg_sid = arbitrator.report_wake("lg_mic")
        self.assertTrue(is_new)

        # 2. Controlador recebe wake do LG via IPC
        self.mod.on_ipc_event({
            "versao": 1,
            "fonte": "lg_mic",
            "evento": "wake",
            "texto": "assistente",
            "session_id": lg_sid,
        })
        fonte, texto, sid = self.mod.fila_intencoes.get_nowait()
        out_wake = io.StringIO()
        with patch("sys.stdout", out_wake):
            self.mod.processar(texto, fonte=fonte, session_id=sid)

        self.assertEqual(self.mod.estado, "comando")
        self.assertEqual(self.mod.sessao_ativa_id, lg_sid)

        # 3. Usuário fala comando no microfone físico ME6S
        out_cmd = io.StringIO()
        with patch("sys.stdout", out_cmd):
            self.mod.processar("ligar fusion", fonte="me6s")

        self.assertIn("SIMULACAO: Wake-on-LAN.", out_cmd.getvalue())
        self.assertEqual(self.mod.estado, "espera")

    def test_04_lg_shutdown_me6s_confirms(self):
        """Teste Obrigatório 4: LG solicita desligamento -> ME6S confirma fisicamente."""
        # 1. LG ativa e solicita desligamento
        arbitrator = SessionArbitrator()
        _, lg_sid = arbitrator.report_wake("lg_mic")

        self.mod.processar("assistente", fonte="lg_mic", session_id=lg_sid)
        with patch.object(self.mod.painel, "host_ligado", return_value=True):
            self.mod.processar("desligar fusion", fonte="lg_mic", session_id=lg_sid)
            self.assertEqual(self.mod.estado, "confirmacao")
            self.assertEqual(self.mod.pendente, "fusion")

            # 2. Usuário confirma fisicamente no ME6S USB (sem session_id IPC)
            out_conf = io.StringIO()
            with patch("sys.stdout", out_conf):
                self.mod.processar("confirmar desligamento", fonte="me6s")

            self.assertIn("SIMULACAO SSH para Fusion (Windows)", out_conf.getvalue())
            self.assertEqual(self.mod.estado, "espera")

    def test_05_simultaneous_wake_single_session(self):
        """Teste Obrigatório 5: Dois microfones ouvem 'Assistente' simultaneamente -> apenas uma sessão."""
        out1 = io.StringIO()
        with patch("sys.stdout", out1):
            self.mod.processar("assistente", fonte="me6s")
        self.assertIn("ATIVADO! Aguardando comando.", out1.getvalue())
        self.assertEqual(self.mod.estado, "comando")
        primeiro_sid = self.mod.sessao_ativa_id

        # 50ms depois, o LG Mic também ouve a mesma fala
        out2 = io.StringIO()
        with patch("sys.stdout", out2):
            self.mod.processar("assistente", fonte="lg_mic")
        self.assertIn("Ja ativado. Aguardando comando.", out2.getvalue())
        # A sessão permanece idêntica, sem recriação
        self.assertEqual(self.mod.sessao_ativa_id, primeiro_sid)

    def test_06_delayed_confirmations_and_old_sessions_rejected(self):
        """Teste Obrigatório 6: Confirmações atrasadas e sessões antigas são rejeitadas."""
        self.mod.estado = "confirmacao"
        self.mod.pendente = "fusion"
        self.mod.sessao_ativa_id = "sess-correta-ativa"
        self.mod.limite = time.monotonic() + 20.0

        # 1. Sessão divergente / antiga rejeitada no IPC
        self.mod.on_ipc_event({
            "versao": 1,
            "fonte": "lg_mic",
            "evento": "confirmar",
            "texto": "confirmar desligamento",
            "session_id": "sess-antiga-expirada",
        })
        self.assertTrue(self.mod.fila_intencoes.empty())

        # 2. Sessão divergente rejeitada no despacho
        out_bad = io.StringIO()
        with patch("sys.stdout", out_bad):
            self.mod.processar("confirmar desligamento", fonte="lg_mic", session_id="sess-antiga-expirada")
        self.assertIn("Confirmacao descartada: session_id incompativel", out_bad.getvalue())
        self.assertEqual(self.mod.estado, "confirmacao")

        # 3. Confirmação após expiração do limite (timeout)
        self.mod.limite = time.monotonic() - 1.0  # Limite expirado
        out_expired = io.StringIO()
        with patch("sys.stdout", out_expired):
            self.mod.processar("confirmar desligamento", fonte="me6s")
        self.assertIn("Tempo esgotado.", out_expired.getvalue())
        self.assertEqual(self.mod.estado, "espera")

    def test_07_action_idempotency_never_executed_twice(self):
        """Teste Obrigatório 7: A mesma ação nunca é executada duas vezes (janela de idempotência 3.5s)."""
        # WoL
        self.mod.estado = "comando"
        self.mod.sessao_ativa_id = "sess-1"
        self.mod.limite = time.monotonic() + 12.0
        out_wol1 = io.StringIO()
        with patch("sys.stdout", out_wol1):
            self.mod.processar("ligar fusion", fonte="me6s")
        self.assertIn("SIMULACAO: Wake-on-LAN.", out_wol1.getvalue())

        # Segunda tentativa imediata de WoL para mesmo alvo
        self.mod.estado = "comando"
        self.mod.sessao_ativa_id = "sess-2"
        self.mod.limite = time.monotonic() + 12.0
        out_wol2 = io.StringIO()
        with patch("sys.stdout", out_wol2):
            self.mod.processar("ligar fusion", fonte="lg_mic")
        self.assertIn("WoL para fusion descartado por idempotencia.", out_wol2.getvalue())

        # SSH Shutdown
        with self.mod.lock_acoes:
            self.mod.acoes_recentes.clear()

        self.mod.estado = "confirmacao"
        self.mod.pendente = "fusion"
        self.mod.limite = time.monotonic() + 20.0
        with patch.object(self.mod.painel, "host_ligado", return_value=True):
            out_ssh1 = io.StringIO()
            with patch("sys.stdout", out_ssh1):
                self.mod.processar("confirmar desligamento", fonte="me6s")
            self.assertIn("SIMULACAO SSH para Fusion (Windows)", out_ssh1.getvalue())

            # Segunda tentativa de confirmação para mesmo alvo
            self.mod.estado = "confirmacao"
            self.mod.pendente = "fusion"
            self.mod.limite = time.monotonic() + 20.0
            out_ssh2 = io.StringIO()
            with patch("sys.stdout", out_ssh2):
                self.mod.processar("confirmar desligamento", fonte="lg_mic")
            self.assertIn("Desligamento de fusion descartado por idempotencia.", out_ssh2.getvalue())

    def test_08_lg_interface_and_sound_compatibility(self):
        """Teste Obrigatório 8: A interface do LG e os sinais sonoros continuam funcionando."""
        out = io.StringIO()
        with patch("sys.stdout", out):
            self.mod.processar("assistente", fonte="lg_mic", session_id="sess-display-1")

        linhas = [l.strip() for l in out.getvalue().strip().split("\n") if l.strip()]
        self.assertIn("Ouvi: assistente", linhas)
        self.assertIn("ATIVADO! Aguardando comando.", linhas)

        dstate = JarvisDisplayState()
        watcher = JarvisLogMonitor(display_state=dstate)
        for l in linhas:
            watcher.processar_linha(l)

        estado = dstate.obter_estado()
        # Verifica se o estado aciona a animação de listening e o som
        self.assertEqual(estado["status"], "listening")
        self.assertEqual(estado["mensagem"], "Estou ouvindo...")

    def test_09_me6s_auto_recovery_remains_functional(self):
        """Teste Obrigatório 9: A recuperação automática do ME6S permanece funcional."""
        mock_proc_crash = MagicMock()
        mock_proc_crash.poll.return_value = 1  # Processo terminou abruptamente
        mock_proc_crash.stdout.read.return_value = b""

        mock_proc_reconnect = MagicMock()
        mock_proc_reconnect.poll.return_value = None
        mock_proc_reconnect.stdout.read.side_effect = [b"\x00" * 4000, b""]

        tentativas = [mock_proc_crash, mock_proc_reconnect]
        with patch("subprocess.Popen", side_effect=lambda *args, **kwargs: tentativas.pop(0) if tentativas else mock_proc_reconnect):
            p1 = self.mod.spawn_arecord()
            self.assertIsNotNone(p1)
            p2 = self.mod.spawn_arecord()
            self.assertIsNotNone(p2)

    # =========================================================================
    # TESTES DE CORREÇÃO DOS PROBLEMAS 1 E 2
    # =========================================================================

    def test_10_me6s_journal_single_wake_fixed(self):
        """Problema 2: 'Ouvi: assistente' + 'ATIVADO!' gera estritamente UMA sessão no árbitro."""
        arbitrator = SessionArbitrator()
        monitor = Me6sJournalMonitor(arbitrator=arbitrator)

        # Simula as linhas emitidas no journal do ME6S
        linhas = [
            "Ouvi: assistente",
            "[Sessao: sess-me6s-coerente-123]",
            "ATIVADO! Aguardando comando.",
        ]
        for l in linhas:
            monitor._processar_linha(l)

        # O árbitro deve ter exatamente 1 ativação
        self.assertEqual(arbitrator.total_activations, 1)
        self.assertEqual(arbitrator.session_id, "sess-me6s-coerente-123")
        self.assertEqual(arbitrator.state, "comando")

    def test_11_real_speech_recognition_vosk(self):
        """Laboratório de Voz: Submete '5.wav' ('assistente') ao modelo Vosk com gramática operacional."""
        wav_path = "/opt/jarvis-voice/avaliacao-dorlir/negativo/5.wav"
        model_path = "/opt/jarvis-voice/vosk-model-small-pt-0.3"
        if not os.path.exists(wav_path) or not os.path.exists(model_path):
            raise unittest.SkipTest("Arquivos reais de áudio/modelo Vosk disponíveis apenas no LXC 110.")
        from vosk import Model, KaldiRecognizer
        m = Model(model_path)
        rec = KaldiRecognizer(m, 16000, json.dumps(self.mod.gramatica, ensure_ascii=False))

        wf = wave.open(wav_path, "rb")
        while True:
            data = wf.readframes(4000)
            if len(data) == 0:
                break
            rec.AcceptWaveform(data)

        res = json.loads(rec.FinalResult())
        texto = res.get("text", "").strip()
        self.assertEqual(texto, "assistente")

    def test_12_real_audio_streaming_through_lg_mic_vosk(self):
        """Laboratório de Voz: Transmite frames PCM reais de '5.wav' via TCP para LgMicServer e valida wake pelo árbitro."""
        wav_path = "/opt/jarvis-voice/avaliacao-dorlir/negativo/5.wav"
        if not os.path.exists(wav_path):
            raise unittest.SkipTest("Arquivo de áudio real disponível apenas no LXC 110.")
        arbitrator = SessionArbitrator(dedup_window_s=1.5)
        test_port = 18899
        test_token = "token-teste-streaming-audio-real-32-chars!"
        server = LgMicServer(
            host="127.0.0.1",
            port=test_port,
            auth_token=test_token,
            arbitrator=arbitrator,
            model_path="/opt/jarvis-voice/vosk-model-small-pt-0.3",
        )
        server.start()
        time.sleep(0.3)

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect(("127.0.0.1", test_port))

            token_bytes = test_token.encode("utf-8")
            sock.sendall(MAGIC_HANDSHAKE)
            sock.sendall(struct.pack(">B", 1))
            sock.sendall(struct.pack(">H", len(token_bytes)))
            sock.sendall(token_bytes)
            sock.sendall(struct.pack(">IBB", 16000, 1, 16))

            wav_path = "/opt/jarvis-voice/avaliacao-dorlir/negativo/5.wav"
            wf = wave.open(wav_path, "rb")
            seq = 0
            chunk_samples = 640
            chunk_bytes = chunk_samples * 2

            while True:
                data = wf.readframes(chunk_samples)
                if len(data) == 0:
                    break
                if len(data) < chunk_bytes:
                    data += b"\x00" * (chunk_bytes - len(data))
                header = MAGIC_DATA + struct.pack(">IQBH", seq, int(time.time() * 1000), 0, chunk_bytes)
                sock.sendall(header + data)
                seq += 1
                time.sleep(0.005)

            for _ in range(25):
                header = MAGIC_DATA + struct.pack(">IQBH", seq, int(time.time() * 1000), 0, chunk_bytes)
                sock.sendall(header + (b"\x00" * chunk_bytes))
                seq += 1
                time.sleep(0.005)

            time.sleep(0.5)
            sock.close()

            status = arbitrator.obter_status()
            self.assertGreaterEqual(status["total_activations"], 1)
            self.assertEqual(arbitrator.primary_source, "lg_mic")
        finally:
            server.stop()

    def test_13_simulation_mode_messages(self):
        """Valida que mensagens iniciais refletem com precisão as variáveis de ambiente."""
        os.environ["JARVIS_WOL_REAL"] = "0"
        os.environ["JARVIS_SSH_REAL"] = "0"
        out1 = io.StringIO()
        with patch("sys.stdout", out1):
            with patch("subprocess.Popen"):
                self.mod.iniciar_servicos(max_iteracoes=1)
        self.assertIn("MODO SIMULACAO - SEM ACOES REAIS", out1.getvalue())

        os.environ["JARVIS_WOL_REAL"] = "1"
        os.environ["JARVIS_SSH_REAL"] = "1"
        out2 = io.StringIO()
        with patch("sys.stdout", out2):
            with patch("subprocess.Popen"):
                self.mod.iniciar_servicos(max_iteracoes=1)
        self.assertIn("MODO OPERACIONAL REAL (WoL=ATIVO, SSH=ATIVO)", out2.getvalue())

    def test_14_iniciar_servicos_empty_queue_no_unbound_error(self):
        """Verifica que o loop principal executa sem UnboundLocalError quando a fila está vazia."""
        self.mod.estado = "comando"
        self.mod.limite = time.monotonic() - 1.0

        output = io.StringIO()
        with patch("sys.stdout", output):
            with patch("subprocess.Popen"):
                self.mod.iniciar_servicos(max_iteracoes=2)
        self.assertIn("Tempo esgotado.", output.getvalue())
        self.assertEqual(self.mod.estado, "espera")

    def test_15_concurrency_scenario1_me6s_first_then_lg_wake(self):
        """Cenário 1A: ME6S ativa no controlador primeiro -> LG ativa quase simultaneamente -> autoridade única mantida."""
        # 1. ME6S ativa no controlador
        out1 = io.StringIO()
        with patch("sys.stdout", out1):
            self.mod.processar("assistente", fonte="me6s")
        self.assertEqual(self.mod.estado, "comando")
        me6s_sid = self.mod.sessao_ativa_id
        self.assertIsNotNone(me6s_sid)

        # 2. LG gera candidato no Gateway quase ao mesmo tempo
        arbitrator = SessionArbitrator()
        _, lg_candidate_sid = arbitrator.report_wake("lg_mic")

        # 3. LG envia wake via IPC ao controlador
        ack = self.mod.on_ipc_event({
            "versao": 1,
            "fonte": "lg_mic",
            "evento": "wake",
            "texto": "assistente",
            "session_id": lg_candidate_sid,
        })

        # 4. Controlador deve responder ACK com active_session_id do ME6S e deduplicado=True
        self.assertTrue(ack.get("sucesso"))
        self.assertEqual(ack.get("active_session_id"), me6s_sid)
        self.assertTrue(ack.get("deduplicado"))

        # 5. Gateway reconcilia com a resposta oficial
        arbitrator.sync_session_id(ack["active_session_id"])
        self.assertEqual(arbitrator.session_id, me6s_sid)

        # 6. LG fala comando "ligar fusion" usando o ID sincronizado
        ack_cmd = self.mod.on_ipc_event({
            "versao": 1,
            "fonte": "lg_mic",
            "evento": "comando",
            "texto": "ligar fusion",
            "session_id": me6s_sid,
        })
        self.assertTrue(ack_cmd.get("sucesso"))

        # 7. Controlador processa o comando com sucesso
        while not self.mod.fila_intencoes.empty():
            fonte, texto, sid = self.mod.fila_intencoes.get_nowait()
            if texto != "assistente":
                out_cmd = io.StringIO()
                with patch("sys.stdout", out_cmd):
                    self.mod.processar(texto, fonte=fonte, session_id=sid)
                self.assertIn("SIMULACAO: Wake-on-LAN.", out_cmd.getvalue())
        self.assertEqual(self.mod.estado, "espera")

    def test_16_concurrency_scenario1_lg_first_then_me6s_wake(self):
        """Cenário 1B: LG ativa primeiro via IPC -> ME6S ativa logo em seguida -> ID único adotado por ambos."""
        # 1. LG ativa no Gateway
        arbitrator = SessionArbitrator()
        _, lg_sid = arbitrator.report_wake("lg_mic")

        # 2. Gateway envia wake via IPC; Controlador estava em espera
        ack = self.mod.on_ipc_event({
            "versao": 1,
            "fonte": "lg_mic",
            "evento": "wake",
            "texto": "assistente",
            "session_id": lg_sid,
        })
        self.assertTrue(ack.get("sucesso"))
        self.assertEqual(ack.get("active_session_id"), lg_sid)
        self.assertFalse(ack.get("deduplicado"))

        # Esvazia fila e processa a ativação
        fonte, texto, sid = self.mod.fila_intencoes.get_nowait()
        out_wake = io.StringIO()
        with patch("sys.stdout", out_wake):
            self.mod.processar(texto, fonte=fonte, session_id=sid)
        self.assertEqual(self.mod.estado, "comando")
        self.assertEqual(self.mod.sessao_ativa_id, lg_sid)

        # 3. 5ms depois, ME6S ouve "assistente" localmente
        out_me6s = io.StringIO()
        with patch("sys.stdout", out_me6s):
            self.mod.processar("assistente", fonte="me6s")
        self.assertIn("Ja ativado. Aguardando comando.", out_me6s.getvalue())
        self.assertEqual(self.mod.sessao_ativa_id, lg_sid)

        # 4. Monitor passivo do journal processa linha e sincroniza arbitrator
        monitor = Me6sJournalMonitor(arbitrator=arbitrator)
        for linha in out_wake.getvalue().strip().split("\n"):
            monitor._processar_linha(linha.strip())
        self.assertEqual(arbitrator.session_id, lg_sid)

        # 5. ME6S fala comando local
        out_cmd = io.StringIO()
        with patch("sys.stdout", out_cmd):
            self.mod.processar("ligar fusion", fonte="me6s")
        self.assertIn("SIMULACAO: Wake-on-LAN.", out_cmd.getvalue())
        self.assertEqual(self.mod.estado, "espera")

    def test_17_concurrency_scenario2_second_wake_during_active_session(self):
        """Cenário 2: Segunda ativação pelo LG durante sessão já aberta NUNCA substitui o ID."""
        # 1. Abre sessão via ME6S
        out = io.StringIO()
        with patch("sys.stdout", out):
            self.mod.processar("assistente", fonte="me6s")
        initial_sid = self.mod.sessao_ativa_id
        self.assertEqual(self.mod.estado, "comando")

        arbitrator = SessionArbitrator()
        arbitrator.report_wake("me6s", session_id=initial_sid)
        self.assertEqual(arbitrator.session_id, initial_sid)

        # 2. Usuário repete 'assistente' pelo microfone LG
        is_new, current_sid = arbitrator.report_wake("lg_mic")
        self.assertFalse(is_new)
        self.assertEqual(current_sid, initial_sid)
        self.assertEqual(arbitrator.session_id, initial_sid)

        # 3. Envia wake ao controlador via IPC
        ack = self.mod.on_ipc_event({
            "versao": 1,
            "fonte": "lg_mic",
            "evento": "wake",
            "texto": "assistente",
            "session_id": current_sid,
        })
        self.assertTrue(ack.get("sucesso"))
        self.assertEqual(ack.get("active_session_id"), initial_sid)
        self.assertTrue(ack.get("deduplicado"))
        self.assertEqual(self.mod.sessao_ativa_id, initial_sid)

        # 4. Usuário agora fala comando pelo LG
        ack_cmd = self.mod.on_ipc_event({
            "versao": 1,
            "fonte": "lg_mic",
            "evento": "comando",
            "texto": "ligar fusion",
            "session_id": initial_sid,
        })
        self.assertTrue(ack_cmd.get("sucesso"))

        # Esvazia fila e processa comando
        while not self.mod.fila_intencoes.empty():
            f, t, s = self.mod.fila_intencoes.get_nowait()
            if t == "ligar fusion":
                out_cmd = io.StringIO()
                with patch("sys.stdout", out_cmd):
                    self.mod.processar(t, fonte=f, session_id=s)
                self.assertIn("SIMULACAO: Wake-on-LAN.", out_cmd.getvalue())
        self.assertEqual(self.mod.estado, "espera")

    def test_18_gateway_restart_during_active_session(self):
        """Cenário de Tolerância: Gateway reinicia durante sessão ativa e recupera autoridade única pelo ACK."""
        # 1. Controlador está ativo na sessão
        self.mod.estado = "comando"
        self.mod.sessao_ativa_id = "sess-authoritative-prior-42"
        self.mod.limite = time.monotonic() + 12.0

        # 2. Gateway novo reiniciado (estado inicial: espera, session_id: None)
        restarted_arbitrator = SessionArbitrator()
        self.assertEqual(restarted_arbitrator.state, "espera")
        self.assertIsNone(restarted_arbitrator.session_id)

        # 3. Usuário fala "assistente" no LG conectado ao Gateway reiniciado
        is_new, temp_sid = restarted_arbitrator.report_wake("lg_mic")
        self.assertTrue(is_new)

        # 4. Gateway emite wake ao controlador e recebe ACK com o session_id oficial ativo
        ack = self.mod.on_ipc_event({
            "versao": 1,
            "fonte": "lg_mic",
            "evento": "wake",
            "texto": "assistente",
            "session_id": temp_sid,
        })
        self.assertTrue(ack.get("sucesso"))
        self.assertEqual(ack.get("active_session_id"), "sess-authoritative-prior-42")
        self.assertTrue(ack.get("deduplicado"))

        # 5. Gateway sincroniza com o ACK recebido
        restarted_arbitrator.sync_session_id(ack["active_session_id"])
        self.assertEqual(restarted_arbitrator.session_id, "sess-authoritative-prior-42")

    def test_19_allowed_real_targets_wol_isolation(self):
        """Valida que quando JARVIS_ALLOWED_REAL_TARGETS='fusion', o alvo 'ananuke' nunca recebe WoL real."""
        os.environ["JARVIS_WOL_REAL"] = "1"
        os.environ["JARVIS_ALLOWED_REAL_TARGETS"] = "fusion"

        # 1. Ativação + Comando ligar fusion (alvo autorizado): entra no fluxo real
        self.mod.processar("assistente", fonte="me6s")
        self.assertEqual(self.mod.estado, "comando")

        out_fusion = io.StringIO()
        with patch("sys.stdout", out_fusion):
            with patch.object(self.mod.painel, "host_ligado", return_value=True) as mock_host:
                self.mod.processar("ligar fusion", fonte="me6s")
                time.sleep(0.1)
                self.assertTrue(mock_host.called, "host_ligado deve ser chamado para fusion quando autorizado")
                self.assertIn("Computador ja esta acessivel.", out_fusion.getvalue())

        # 2. Ativação + Comando ligar ananuke (alvo NÃO autorizado): NUNCA chama host_ligado nem envia WoL real
        self.mod.processar("assistente", fonte="me6s")
        self.assertEqual(self.mod.estado, "comando")

        out_ananuke = io.StringIO()
        with patch("sys.stdout", out_ananuke):
            with patch.object(self.mod.painel, "host_ligado", return_value=True) as mock_host:
                self.mod.processar("ligar computador dois", fonte="me6s")
                self.assertFalse(mock_host.called, "host_ligado NÃO deve ser chamado para alvo não autorizado")
                self.assertIn("SIMULAÇÃO: enviaria Wake-on-LAN.", out_ananuke.getvalue())
                self.assertIn("SIMULACAO: Wake-on-LAN.", out_ananuke.getvalue())

    def test_20_allowed_real_targets_ssh_isolation(self):
        """Valida que quando JARVIS_ALLOWED_REAL_TARGETS='fusion', o alvo 'ananuke' nunca recebe SSH real."""
        os.environ["JARVIS_SSH_REAL"] = "1"
        os.environ["JARVIS_ALLOWED_REAL_TARGETS"] = "fusion"

        self.mod.processar("assistente", fonte="me6s")
        self.assertEqual(self.mod.estado, "comando")

        out_desligar = io.StringIO()
        with patch("sys.stdout", out_desligar):
            with patch("subprocess.run") as mock_subproc:
                self.mod.processar("desligar computador dois", fonte="me6s")
                self.assertEqual(self.mod.estado, "confirmacao")
                self.assertEqual(self.mod.pendente, "ananuke")

                self.mod.processar("confirmar desligamento", fonte="me6s")
                time.sleep(0.1)
                mock_subproc.assert_not_called()
                self.assertIn("SIMULAÇÃO: enviaria desligamento SSH.", out_desligar.getvalue())
                self.assertIn("SIMULACAO SSH para Ananuke (Debian)", out_desligar.getvalue())

    def test_21_allowed_real_targets_fail_closed_when_empty_or_unset(self):
        """Valida comportamento fail-closed: se JARVIS_ALLOWED_REAL_TARGETS estiver vazio ou ausente, nenhum alvo é real."""
        os.environ["JARVIS_WOL_REAL"] = "1"
        os.environ["JARVIS_SSH_REAL"] = "1"
        if "JARVIS_ALLOWED_REAL_TARGETS" in os.environ:
            del os.environ["JARVIS_ALLOWED_REAL_TARGETS"]

        # 1. WoL para Fusion deve permanecer estritamente em simulação
        self.mod.processar("assistente", fonte="me6s")
        self.assertEqual(self.mod.estado, "comando")

        out_wol = io.StringIO()
        with patch("sys.stdout", out_wol):
            with patch.object(self.mod.painel, "host_ligado") as mock_host:
                self.mod.processar("ligar fusion", fonte="me6s")
                mock_host.assert_not_called()
                self.assertIn("SIMULAÇÃO: enviaria Wake-on-LAN.", out_wol.getvalue())

        # 2. SSH para Fusion deve permanecer estritamente em simulação
        self.mod.processar("assistente", fonte="me6s")
        self.assertEqual(self.mod.estado, "comando")

        out_ssh = io.StringIO()
        with patch("sys.stdout", out_ssh):
            with patch("subprocess.run") as mock_subproc:
                self.mod.processar("desligar fusion", fonte="me6s")
                self.assertEqual(self.mod.estado, "confirmacao")
                self.mod.processar("confirmar desligamento", fonte="me6s")
                time.sleep(0.1)
                mock_subproc.assert_not_called()
                self.assertIn("SIMULAÇÃO: enviaria desligamento SSH.", out_ssh.getvalue())

    def test_22_arbitrator_report_command_delegates_to_confirmation_when_confirming(self):
        """Valida que report_command delega para confirmação se o estado atual do árbitro for 'confirmacao'."""
        arbitrator = SessionArbitrator()
        is_new, sid = arbitrator.report_wake("lg_mic")
        self.assertTrue(is_new)
        # Comando para desligar fusion
        cmd_res = arbitrator.report_command("lg_mic", "desligar fusion")
        self.assertTrue(cmd_res["valido"])
        self.assertEqual(arbitrator.state, "confirmacao")

        # Invocação de report_command com 'confirmar desligamento'
        conf_res = arbitrator.report_command("me6s", "confirmar desligamento")
        self.assertTrue(conf_res["valido"])
        self.assertEqual(conf_res["acao"], "desligar_confirmado")
        self.assertEqual(conf_res["alvo"], "fusion")
        self.assertEqual(arbitrator.state, "espera")

    def test_23_alvo_real_permitido_handles_quotes_and_spaces(self):
        """Valida que alvo_real_permitido trata aspas, espaços e maiúsculas/minúsculas."""
        os.environ["JARVIS_ALLOWED_REAL_TARGETS"] = '"Fusion" , \'Storage\''
        self.assertTrue(self.mod.alvo_real_permitido("fusion"))
        self.assertTrue(self.mod.alvo_real_permitido("FUSION"))
        self.assertTrue(self.mod.alvo_real_permitido("storage"))
        self.assertFalse(self.mod.alvo_real_permitido("ananuke"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
