#!/usr/bin/env python3
"""
Testes de Integração com UNIX Domain Socket Real em Ambiente Isolado.
Valida:
- Cenários de Timeout, ACK inválido, Conexão interrompida
- Fila cheia respondendo com falha, nunca sucesso
- Confirmação de wake estritamente associada ao processamento real
- Comandos chegando imediatamente após a ativação
- Despertar simultâneo dos dois microfones com autoridade única
- Proteção contra comandos duplicados em falhas e retransmissões
"""
import importlib.util
import io
import json
import os
import queue
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from gateway.ipc_bus import (
    DEFAULT_IPC_SOCKET,
    IpcEventBroadcaster,
    IpcEventListener,
)
from gateway.audio_gateway import SessionArbitrator


class TestIpcRealSocketIntegration(unittest.TestCase):
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

        target_path = Path(__file__).resolve().parent.parent.parent / "scripts" / "jarvis_operacional_corrigido.py"
        spec = importlib.util.spec_from_file_location("jarvis_op_test", str(target_path))
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.sock_path = os.path.join(self.temp_dir, "real_ipc_test.sock")
        self.state_file = os.path.join(self.temp_dir, "acoes_recentes.json")
        os.environ["JARVIS_STATE_FILE"] = self.state_file

        # Reseta o estado do controlador
        self.mod.estado = "espera"
        self.mod.pendente = None
        self.mod.sessao_ativa_id = None
        self.mod.fonte_ativa = None
        self.mod.ultima_ativacao = 0.0
        with self.mod.lock_acoes:
            self.mod.acoes_recentes.clear()
            self.mod.registro_acoes.clear()
        if hasattr(self.mod.painel, "simular_atraso_ssh_s"):
            delattr(self.mod.painel, "simular_atraso_ssh_s")
        if hasattr(self.mod.painel, "simular_codigo_ssh"):
            delattr(self.mod.painel, "simular_codigo_ssh")
        while not self.mod.fila_intencoes.empty():
            try:
                self.mod.fila_intencoes.get_nowait()
            except queue.Empty:
                break
        self.patch_host_ligado = patch.object(self.mod.painel, "host_ligado", return_value=False)
        self.patch_host_ligado.start()

    def tearDown(self):
        try:
            self.patch_host_ligado.stop()
        except Exception:
            pass
        if hasattr(self.mod.painel, "simular_atraso_ssh_s"):
            delattr(self.mod.painel, "simular_atraso_ssh_s")
        if hasattr(self.mod.painel, "simular_codigo_ssh"):
            delattr(self.mod.painel, "simular_codigo_ssh")
        if os.path.exists(self.state_file):
            try:
                os.remove(self.state_file)
            except OSError:
                pass
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_01_timeout_waiting_for_ack_returns_failure(self):
        """1. Broadcaster com timeout curto não pode retornar sucesso quando o ACK atrasar/expirar."""
        server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server_sock.bind(self.sock_path)
        server_sock.listen(1)

        def slow_listener():
            try:
                client, _ = server_sock.accept()
                # Lê evento e deliberadamente dorme mais do que o timeout do broadcaster
                client.recv(1024)
                time.sleep(0.8)
                client.sendall(b'{"sucesso": true}\n')
                client.close()
            except Exception:
                pass
            finally:
                server_sock.close()

        th = threading.Thread(target=slow_listener, daemon=True)
        th.start()

        broadcaster = IpcEventBroadcaster(socket_path=self.sock_path, timeout=0.3)
        res = broadcaster.emit_event(source="lg_mic", event_type="wake", text="assistente")

        self.assertIsNone(res)
        self.assertEqual(broadcaster.last_error, "timeout_ack")
        th.join(timeout=1.5)

    def test_02_invalid_ack_returns_failure(self):
        """2. Broadcaster não pode retornar sucesso quando o ACK recebido for inválido ou corrompido."""
        server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server_sock.bind(self.sock_path)
        server_sock.listen(1)

        def malformed_listener():
            try:
                client, _ = server_sock.accept()
                client.recv(1024)
                client.sendall(b'{corrupted_json_payload\n')
                client.close()
            except Exception:
                pass
            finally:
                server_sock.close()

        th = threading.Thread(target=malformed_listener, daemon=True)
        th.start()

        broadcaster = IpcEventBroadcaster(socket_path=self.sock_path, timeout=1.0)
        res = broadcaster.emit_event(source="lg_mic", event_type="wake", text="assistente")

        self.assertIsNone(res)
        self.assertEqual(broadcaster.last_error, "ack_invalido")
        th.join(timeout=1.5)

    def test_03_interrupted_connection_returns_failure(self):
        """3. Broadcaster não pode retornar sucesso quando a conexão for fechada prematuramente."""
        server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server_sock.bind(self.sock_path)
        server_sock.listen(1)

        def close_immediately_listener():
            try:
                client, _ = server_sock.accept()
                client.recv(1024)
                # Fecha a conexão sem enviar nenhum byte de resposta
                client.close()
            except Exception:
                pass
            finally:
                server_sock.close()

        th = threading.Thread(target=close_immediately_listener, daemon=True)
        th.start()

        broadcaster = IpcEventBroadcaster(socket_path=self.sock_path, timeout=1.0)
        res = broadcaster.emit_event(source="lg_mic", event_type="wake", text="assistente")

        self.assertIsNone(res)
        self.assertEqual(broadcaster.last_error, "ack_ausente")
        th.join(timeout=1.5)

    def test_04_queue_full_responds_with_failure_never_success(self):
        """4. Se a fila estiver cheia, o IPC deve responder com falha, NUNCA sucesso."""
        listener = IpcEventListener(
            socket_path=self.sock_path,
            on_event=self.mod.on_ipc_event,
            allowed_uid=os.getuid(),
            verify_peer_cred=True,
        )
        listener.start()
        try:
            # Enche artificialmente a fila de intenções até a capacidade máxima
            while not self.mod.fila_intencoes.full():
                self.mod.fila_intencoes.put_nowait(("dummy", "dummy", None))

            self.assertTrue(self.mod.fila_intencoes.full())

            broadcaster = IpcEventBroadcaster(socket_path=self.sock_path, timeout=1.0)
            res = broadcaster.emit_event(
                source="lg_mic",
                event_type="wake",
                text="assistente",
                session_id="sess-queue-full-1",
            )

            # Broadcaster não pode retornar sucesso
            self.assertIsNone(res)
            self.assertEqual(broadcaster.last_error, "fila_cheia")
            self.assertIsNotNone(broadcaster.last_ack)
            self.assertFalse(broadcaster.last_ack.get("sucesso"))
            self.assertFalse(broadcaster.last_ack.get("processado"))
            self.assertEqual(broadcaster.last_ack.get("erro"), "fila_cheia")
        finally:
            listener.stop()

    def test_05_wake_confirmed_only_after_actual_processing(self):
        """5. O controlador não pode confirmar um wake como processado enquanto ele estiver somente na fila."""
        listener = IpcEventListener(
            socket_path=self.sock_path,
            on_event=self.mod.on_ipc_event,
            allowed_uid=os.getuid(),
            verify_peer_cred=True,
        )
        listener.start()

        # Inicia loop de consumo em background para simular o worker do controlador
        worker_running = threading.Event()
        worker_running.set()
        self.mod.loop_principal_ativo.set()

        def worker_loop():
            while worker_running.is_set():
                try:
                    item = self.mod.fila_intencoes.get(timeout=0.05)
                    fonte = item[0]
                    texto = item[1]
                    sid = item[2]
                    ev_id = item[3] if len(item) > 3 else None
                    comp_q = item[4] if len(item) > 4 else None

                    # Executa processamento real
                    res = self.mod.processar(texto, fonte=fonte, session_id=sid, event_id=ev_id)
                    if comp_q is not None:
                        comp_q.put(res)
                except queue.Empty:
                    pass

        worker_th = threading.Thread(target=worker_loop, daemon=True)
        worker_th.start()

        try:
            broadcaster = IpcEventBroadcaster(socket_path=self.sock_path, timeout=3.0)
            ack = broadcaster.emit_event(
                source="lg_mic",
                event_type="wake",
                text="assistente",
                session_id="sess-processed-sync-1",
            )

            # Quando o ACK for entregue ao cliente, o wake JÁ DEVE ESTAR 100% PROCESSADO
            self.assertIsNotNone(ack)
            self.assertTrue(ack.get("sucesso"))
            self.assertTrue(ack.get("processado"))
            self.assertEqual(ack.get("estado"), "comando")
            self.assertEqual(ack.get("active_session_id"), "sess-processed-sync-1")

            # O estado interno do controlador já foi efetivamente alterado para 'comando'
            self.assertEqual(self.mod.estado, "comando")
            self.assertEqual(self.mod.sessao_ativa_id, "sess-processed-sync-1")
        finally:
            worker_running.clear()
            self.mod.loop_principal_ativo.clear()
            worker_th.join(timeout=1.0)
            listener.stop()

    def test_06_commands_arriving_immediately_after_activation(self):
        """6. Comandos chegando imediatamente após a ativação são aceitos sem corrida."""
        listener = IpcEventListener(
            socket_path=self.sock_path,
            on_event=self.mod.on_ipc_event,
            allowed_uid=os.getuid(),
            verify_peer_cred=True,
        )
        listener.start()

        worker_running = threading.Event()
        worker_running.set()
        self.mod.loop_principal_ativo.set()

        def worker_loop():
            while worker_running.is_set():
                try:
                    item = self.mod.fila_intencoes.get(timeout=0.05)
                    fonte, texto, sid, ev_id, comp_q = item
                    res = self.mod.processar(texto, fonte=fonte, session_id=sid, event_id=ev_id)
                    if comp_q is not None:
                        comp_q.put(res)
                except queue.Empty:
                    pass

        worker_th = threading.Thread(target=worker_loop, daemon=True)
        worker_th.start()

        try:
            broadcaster = IpcEventBroadcaster(socket_path=self.sock_path, timeout=3.0)
            # 1. Envia Wake
            wake_ack = broadcaster.emit_event(
                source="lg_mic",
                event_type="wake",
                text="assistente",
                session_id="sess-immediate-cmd-1",
            )
            self.assertIsNotNone(wake_ack)
            self.assertTrue(wake_ack.get("sucesso"))
            active_sid = wake_ack.get("active_session_id")

            # 2. Envia Comando IMEDIATAMENTE (0ms de atraso)
            cmd_ack = broadcaster.emit_event(
                source="lg_mic",
                event_type="comando",
                text="ligar fusion",
                session_id=active_sid,
            )

            self.assertIsNotNone(cmd_ack)
            self.assertTrue(cmd_ack.get("sucesso"))
            self.assertTrue(cmd_ack.get("processado"))
            self.assertEqual(cmd_ack.get("acao"), "ligar")
            self.assertEqual(self.mod.estado, "espera")
        finally:
            worker_running.clear()
            self.mod.loop_principal_ativo.clear()
            worker_th.join(timeout=1.0)
            listener.stop()

    def test_07_simultaneous_wake_me6s_and_lg_on_real_socket(self):
        """7. Dois microfones ativando simultaneamente sobre socket real reconciliam autoridade única."""
        listener = IpcEventListener(
            socket_path=self.sock_path,
            on_event=self.mod.on_ipc_event,
            allowed_uid=os.getuid(),
            verify_peer_cred=True,
        )
        listener.start()

        worker_running = threading.Event()
        worker_running.set()
        self.mod.loop_principal_ativo.set()

        def worker_loop():
            while worker_running.is_set():
                try:
                    item = self.mod.fila_intencoes.get(timeout=0.05)
                    fonte, texto, sid, ev_id, comp_q = item
                    res = self.mod.processar(texto, fonte=fonte, session_id=sid, event_id=ev_id)
                    if comp_q is not None:
                        comp_q.put(res)
                except queue.Empty:
                    pass

        worker_th = threading.Thread(target=worker_loop, daemon=True)
        worker_th.start()

        try:
            # ME6S ativa localmente primeiro
            self.mod.processar("assistente", fonte="me6s")
            me6s_sid = self.mod.sessao_ativa_id

            # LG ativa quase simultaneamente via UNIX domain socket
            broadcaster = IpcEventBroadcaster(socket_path=self.sock_path, timeout=3.0)
            lg_ack = broadcaster.emit_event(
                source="lg_mic",
                event_type="wake",
                text="assistente",
                session_id="sess-lg-candidate-simul",
            )

            # Controlador responde com o active_session_id do ME6S e deduplicado=True
            self.assertIsNotNone(lg_ack)
            self.assertTrue(lg_ack.get("sucesso"))
            self.assertEqual(lg_ack.get("active_session_id"), me6s_sid)
            self.assertTrue(lg_ack.get("deduplicado"))
            self.assertEqual(self.mod.sessao_ativa_id, me6s_sid)
        finally:
            worker_running.clear()
            self.mod.loop_principal_ativo.clear()
            worker_th.join(timeout=1.0)
            listener.stop()

    def test_08_retransmissions_never_duplicate_commands(self):
        """8. Falhas e retransmissões não provocam comandos duplicados."""
        listener = IpcEventListener(
            socket_path=self.sock_path,
            on_event=self.mod.on_ipc_event,
            allowed_uid=os.getuid(),
            verify_peer_cred=True,
        )
        listener.start()

        worker_running = threading.Event()
        worker_running.set()
        self.mod.loop_principal_ativo.set()

        comandos_executados = []

        def worker_loop():
            while worker_running.is_set():
                try:
                    item = self.mod.fila_intencoes.get(timeout=0.05)
                    fonte, texto, sid, ev_id, comp_q = item
                    if texto == "ligar fusion":
                        comandos_executados.append(ev_id)
                    res = self.mod.processar(texto, fonte=fonte, session_id=sid, event_id=ev_id)
                    if comp_q is not None:
                        comp_q.put(res)
                except queue.Empty:
                    pass

        worker_th = threading.Thread(target=worker_loop, daemon=True)
        worker_th.start()

        try:
            broadcaster = IpcEventBroadcaster(socket_path=self.sock_path, timeout=3.0)

            # Ativação prévia
            wake_ack = broadcaster.emit_event(
                source="lg_mic",
                event_type="wake",
                text="assistente",
                session_id="sess-retransmission-test",
            )
            self.assertIsNotNone(wake_ack)
            sid = wake_ack.get("active_session_id")

            # 1. Envio do comando com event_id fixo
            fixed_event_id = "ev-fixed-test-999"
            cmd_ack1 = broadcaster.emit_event(
                source="lg_mic",
                event_type="comando",
                text="ligar fusion",
                session_id=sid,
                event_id=fixed_event_id,
            )
            self.assertIsNotNone(cmd_ack1)
            self.assertTrue(cmd_ack1.get("sucesso"))
            self.assertEqual(len(comandos_executados), 1)

            # 2. Retransmissão simulada com o MESMO event_id
            cmd_ack2 = broadcaster.emit_event(
                source="lg_mic",
                event_type="comando",
                text="ligar fusion",
                session_id=sid,
                event_id=fixed_event_id,
            )
            self.assertIsNotNone(cmd_ack2)
            self.assertTrue(cmd_ack2.get("sucesso"))
            self.assertTrue(cmd_ack2.get("retransmissao", False))

            # Garante que o comando foi executado estritamente UMA ÚNICA VEZ
            self.assertEqual(len(comandos_executados), 1)
        finally:
            worker_running.clear()
            self.mod.loop_principal_ativo.clear()
            worker_th.join(timeout=1.0)
            listener.stop()

    def test_09_slow_ssh_execution_does_not_timeout_or_block_ipc(self):
        """9. Execução demorada de SSH (3-10s) não provoca timeout do IPC nem bloqueia novas mensagens."""
        listener = IpcEventListener(
            socket_path=self.sock_path,
            on_event=self.mod.on_ipc_event,
            allowed_uid=os.getuid(),
            verify_peer_cred=True,
        )
        listener.start()

        worker_running = threading.Event()
        worker_running.set()
        self.mod.loop_principal_ativo.set()

        def worker_loop():
            while worker_running.is_set():
                try:
                    item = self.mod.fila_intencoes.get(timeout=0.05)
                    fonte, texto, sid, ev_id, comp_q = item
                    res = self.mod.processar(texto, fonte=fonte, session_id=sid, event_id=ev_id)
                    if comp_q is not None:
                        comp_q.put(res)
                except queue.Empty:
                    pass

        worker_th = threading.Thread(target=worker_loop, daemon=True)
        worker_th.start()

        # Simula SSH demorando 4.0 segundos
        self.mod.painel.simular_atraso_ssh_s = 4.0

        try:
            broadcaster = IpcEventBroadcaster(socket_path=self.sock_path, timeout=2.0)

            # 1. Ativação via LG
            wake_ack = broadcaster.emit_event(
                source="lg_mic",
                event_type="wake",
                text="assistente",
                session_id="sess-slow-ssh-1",
            )
            self.assertIsNotNone(wake_ack)
            self.assertTrue(wake_ack.get("sucesso"))

            # 2. Comando para desligar fusion (com host ligado simulado)
            with patch.object(self.mod.painel, "host_ligado", return_value=True):
                cmd_ack = broadcaster.emit_event(
                    source="lg_mic",
                    event_type="comando",
                    text="desligar fusion",
                    session_id="sess-slow-ssh-1",
                )
            self.assertIsNotNone(cmd_ack)
            self.assertTrue(cmd_ack.get("sucesso"))
            self.assertEqual(cmd_ack.get("estado"), "confirmacao")

            # 3. Confirmação do desligamento: SSH demora 4.0s, mas IPC tem timeout de 2.0s
            t_inicio = time.monotonic()
            with patch.object(self.mod.painel, "host_ligado", return_value=True):
                conf_ack = broadcaster.emit_event(
                    source="lg_mic",
                    event_type="confirmar",
                    text="confirmar desligamento",
                    session_id="sess-slow-ssh-1",
                    event_id="ev-slow-ssh-conf",
                )
            t_duracao = time.monotonic() - t_inicio

            # O ACK deve ser retornado em menos de 1.0s (sem estourar timeout de 2.0s)
            self.assertLess(t_duracao, 1.0)
            self.assertIsNotNone(conf_ack)
            self.assertTrue(conf_ack.get("sucesso"))
            self.assertEqual(conf_ack.get("event_id"), "ev-slow-ssh-conf")
            self.assertEqual(conf_ack.get("estado"), "espera")
            self.assertTrue(conf_ack.get("processado"))
            self.assertIn(conf_ack.get("status"), ("em_processamento", "concluido"))

            # 4. Verifica que a ação está registrada no controlador
            status_reg = self.mod.obter_status_acao("ev-slow-ssh-conf")
            self.assertIsNotNone(status_reg)
            self.assertIn(status_reg.get("status"), ("em_processamento", "concluido"))

            # 5. Enquanto o SSH de 4s roda em background, novos eventos IPC são aceitos sem bloqueio
            wake_ack2 = broadcaster.emit_event(
                source="lg_mic",
                event_type="wake",
                text="assistente",
                session_id="sess-slow-ssh-2",
            )
            self.assertIsNotNone(wake_ack2)
            self.assertTrue(wake_ack2.get("sucesso"))
            self.assertEqual(wake_ack2.get("estado"), "comando")

            # Aguarda a conclusão do worker SSH (4.0s)
            time.sleep(4.2)
            status_final = self.mod.obter_status_acao("ev-slow-ssh-conf")
            self.assertEqual(status_final.get("status"), "concluido")
        finally:
            self.mod.painel.simular_atraso_ssh_s = 0.0
            worker_running.clear()
            self.mod.loop_principal_ativo.clear()
            worker_th.join(timeout=1.0)
            listener.stop()

    def test_10_client_disconnect_during_processing_safe(self):
        """10. Desconexão do cliente durante processamento de ação demorada não trava o controlador."""
        listener = IpcEventListener(
            socket_path=self.sock_path,
            on_event=self.mod.on_ipc_event,
            allowed_uid=os.getuid(),
            verify_peer_cred=True,
        )
        listener.start()

        worker_running = threading.Event()
        worker_running.set()
        self.mod.loop_principal_ativo.set()

        def worker_loop():
            while worker_running.is_set():
                try:
                    item = self.mod.fila_intencoes.get(timeout=0.05)
                    fonte, texto, sid, ev_id, comp_q = item
                    res = self.mod.processar(texto, fonte=fonte, session_id=sid, event_id=ev_id)
                    if comp_q is not None:
                        comp_q.put(res)
                except queue.Empty:
                    pass

        worker_th = threading.Thread(target=worker_loop, daemon=True)
        worker_th.start()

        self.mod.painel.simular_atraso_ssh_s = 1.0

        try:
            # Prepara o estado em 'confirmacao'
            self.mod.estado = "confirmacao"
            self.mod.pendente = "fusion"
            self.mod.sessao_ativa_id = "sess-disc-1"
            self.mod.limite = time.monotonic() + 12.0

            # Conecta cliente raw e envia evento, fechando o socket imediatamente
            raw_client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            raw_client.connect(self.sock_path)
            ev_id = "ev-disconnect-early-1"
            payload = {
                "versao": 1,
                "event_id": ev_id,
                "fonte": "lg_mic",
                "evento": "confirmar",
                "texto": "confirmar desligamento",
                "session_id": "sess-disc-1",
                "timestamp": round(time.time(), 4),
                "nonce": f"nonce_{ev_id}",
            }
            with patch.object(self.mod.painel, "host_ligado", return_value=True):
                raw_client.sendall((json.dumps(payload) + "\n").encode("utf-8"))
                # Fecha imediatamente antes de ler a resposta (provoca BrokenPipe/desconexão no listener)
                raw_client.close()

                # Aguarda a ação em background concluir
                time.sleep(1.3)
                status_reg = self.mod.obter_status_acao(ev_id)
                self.assertIsNotNone(status_reg)
                self.assertEqual(status_reg.get("status"), "concluido")

                # Garante que nenhum lock permaneceu preso e o controlador continua operando
                self.assertFalse(self.mod.lock_acoes.locked())
                self.assertEqual(self.mod.estado, "espera")
        finally:
            self.mod.painel.simular_atraso_ssh_s = 0.0
            worker_running.clear()
            self.mod.loop_principal_ativo.clear()
            worker_th.join(timeout=1.0)
            listener.stop()

    def test_11_incomplete_acks_strictly_rejected(self):
        """11. Broadcaster exige correspondência de event_id, sucesso, estado e confirmação real."""
        server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server_sock.bind(self.sock_path)
        server_sock.listen(5)

        cenarios = [
            (b'{}\n', "event_id_ausente"),
            (b'{"sucesso": true, "estado": "espera", "processado": true}\n', "event_id_ausente"),
            (b'{"event_id": "divergente-id", "sucesso": true, "estado": "espera", "processado": true}\n', "event_id_divergente"),
            (b'{"event_id": "TARGET_ID", "estado": "espera", "processado": true}\n', "sucesso_ausente"),
            (b'{"event_id": "TARGET_ID", "sucesso": false, "erro": "recusado_pelo_servidor", "estado": "espera", "processado": true}\n', "recusado_pelo_servidor"),
            (b'{"event_id": "TARGET_ID", "sucesso": true, "processado": true}\n', "estado_invalido"),
            (b'{"event_id": "TARGET_ID", "sucesso": true, "estado": "desconhecido", "processado": true}\n', "estado_invalido"),
            (b'{"event_id": "TARGET_ID", "sucesso": true, "estado": "espera"}\n', "confirmacao_processamento_ausente"),
        ]

        broadcaster = IpcEventBroadcaster(socket_path=self.sock_path, timeout=1.0)

        import secrets
        for idx, (resposta_bytes, erro_esperado) in enumerate(cenarios):
            ev_id = f"ev-strict-{secrets.token_hex(4)}-{idx}"
            if b"TARGET_ID" in resposta_bytes:
                real_resp = resposta_bytes.replace(b"TARGET_ID", ev_id.encode("utf-8"))
            else:
                real_resp = resposta_bytes

            def _responder(r=real_resp):
                try:
                    c, _ = server_sock.accept()
                    c.recv(1024)
                    c.sendall(r)
                    c.close()
                except Exception:
                    pass

            th = threading.Thread(target=_responder, daemon=True)
            th.start()

            res = broadcaster.emit_event(source="lg_mic", event_type="wake", text="assistente", event_id=ev_id)
            th.join(timeout=0.5)

            self.assertIsNone(res, f"Esperava rejeição para: {real_resp.decode('utf-8')}")
            self.assertEqual(broadcaster.last_error, erro_esperado)

        server_sock.close()

    def test_12_retransmission_after_timeout_never_duplicates_action(self):
        """12. Retransmissão após timeout nunca executa uma segunda vez o comando."""
        listener = IpcEventListener(
            socket_path=self.sock_path,
            on_event=self.mod.on_ipc_event,
            allowed_uid=os.getuid(),
            verify_peer_cred=True,
        )
        listener.start()

        worker_running = threading.Event()
        worker_running.set()
        self.mod.loop_principal_ativo.set()

        ssh_execucoes = []

        def worker_loop():
            while worker_running.is_set():
                try:
                    item = self.mod.fila_intencoes.get(timeout=0.05)
                    fonte, texto, sid, ev_id, comp_q = item
                    if texto == "confirmar desligamento":
                        ssh_execucoes.append((fonte, texto, sid, ev_id))
                    res = self.mod.processar(texto, fonte=fonte, session_id=sid, event_id=ev_id)
                    if comp_q is not None:
                        comp_q.put(res)
                except queue.Empty:
                    pass

        worker_th = threading.Thread(target=worker_loop, daemon=True)
        worker_th.start()

        self.mod.estado = "confirmacao"
        self.mod.pendente = "fusion"
        self.mod.sessao_ativa_id = "sess-retrans-timeout-1"
        self.mod.limite = time.monotonic() + 12.0

        ev_id_fixo = "ev-confirm-retrans-test-12"

        try:
            broadcaster = IpcEventBroadcaster(socket_path=self.sock_path, timeout=2.0)
            with patch.object(self.mod.painel, "host_ligado", return_value=True):
                # 1. Primeira emissão
                ack1 = broadcaster.emit_event(
                    source="lg_mic",
                    event_type="confirmar",
                    text="confirmar desligamento",
                    session_id="sess-retrans-timeout-1",
                    event_id=ev_id_fixo,
                )
                self.assertIsNotNone(ack1)
                self.assertTrue(ack1.get("sucesso"))
                self.assertEqual(len(ssh_execucoes), 1)

                # 2. Retransmissão com MESMO event_id (como ocorreria após timeout na rede)
                ack2 = broadcaster.emit_event(
                    source="lg_mic",
                    event_type="confirmar",
                    text="confirmar desligamento",
                    session_id="sess-retrans-timeout-1",
                    event_id=ev_id_fixo,
                )
                self.assertIsNotNone(ack2)
                self.assertTrue(ack2.get("sucesso"))
                self.assertTrue(ack2.get("retransmissao"))

                # 3. Reinicialização do Broadcaster (simula cliente reiniciando)
                broadcaster_novo = IpcEventBroadcaster(socket_path=self.sock_path, timeout=2.0)
                ack3 = broadcaster_novo.emit_event(
                    source="lg_mic",
                    event_type="confirmar",
                    text="confirmar desligamento",
                    session_id="sess-retrans-timeout-1",
                    event_id=ev_id_fixo,
                )
                self.assertIsNotNone(ack3)
                self.assertTrue(ack3.get("sucesso"))

                # Estritamente 1 única execução de SSH foi disparada
                self.assertEqual(len(ssh_execucoes), 1)
        finally:
            worker_running.clear()
            self.mod.loop_principal_ativo.clear()
            worker_th.join(timeout=1.0)
            listener.stop()

    def test_13_action_failure_never_causes_duplicate_execution(self):
        """13. Falha na execução da ação registra status 'falha' e impede execuções duplicadas."""
        self.mod.estado = "confirmacao"
        self.mod.pendente = "fusion"
        self.mod.sessao_ativa_id = "sess-fail-test-1"
        self.mod.limite = time.monotonic() + 12.0

        ev_id = "ev-action-fail-test-13"

        def _ssh_falho(cfg):
            raise RuntimeError("Falha simulada de rede SSH")

        with patch.object(self.mod.painel, "host_ligado", return_value=True), \
             patch.object(self.mod.painel, "montar_comando_ssh", side_effect=_ssh_falho):
            res1 = self.mod.processar("confirmar desligamento", fonte="lg_mic", session_id="sess-fail-test-1", event_id=ev_id)
            time.sleep(0.05)

            # O status da ação deve ser registrado como 'falha'
            status_reg = self.mod.obter_status_acao(ev_id)
            self.assertIsNotNone(status_reg)
            self.assertEqual(status_reg.get("status"), "falha")
            self.assertIn("Falha simulada de rede SSH", status_reg.get("erro", ""))

            # Tentativa de executar novamente imediatamente é bloqueada por idempotência
            self.mod.estado = "confirmacao"
            self.mod.pendente = "fusion"
            out_retry = io.StringIO()
            with patch("sys.stdout", out_retry):
                res2 = self.mod.processar("confirmar desligamento", fonte="me6s", session_id="sess-fail-test-2")
            self.assertIn("Desligamento de fusion descartado por idempotencia.", out_retry.getvalue())
            self.assertTrue(res2.get("deduplicado"))

    def test_14_lg_and_me6s_operate_simultaneously_during_slow_action(self):
        """14. LG e ME6S continuam operando e despertando simultaneamente durante ação SSH em background."""
        listener = IpcEventListener(
            socket_path=self.sock_path,
            on_event=self.mod.on_ipc_event,
            allowed_uid=os.getuid(),
            verify_peer_cred=True,
        )
        listener.start()

        worker_running = threading.Event()
        worker_running.set()
        self.mod.loop_principal_ativo.set()

        def worker_loop():
            while worker_running.is_set():
                try:
                    item = self.mod.fila_intencoes.get(timeout=0.05)
                    fonte, texto, sid, ev_id, comp_q = item
                    res = self.mod.processar(texto, fonte=fonte, session_id=sid, event_id=ev_id)
                    if comp_q is not None:
                        comp_q.put(res)
                except queue.Empty:
                    pass

        worker_th = threading.Thread(target=worker_loop, daemon=True)
        worker_th.start()

        # Simula SSH demorando 3.5 segundos em background
        self.mod.painel.simular_atraso_ssh_s = 3.5

        try:
            broadcaster = IpcEventBroadcaster(socket_path=self.sock_path, timeout=2.0)

            # 1. Prepara e confirma desligamento de fusion
            self.mod.estado = "confirmacao"
            self.mod.pendente = "fusion"
            self.mod.sessao_ativa_id = "sess-bg-active"
            self.mod.limite = time.monotonic() + 12.0

            with patch.object(self.mod.painel, "host_ligado", return_value=True):
                conf_ack = broadcaster.emit_event(
                    source="lg_mic",
                    event_type="confirmar",
                    text="confirmar desligamento",
                    session_id="sess-bg-active",
                    event_id="ev-bg-ssh-action",
                )
            self.assertIsNotNone(conf_ack)
            self.assertTrue(conf_ack.get("sucesso"))
            self.assertEqual(conf_ack.get("estado"), "espera")

            # 2. Enquanto o SSH está rodando em background (3.5s), o ME6S ativa fisicamente
            out_me6s = io.StringIO()
            with patch("sys.stdout", out_me6s):
                res_me6s = self.mod.processar("assistente", fonte="me6s")
            self.assertTrue(res_me6s.get("sucesso"))
            self.assertEqual(self.mod.estado, "comando")
            self.assertIn("ATIVADO! Aguardando comando.", out_me6s.getvalue())

            # 3. LG envia comando de ligar ananuke enquanto o SSH de fusion continua rodando
            cmd_ack = broadcaster.emit_event(
                source="lg_mic",
                event_type="comando",
                text="ligar computador dois",
                session_id=res_me6s.get("active_session_id"),
            )
            self.assertIsNotNone(cmd_ack)
            self.assertTrue(cmd_ack.get("sucesso"))
            self.assertEqual(cmd_ack.get("acao"), "ligar")
            self.assertEqual(cmd_ack.get("alvo"), "ananuke")

            # 4. Aguarda a conclusão final da thread de desligamento
            time.sleep(3.6)
            status_reg = self.mod.obter_status_acao("ev-bg-ssh-action")
            self.assertEqual(status_reg.get("status"), "concluido")
        finally:
            self.mod.painel.simular_atraso_ssh_s = 0.0
            worker_running.clear()
            self.mod.loop_principal_ativo.clear()
            worker_th.join(timeout=1.0)
            listener.stop()

    def test_15_action_state_persists_across_controller_restarts(self):
        """15. Persistência de estado segura protege contra ações duplicadas mesmo após reinício do controlador."""
        ev_id = "ev-persist-restart-15"
        self.mod.estado = "confirmacao"
        self.mod.pendente = "fusion"
        self.mod.sessao_ativa_id = "sess-persist-1"

        with patch.object(self.mod.painel, "host_ligado", return_value=True):
            res = self.mod.processar("confirmar desligamento", fonte="lg_mic", session_id="sess-persist-1", event_id=ev_id)
            self.assertTrue(res.get("sucesso"))

        # Confirma que o arquivo de estado foi gravado no disco
        self.assertTrue(os.path.exists(self.state_file))

        # Simula o reinício total do processo do controlador (limpeza de memória RAM)
        with self.mod.lock_acoes:
            self.mod.acoes_recentes.clear()
            self.mod.registro_acoes.clear()

        # O novo processo inicia e recarrega o estado persistido do arquivo
        self.mod._carregar_estado_acoes()

        # 1. Verifica que a ação anterior foi recuperada
        status_recuperado = self.mod.obter_status_acao(ev_id)
        self.assertIsNotNone(status_recuperado)
        self.assertEqual(status_recuperado.get("status"), "concluido")

        # 2. Verifica que a mesma ação dentro da janela de idempotência permanece bloqueada após reinício
        self.assertTrue(self.mod.ja_executado_recentemente("desligar", "fusion", janela=3.5))

        # 3. Uma nova tentativa de desligar fusion logo após o reinício é descartada por idempotência
        self.mod.estado = "confirmacao"
        self.mod.pendente = "fusion"
        self.mod.sessao_ativa_id = "sess-persist-2"
        with patch.object(self.mod.painel, "host_ligado", return_value=True):
            out = io.StringIO()
            with patch("sys.stdout", out):
                res_retry = self.mod.processar("confirmar desligamento", fonte="me6s", session_id="sess-persist-2")
            self.assertTrue(res_retry.get("deduplicado"))
            self.assertIn("Desligamento de fusion descartado por idempotencia.", out.getvalue())

    def test_16_ssh_nonzero_returncode_registers_failure(self):
        """16. Código de retorno SSH != 0 registra status 'falha' e erro correspondente."""
        ev_id = "ev-ssh-exit-error-16"
        self.mod.estado = "confirmacao"
        self.mod.pendente = "fusion"
        self.mod.sessao_ativa_id = "sess-ssh-fail-1"

        # Simula código de retorno 255 (falha de conexão / host down)
        self.mod.painel.simular_codigo_ssh = 255
        try:
            with patch.object(self.mod.painel, "host_ligado", return_value=True):
                res = self.mod.processar("confirmar desligamento", fonte="lg_mic", session_id="sess-ssh-fail-1", event_id=ev_id)
                time.sleep(0.06)

            status_reg = self.mod.obter_status_acao(ev_id)
            self.assertIsNotNone(status_reg)
            self.assertEqual(status_reg.get("status"), "falha")
            self.assertEqual(status_reg.get("erro"), "ssh_returncode_255")
        finally:
            if hasattr(self.mod.painel, "simular_codigo_ssh"):
                delattr(self.mod.painel, "simular_codigo_ssh")

    def test_17_ssh_timeout_registers_failure(self):
        """17. Timeout de 25s no SSH registra status 'falha' com erro 'ssh_timeout_25s'."""
        ev_id = "ev-ssh-timeout-17"
        self.mod.estado = "confirmacao"
        self.mod.pendente = "fusion"
        self.mod.sessao_ativa_id = "sess-ssh-timeout-1"

        def _timeout_ssh(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd=["ssh"], timeout=25)

        with patch.dict(os.environ, {"JARVIS_SSH_REAL": "1"}), \
             patch.object(self.mod.painel, "host_ligado", return_value=True), \
             patch.object(self.mod.subprocess, "run", side_effect=_timeout_ssh):
            res = self.mod.processar("confirmar desligamento", fonte="lg_mic", session_id="sess-ssh-timeout-1", event_id=ev_id)
            time.sleep(0.06)

        status_reg = self.mod.obter_status_acao(ev_id)
        self.assertIsNotNone(status_reg)
        self.assertEqual(status_reg.get("status"), "falha")
        self.assertEqual(status_reg.get("erro"), "ssh_timeout_25s")

    def test_18_persistence_failure_blocks_real_wol_and_ssh(self):
        """18. Falha na persistência bloqueia estritamente ações reais de WoL e SSH (Requisitos 1 e 2)."""
        invalid_state_file = "/sys/kernel/security/jarvis_invalid_state/acoes.json"

        with patch.dict(os.environ, {"JARVIS_STATE_FILE": invalid_state_file, "JARVIS_WOL_REAL": "1", "JARVIS_SSH_REAL": "1"}):
            self.mod.estado = "comando"
            self.mod.sessao_ativa_id = "sess-persist-fail-1"

            # 1. Teste WoL com falha na persistência
            with patch.object(self.mod.painel, "host_ligado", return_value=False), \
                 patch.object(self.mod.subprocess, "run") as mock_subproc:
                res_wol = self.mod.processar("ligar fusion", fonte="lg_mic", session_id="sess-persist-fail-1", event_id="ev-wol-fail")
                self.assertFalse(res_wol.get("sucesso"))
                self.assertEqual(res_wol.get("erro"), "falha_persistencia_estado")
                mock_subproc.assert_not_called()

            # 2. Teste SSH com falha na persistência
            self.mod.estado = "confirmacao"
            self.mod.pendente = "fusion"
            self.mod.sessao_ativa_id = "sess-persist-fail-2"
            with patch.object(self.mod.painel, "host_ligado", return_value=True), \
                 patch.object(self.mod.subprocess, "run") as mock_subproc_ssh:
                res_ssh = self.mod.processar("confirmar desligamento", fonte="lg_mic", session_id="sess-persist-fail-2", event_id="ev-ssh-fail")
                self.assertFalse(res_ssh.get("sucesso"))
                self.assertEqual(res_ssh.get("erro"), "falha_persistencia_estado")
                mock_subproc_ssh.assert_not_called()

            # 3. Garante que nenhum fallback silencioso para /tmp ocorreu
            fallback_path = os.path.join(tempfile.gettempdir(), "jarvis_run", "acoes_recentes.json")
            self.assertFalse(os.path.exists(fallback_path))

    def test_19_real_isolated_process_kill_during_ssh_recovers_as_indetermined(self):
        """19. Reinicialização real de processo isolado durante SSH em andamento recupera como INDETERMINADO e preserva microfones (Requisitos 3, 4 e 5)."""
        temp_dir = tempfile.mkdtemp()
        state_file = os.path.join(temp_dir, "acoes_recentes.json")
        repo_root = str(Path(__file__).resolve().parent.parent.parent)

        code_proc1 = f"""
import sys, os, time, json
from pathlib import Path
try:
    import vosk
except ImportError:
    import types
    from unittest.mock import MagicMock
    mv = types.ModuleType('vosk')
    mv.Model = MagicMock()
    mv.KaldiRecognizer = MagicMock()
    mv.SetLogLevel = MagicMock()
    sys.modules['vosk'] = mv

try:
    import app
except ImportError:
    import types
    ma = types.ModuleType('app')
    ma.SERVIDORES = {{'fusion': {{'nome': 'Fusion (Windows)', 'mac': 'bc:24:11:83:35:82'}}}}
    ma.host_ligado = lambda srv: True
    ma.montar_comando_ssh = lambda cfg: ['echo', 'sim']
    sys.modules['app'] = ma

sys.path.insert(0, r'{repo_root}')
sys.path.insert(0, os.path.join(r'{repo_root}', 'scripts'))
os.environ['JARVIS_STATE_FILE'] = r'{state_file}'
os.environ['JARVIS_SSH_REAL'] = '0'
os.environ['JARVIS_WOL_REAL'] = '0'

import jarvis_operacional_corrigido as mod
mod.painel.simular_atraso_ssh_s = 5.0
mod.painel.host_ligado = lambda srv: True

mod.processar('assistente', fonte='lg_mic', session_id='sess-kill-test')
mod.processar('desligar fusion', fonte='lg_mic', session_id='sess-kill-test')
res = mod.processar('confirmar desligamento', fonte='lg_mic', session_id='sess-kill-test', event_id='ev-kill-test-1')
print('PROC1_DISPATCHED:' + json.dumps(res), flush=True)

time.sleep(10.0)
"""
        try:
            proc1 = subprocess.Popen(
                [sys.executable, "-c", code_proc1],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            linha = ""
            for _ in range(30):
                l = proc1.stdout.readline()
                if not l:
                    break
                if "PROC1_DISPATCHED:" in l:
                    linha = l
                    break
            self.assertIn("PROC1_DISPATCHED:", linha)

            with open(state_file, "r", encoding="utf-8") as f:
                dados1 = json.load(f)
            reg1 = dados1.get("registro_acoes", {}).get("ev-kill-test-1")
            self.assertIsNotNone(reg1)
            self.assertEqual(reg1.get("status"), "em_processamento")

            proc1.kill()
            proc1.wait()

            code_proc2 = f"""
import sys, os, time, json
from pathlib import Path
try:
    import vosk
except ImportError:
    import types
    from unittest.mock import MagicMock
    mv = types.ModuleType('vosk')
    mv.Model = MagicMock()
    mv.KaldiRecognizer = MagicMock()
    mv.SetLogLevel = MagicMock()
    sys.modules['vosk'] = mv

try:
    import app
except ImportError:
    import types
    ma = types.ModuleType('app')
    ma.SERVIDORES = {{
        'fusion': {{'nome': 'Fusion (Windows)', 'mac': 'bc:24:11:83:35:82'}},
        'ananuke': {{'nome': 'Ananuke (Debian)', 'mac': '0a:e0:af:c3:06:fc'}},
    }}
    ma.host_ligado = lambda srv: True
    ma.montar_comando_ssh = lambda cfg: ['echo', 'sim']
    sys.modules['app'] = ma

sys.path.insert(0, r'{repo_root}')
sys.path.insert(0, os.path.join(r'{repo_root}', 'scripts'))
os.environ['JARVIS_STATE_FILE'] = r'{state_file}'
os.environ['JARVIS_SSH_REAL'] = '0'
os.environ['JARVIS_WOL_REAL'] = '0'

import jarvis_operacional_corrigido as mod

st = mod.obter_status_acao(event_id='ev-kill-test-1')
print('INTERRUPTED_STATUS:' + json.dumps(st), flush=True)

blocked = mod.ja_executado_recentemente('desligar', 'fusion', janela=3.5)
print('IDEMPOTENCY_BLOCKED:' + str(blocked), flush=True)

retrans = mod.processar('confirmar desligamento', fonte='lg_mic', session_id='sess-kill-test', event_id='ev-kill-test-1')
print('RETRANS_RESULT:' + json.dumps(retrans), flush=True)

wake_res = mod.processar('assistente', fonte='me6s')
print('ME6S_WAKE:' + json.dumps(wake_res), flush=True)

cmd_res = mod.processar('ligar computador dois', fonte='lg_mic')
print('LG_CMD:' + json.dumps(cmd_res), flush=True)
"""
            proc2 = subprocess.Popen(
                [sys.executable, "-c", code_proc2],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            out2, err2 = proc2.communicate(timeout=10)
            self.assertEqual(proc2.returncode, 0, f"Processo 2 falhou: {err2}")

            linhas_out = {l.split(":", 1)[0].strip(): l.split(":", 1)[1].strip() for l in out2.strip().split("\n") if ":" in l}

            st_interrupted = json.loads(linhas_out["INTERRUPTED_STATUS"])
            self.assertEqual(st_interrupted.get("status"), "indeterminado")
            self.assertEqual(st_interrupted.get("erro"), "operacao_interrompida_por_reinicio")

            self.assertEqual(linhas_out["IDEMPOTENCY_BLOCKED"], "True")

            st_retrans = json.loads(linhas_out["RETRANS_RESULT"])
            self.assertFalse(st_retrans.get("sucesso"))
            self.assertEqual(st_retrans.get("status"), "indeterminado")
            self.assertTrue(st_retrans.get("retransmissao"))

            st_wake = json.loads(linhas_out["ME6S_WAKE"])
            self.assertTrue(st_wake.get("sucesso"))
            self.assertEqual(st_wake.get("estado"), "comando")

            st_cmd = json.loads(linhas_out["LG_CMD"])
            self.assertTrue(st_cmd.get("sucesso"))
            self.assertEqual(st_cmd.get("acao"), "ligar")
            self.assertEqual(st_cmd.get("alvo"), "ananuke")

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
