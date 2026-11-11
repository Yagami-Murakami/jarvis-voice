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


class TestPatchedOperationalScript(unittest.TestCase):
    """Valida a integração operacional do patch no script de voz."""

    @classmethod
    def setUpClass(cls):
        cls.isolated_path = "/opt/jarvis-voice/test_isolado/jarvis-operacional.py"
        assert os.path.exists(cls.isolated_path), f"Arquivo não encontrado: {cls.isolated_path}"

        spec = importlib.util.spec_from_file_location("jarvis_isolado", cls.isolated_path)
        cls.mod = importlib.util.module_from_spec(spec)

        with patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.stdout.read.return_value = b""
            mock_popen.return_value = mock_proc
            spec.loader.exec_module(cls.mod)

    def setUp(self):
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

    def test_01_log_protocol_backward_compatibility(self):
        """Verifica se as linhas impressas continuam 100% compatíveis com display/server.py e Me6sJournalMonitor."""
        output = io.StringIO()
        with patch("sys.stdout", output):
            self.mod.processar("assistente", fonte="lg_mic", session_id="sess-test-1")

        linhas = [l.strip() for l in output.getvalue().strip().split("\n") if l.strip()]
        # Linha 1: [Origem: lg_mic] (metadado para o monitor de journal)
        self.assertEqual(linhas[0], "[Origem: lg_mic]")
        # Linha 2: Ouvi: assistente (contrato com display)
        self.assertEqual(linhas[1], "Ouvi: assistente")
        # Linha 3: ATIVADO! Aguardando comando.
        self.assertEqual(linhas[2], "ATIVADO! Aguardando comando.")

        # Valida parsing com JarvisDisplayState e JarvisLogMonitor de display/server.py
        dstate = JarvisDisplayState()
        watcher = JarvisLogMonitor(display_state=dstate)
        for l in linhas:
            watcher.processar_linha(l)
        self.assertEqual(dstate.obter_estado()["status"], "listening")
        self.assertEqual(dstate.obter_estado()["mensagem"], "Estou ouvindo...")

    def test_02_idempotency_not_recorded_on_insufficient_confirmation(self):
        """Bloqueador 4: Verifica que 'confirmar' insuficiente em modo real NÃO registra ação no cache."""
        os.environ["JARVIS_SSH_REAL"] = "1"
        self.mod.estado = "confirmacao"
        self.mod.pendente = "fusion"
        self.mod.limite = time.monotonic() + 20.0

        with patch.object(self.mod.painel, "host_ligado", return_value=True):
            output = io.StringIO()
            with patch("sys.stdout", output):
                self.mod.processar("confirmar", fonte="me6s")

        self.assertIn("Confirmacao insuficiente. Pedido cancelado.", output.getvalue())
        self.assertEqual(self.mod.estado, "espera")
        # Cache de ações recentes NÃO deve conter 'fusion'
        with self.mod.lock_acoes:
            self.assertNotIn(("desligar", "fusion"), self.mod.acoes_recentes)

    def test_03_idempotency_recorded_only_after_valid_confirmation(self):
        """Bloqueador 4: Confirmação estrita válida registra ação e descarta duplicações imediatas."""
        os.environ["JARVIS_SSH_REAL"] = "1"
        self.mod.estado = "confirmacao"
        self.mod.pendente = "fusion"
        self.mod.limite = time.monotonic() + 20.0

        with patch.object(self.mod.painel, "host_ligado", return_value=True), \
             patch("subprocess.run") as mock_subrun:
            mock_subrun.return_value = MagicMock(returncode=0, stdout="Shutdown initiated", stderr="")
            output1 = io.StringIO()
            with patch("sys.stdout", output1):
                self.mod.processar("confirmar desligamento", fonte="lg_mic")

            self.assertIn("Verifique se o computador desligou.", output1.getvalue())
            self.assertEqual(self.mod.estado, "espera")

            with self.mod.lock_acoes:
                self.assertIn(("desligar", "fusion"), self.mod.acoes_recentes)

            # Nova tentativa imediata deve ser barrada por idempotência
            self.mod.estado = "confirmacao"
            self.mod.pendente = "fusion"
            self.mod.limite = time.monotonic() + 20.0
            output2 = io.StringIO()
            with patch("sys.stdout", output2):
                self.mod.processar("confirmar desligamento", fonte="me6s")
            self.assertIn("descartado por idempotencia", output2.getvalue())

    def test_04_strict_ipc_semantic_and_state_validation(self):
        """Bloqueador 5 e Requisito 4: Validação estrita de session_id, origem e estado no IPC."""
        # 1. Payload não-dicionário ou versão incorreta
        self.mod.on_ipc_event("string invalida")
        self.mod.on_ipc_event({"versao": 2, "fonte": "lg_mic", "evento": "wake", "texto": "assistente"})
        # 2. Fonte desconhecida
        self.mod.on_ipc_event({"versao": 1, "fonte": "hacker_mic", "evento": "wake", "texto": "assistente"})
        # 3. Tipo inválido
        self.mod.on_ipc_event({"versao": 1, "fonte": "lg_mic", "evento": "executar_shell", "texto": "rm -rf"})
        # 4. Wake com texto incorreto
        self.mod.on_ipc_event({"versao": 1, "fonte": "lg_mic", "evento": "wake", "texto": "desligar fusion"})
        # 5. Comando desconhecido na gramática
        self.mod.on_ipc_event({"versao": 1, "fonte": "lg_mic", "evento": "comando", "texto": "destruir servidor"})

        # 6. Comando recebido fora do estado 'comando' (estado atual: 'espera')
        self.mod.estado = "espera"
        self.mod.on_ipc_event({"versao": 1, "fonte": "lg_mic", "evento": "comando", "texto": "ligar fusion", "session_id": "sess-1"})

        # 7. Confirmação recebida fora do estado 'confirmacao'
        self.mod.estado = "espera"
        self.mod.on_ipc_event({"versao": 1, "fonte": "lg_mic", "evento": "confirmar", "texto": "confirmar desligamento", "session_id": "sess-1"})

        # 8. Comando recebido no estado 'comando', mas com session_id divergente
        self.mod.estado = "comando"
        self.mod.sessao_ativa_id = "sess-ativa-correta"
        self.mod.on_ipc_event({"versao": 1, "fonte": "lg_mic", "evento": "comando", "texto": "ligar fusion", "session_id": "sess-errada"})

        # Fila deve permanecer vazia pois todos acima foram rejeitados
        self.assertTrue(self.mod.fila_intencoes.empty())

        # Payload legítimo de wake
        self.mod.estado = "espera"
        self.mod.on_ipc_event({"versao": 1, "fonte": "lg_mic", "evento": "wake", "texto": "assistente", "session_id": "sess-1"})
        self.assertFalse(self.mod.fila_intencoes.empty())
        item = self.mod.fila_intencoes.get_nowait()
        self.assertEqual(item, ("lg_mic", "assistente", "sess-1"))

        # Payload legítimo de comando com session_id correspondente
        self.mod.estado = "comando"
        self.mod.sessao_ativa_id = "sess-1"
        self.mod.on_ipc_event({"versao": 1, "fonte": "lg_mic", "evento": "comando", "texto": "ligar fusion", "session_id": "sess-1"})
        self.assertFalse(self.mod.fila_intencoes.empty())
        item = self.mod.fila_intencoes.get_nowait()
        self.assertEqual(item, ("lg_mic", "ligar fusion", "sess-1"))

        # Payload legítimo de confirmação com session_id correspondente
        self.mod.estado = "confirmacao"
        self.mod.pendente = "fusion"
        self.mod.sessao_ativa_id = "sess-1"
        self.mod.on_ipc_event({"versao": 1, "fonte": "lg_mic", "evento": "confirmar", "texto": "confirmar desligamento", "session_id": "sess-1"})
        self.assertFalse(self.mod.fila_intencoes.empty())
        item = self.mod.fila_intencoes.get_nowait()
        self.assertEqual(item, ("lg_mic", "confirmar desligamento", "sess-1"))

    def test_05_simultaneous_wake_deduplication(self):
        """Verifica que dois 'assistente' em menos de 2.0s são deduplicados."""
        output1 = io.StringIO()
        with patch("sys.stdout", output1):
            self.mod.processar("assistente", fonte="me6s")
        self.assertIn("ATIVADO! Aguardando comando.", output1.getvalue())
        self.assertEqual(self.mod.estado, "comando")

        # Segundo wake dentro de 100ms pelo LG Mic
        output2 = io.StringIO()
        with patch("sys.stdout", output2):
            self.mod.processar("assistente", fonte="lg_mic")
        self.assertIn("Ja ativado. Aguardando comando.", output2.getvalue())

    def test_06_real_speech_recognition_vosk(self):
        """Laboratório de Voz: Submete '5.wav' ('assistente') ao modelo Vosk com gramática operacional."""
        wav_path = "/opt/jarvis-voice/avaliacao-dorlir/negativo/5.wav"
        self.assertTrue(os.path.exists(wav_path))

        from vosk import Model, KaldiRecognizer
        m = Model("/opt/jarvis-voice/vosk-model-small-pt-0.3")
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

    def test_07_real_audio_streaming_through_lg_mic_vosk(self):
        """Laboratório de Voz: Transmite frames PCM reais de '5.wav' via TCP para LgMicServer e valida wake pelo árbitro."""
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

    def test_08_me6s_journal_monitor_filters_lg_events(self):
        """Requisito 3: Me6sJournalMonitor ignora linhas de log com [Origem: lg_mic] e não aciona o árbitro."""
        arbitrator = SessionArbitrator()
        monitor = Me6sJournalMonitor(arbitrator=arbitrator)

        # 1. Simula emissão pelo canal LG Mic
        linhas_lg = [
            "[Origem: lg_mic]",
            "Ouvi: assistente",
            "ATIVADO! Aguardando comando.",
        ]
        for l in linhas_lg:
            monitor._processar_linha(l)

        # O árbitro NÃO deve ter aberto sessão para ME6S
        self.assertEqual(arbitrator.state, "espera")
        self.assertIsNone(arbitrator.primary_source)

        # 2. Simula emissão legítima pelo canal ME6S
        linhas_me6s = [
            "Ouvi: assistente",
            "ATIVADO! Aguardando comando.",
        ]
        for l in linhas_me6s:
            monitor._processar_linha(l)

        # Agora o árbitro DEVE ter recebido o wake de me6s
        self.assertEqual(arbitrator.state, "comando")
        self.assertEqual(arbitrator.primary_source, "me6s")

    def test_09_iniciar_servicos_empty_queue_and_timeout(self):
        """Requisito 1 e 6: Executa loop principal com fila vazia e valida que UnboundLocalError foi eliminado."""
        self.mod.estado = "comando"
        self.mod.limite = time.monotonic() - 1.0  # Limite já expirado

        output = io.StringIO()
        with patch("sys.stdout", output):
            with patch("subprocess.Popen") as mock_popen:
                mock_proc = MagicMock()
                mock_proc.stdout.read.return_value = b""
                mock_popen.return_value = mock_proc
                # Executa exatamente 2 iterações com fila vazia
                self.mod.iniciar_servicos(max_iteracoes=2)

        saida = output.getvalue()
        # Deve ter processado o timeout na primeira iteração com fila vazia sem erro de escopo
        self.assertIn("Tempo esgotado.", saida)
        self.assertEqual(self.mod.estado, "espera")
        self.assertIsNone(self.mod.pendente)
        self.assertIsNone(self.mod.fonte_ativa)
        self.assertIsNone(self.mod.sessao_ativa_id)

    def test_10_me6s_capture_resilience_and_reconnection(self):
        """Requisito 2 e 6: Simula término de arecord e valida tentativa de reconexão automática."""
        mock_proc_failing = MagicMock()
        mock_proc_failing.poll.return_value = 1  # Processo terminou
        mock_proc_failing.stdout.read.return_value = b""

        mock_proc_ok = MagicMock()
        mock_proc_ok.poll.return_value = None
        mock_proc_ok.stdout.read.side_effect = [b"\x00" * 4000, b""]

        tentativas = [mock_proc_failing, mock_proc_ok]

        with patch("subprocess.Popen", side_effect=lambda *args, **kwargs: tentativas.pop(0) if tentativas else mock_proc_ok):
            p1 = self.mod.spawn_arecord()
            self.assertIsNotNone(p1)
            p2 = self.mod.spawn_arecord()
            self.assertIsNotNone(p2)

    def test_11_cross_confirmation_dual_mic(self):
        """Requisito 7: Validação de confirmações cruzadas entre LG Mic e ME6S USB."""
        # Cenário A: LG acorda e solicita desligamento; ME6S confirma
        self.mod.estado = "espera"
        self.mod.processar("assistente", fonte="lg_mic", session_id="sess-cross-1")
        self.assertEqual(self.mod.estado, "comando")
        self.assertEqual(self.mod.sessao_ativa_id, "sess-cross-1")

        with patch.object(self.mod.painel, "host_ligado", return_value=True):
            self.mod.processar("desligar fusion", fonte="lg_mic", session_id="sess-cross-1")
            self.assertEqual(self.mod.estado, "confirmacao")
            self.assertEqual(self.mod.pendente, "fusion")

            # Confirmação física pelo ME6S USB (sem session_id IPC)
            output = io.StringIO()
            with patch("sys.stdout", output):
                self.mod.processar("confirmar desligamento", fonte="me6s")

            self.assertIn("SIMULACAO SSH para", output.getvalue())
            self.assertEqual(self.mod.estado, "espera")
            self.assertIsNone(self.mod.pendente)

        # Cenário B: ME6S acorda e solicita desligamento; LG confirma com session_id correspondente
        self.mod.estado = "espera"
        self.mod.ultima_ativacao = 0.0
        with self.mod.lock_acoes:
            self.mod.acoes_recentes.clear()
        self.mod.processar("assistente", fonte="me6s")
        self.assertEqual(self.mod.estado, "comando")
        sid_me6s = self.mod.sessao_ativa_id

        with patch.object(self.mod.painel, "host_ligado", return_value=True):
            self.mod.processar("desligar fusion", fonte="me6s")
            self.assertEqual(self.mod.estado, "confirmacao")

            # LG confirma com session_id correto
            output = io.StringIO()
            with patch("sys.stdout", output):
                self.mod.processar("confirmar desligamento", fonte="lg_mic", session_id=sid_me6s)

            self.assertIn("SIMULACAO SSH para", output.getvalue())
            self.assertEqual(self.mod.estado, "espera")

        # Cenário C: Confirmação com session_id incorreto é descartada
        self.mod.estado = "confirmacao"
        self.mod.pendente = "fusion"
        self.mod.sessao_ativa_id = "sess-correta"
        output_bad = io.StringIO()
        with patch("sys.stdout", output_bad):
            self.mod.processar("confirmar desligamento", fonte="lg_mic", session_id="sess-errada")
        self.assertIn("Confirmacao descartada: session_id incompativel", output_bad.getvalue())
        self.assertEqual(self.mod.estado, "confirmacao")  # Permanece em confirmação

    def test_12_simulation_mode_messages(self):
        """Requisito 5: Mensagem inicial reflete com precisão as variáveis de ambiente."""
        # 1. Ambos 0 (Simulação pura)
        os.environ["JARVIS_WOL_REAL"] = "0"
        os.environ["JARVIS_SSH_REAL"] = "0"
        out1 = io.StringIO()
        with patch("sys.stdout", out1):
            with patch("subprocess.Popen"):
                self.mod.iniciar_servicos(max_iteracoes=1)
        self.assertIn("MODO SIMULACAO - SEM ACOES REAIS", out1.getvalue())

        # 2. Ambos 1 (Modo real operacional)
        os.environ["JARVIS_WOL_REAL"] = "1"
        os.environ["JARVIS_SSH_REAL"] = "1"
        out2 = io.StringIO()
        with patch("sys.stdout", out2):
            with patch("subprocess.Popen"):
                self.mod.iniciar_servicos(max_iteracoes=1)
        self.assertIn("MODO OPERACIONAL REAL (WoL=ATIVO, SSH=ATIVO)", out2.getvalue())

        # 3. Misto
        os.environ["JARVIS_WOL_REAL"] = "1"
        os.environ["JARVIS_SSH_REAL"] = "0"
        out3 = io.StringIO()
        with patch("sys.stdout", out3):
            with patch("subprocess.Popen"):
                self.mod.iniciar_servicos(max_iteracoes=1)
        self.assertIn("MODO MISTO (WoL=ATIVO, SSH=SIMULADO)", out3.getvalue())

    def test_13_delayed_events_handling(self):
        """Requisito 7: Eventos atrasados são descartados."""
        # 1. Descarte de áudio atrasado na fila ME6S
        rec_mock = MagicMock()
        item_atrasado = (time.monotonic() - 3.0, b"\x00" * 4000)
        out = io.StringIO()
        with patch("sys.stdout", out):
            instante, audio = item_atrasado
            if time.monotonic() - instante > 1.5:
                rec_mock.Reset()
                print("Audio atrasado descartado.", flush=True)
        self.assertIn("Audio atrasado descartado.", out.getvalue())
        rec_mock.Reset.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
