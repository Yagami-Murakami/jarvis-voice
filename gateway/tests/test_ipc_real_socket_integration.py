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
import json
import os
import queue
import shutil
import socket
import tempfile
import threading
import time
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
        # Carrega o módulo isolado do controlador corrigido uma única vez
        target_path = Path(__file__).resolve().parent.parent.parent / "scripts" / "jarvis_operacional_corrigido.py"
        spec = importlib.util.spec_from_file_location("jarvis_op_test", str(target_path))
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.sock_path = os.path.join(self.temp_dir, "real_ipc_test.sock")

        # Reseta o estado do controlador
        self.mod.estado = "espera"
        self.mod.pendente = None
        self.mod.sessao_ativa_id = None
        self.mod.fonte_ativa = None
        self.mod.ultima_ativacao = 0.0
        with self.mod.lock_acoes:
            self.mod.acoes_recentes.clear()
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
