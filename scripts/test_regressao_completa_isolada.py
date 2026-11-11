#!/usr/bin/env python3
"""
Bateria de Testes de Regressão e Validação da Cópia Isolada Patcheada.
Executa no LXC 110 contra /opt/jarvis-voice/test_isolado/jarvis-operacional.py
SEM acionar comandos reais e SEM alterar o serviço de produção.
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
from gateway.audio_gateway import Me6sJournalMonitor, SessionArbitrator
from display.server import JarvisDisplayState, JarvisLogMonitor


class TestPatchedOperationalScript(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Carrega dinamicamente a cópia isolada patcheada
        cls.isolated_path = "/opt/jarvis-voice/test_isolado/jarvis-operacional.py"
        assert os.path.exists(cls.isolated_path), f"Arquivo não encontrado: {cls.isolated_path}"

        spec = importlib.util.spec_from_file_location("jarvis_isolado", cls.isolated_path)
        cls.mod = importlib.util.module_from_spec(spec)

        # Mock seguro para arecord no carregamento (evitar tentar abrir dispositivo ALSA ocupado)
        with patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.stdout.read.return_value = b""
            mock_popen.return_value = mock_proc
            spec.loader.exec_module(cls.mod)

    def setUp(self):
        # Garante variáveis de ambiente de simulação estrita
        os.environ["JARVIS_WOL_REAL"] = "0"
        os.environ["JARVIS_SSH_REAL"] = "0"
        self.mod.estado = "espera"
        self.mod.limite = 0.0
        self.mod.pendente = None
        self.mod.ultima_ativacao = 0.0
        self.mod.fonte_ativa = None
        self.mod.sessao_ativa_id = None
        with self.mod.lock_acoes:
            self.mod.acoes_recentes.clear()

    def test_01_log_protocol_backward_compatibility(self):
        """Verifica se as linhas impressas continuam 100% compatíveis com display/server.py e Me6sJournalMonitor."""
        output = io.StringIO()
        with patch("sys.stdout", output):
            self.mod.processar("assistente", fonte="lg_mic", session_id="sess-test-1")

        linhas = [l.strip() for l in output.getvalue().strip().split("\n") if l.strip()]
        # Linha 1 deve ser exatamente 'Ouvi: assistente'
        self.assertEqual(linhas[0], "Ouvi: assistente")
        # Linha de metadados separada
        self.assertEqual(linhas[1], "[Origem: lg_mic]")
        # Linha de ativação contratual
        self.assertEqual(linhas[2], "ATIVADO! Aguardando comando.")

        # Valida parsing com JarvisDisplayState e JarvisLogMonitor de display/server.py
        dstate = JarvisDisplayState()
        watcher = JarvisLogMonitor(display_state=dstate)
        for l in linhas:
            watcher.processar_linha(l)
        self.assertEqual(dstate.obter_estado()["status"], "listening")
        self.assertEqual(dstate.obter_estado()["mensagem"], "Estou ouvindo...")

        # Valida parsing com Me6sJournalMonitor de audio_gateway.py
        arbitrator = SessionArbitrator()
        monitor = Me6sJournalMonitor(arbitrator=arbitrator)
        for l in linhas:
            monitor._processar_linha(l)
        self.assertEqual(arbitrator.state, "comando")

    def test_02_idempotency_not_recorded_on_insufficient_confirmation(self):
        """Bloqueador 4: Verifica que 'confirmar' insuficiente em modo real NÃO registra ação no cache."""
        os.environ["JARVIS_SSH_REAL"] = "1"
        self.mod.estado = "confirmacao"
        self.mod.pendente = "fusion"
        self.mod.limite = time.monotonic() + 20.0

        output = io.StringIO()
        with patch.object(self.mod.painel, "host_ligado", return_value=True):
            with patch("sys.stdout", output):
                self.mod.processar("confirmar", fonte="me6s")

        # Deve rejeitar por confirmação insuficiente
        self.assertEqual(self.mod.estado, "espera")
        self.assertIn("Confirmacao insuficiente. Pedido cancelado.", output.getvalue())

        # O cache de idempotência NÃO deve conter 'desligar' para 'fusion'
        with self.mod.lock_acoes:
            self.assertNotIn(("desligar", "fusion"), self.mod.acoes_recentes)

    def test_03_idempotency_recorded_only_after_valid_confirmation(self):
        """Bloqueador 4: Confirmação estrita válida registra ação e descarta duplicações imediatas."""
        os.environ["JARVIS_SSH_REAL"] = "0"  # Modo simulação
        self.mod.estado = "confirmacao"
        self.mod.pendente = "fusion"
        self.mod.limite = time.monotonic() + 20.0

        output1 = io.StringIO()
        with patch.object(self.mod.painel, "host_ligado", return_value=True):
            with patch("sys.stdout", output1):
                self.mod.processar("confirmar desligamento", fonte="lg_mic")

        self.assertIn("SIMULACAO SSH para", output1.getvalue())
        with self.mod.lock_acoes:
            self.assertIn(("desligar", "fusion"), self.mod.acoes_recentes)

        # Nova tentativa de confirmação dentro de 3.5s deve ser descartada por idempotência
        self.mod.estado = "confirmacao"
        self.mod.pendente = "fusion"
        self.mod.limite = time.monotonic() + 20.0
        output2 = io.StringIO()
        with patch.object(self.mod.painel, "host_ligado", return_value=True):
            with patch("sys.stdout", output2):
                self.mod.processar("confirmar desligamento", fonte="me6s")

        self.assertIn("descartado por idempotencia", output2.getvalue())

    def test_04_strict_ipc_semantic_validation(self):
        """Bloqueador 5: Verifica rejeição de mensagens IPC malformadas ou com texto fora de contexto."""
        # 1. Payload não-dicionário ou versão incorreta
        self.mod.on_ipc_event("invalid string")
        self.mod.on_ipc_event({"versao": 2, "fonte": "lg_mic", "evento": "wake", "texto": "assistente"})
        # 2. Fonte inválida
        self.mod.on_ipc_event({"versao": 1, "fonte": "hacker_mic", "evento": "wake", "texto": "assistente"})
        # 3. Tipo inválido
        self.mod.on_ipc_event({"versao": 1, "fonte": "lg_mic", "evento": "executar_shell", "texto": "rm -rf"})
        # 4. Texto em desacordo com tipo (ex: wake com texto diferente de assistente)
        self.mod.on_ipc_event({"versao": 1, "fonte": "lg_mic", "evento": "wake", "texto": "desligar fusion"})
        # 5. Comando desconhecido na gramática
        self.mod.on_ipc_event({"versao": 1, "fonte": "lg_mic", "evento": "comando", "texto": "destruir servidor"})

        # Fila deve permanecer vazia pois todos foram rejeitados
        self.assertTrue(self.mod.fila_intencoes.empty())

        # Payload legítimo deve ser aceito
        self.mod.on_ipc_event({"versao": 1, "fonte": "lg_mic", "evento": "wake", "texto": "assistente", "session_id": "s1"})
        self.assertFalse(self.mod.fila_intencoes.empty())
        item = self.mod.fila_intencoes.get_nowait()
        self.assertEqual(item, ("lg_mic", "assistente", "s1"))

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
        """Teste com áudio real: submete '5.wav' ('assistente') ao reconhecedor Vosk operacional."""
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
        """Teste ponta a ponta: transmite frames de '5.wav' via TCP para LgMicServer e valida wake pelo árbitro."""
        import socket
        import struct
        from gateway.audio_gateway import LgMicServer, SessionArbitrator, MAGIC_DATA, MAGIC_HANDSHAKE

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
