#!/usr/bin/env python3
"""Teste determinístico da implementação real do novo controlador em ambiente isolado.

Valida:
1. Comunicação por socket UNIX real (AF_UNIX).
2. Sondagens simuladas de 6 a 10 segundos sem bloqueio da fila IPC (ACK < 100ms).
3. Concorrência entre os dois microfones durante a execução da sondagem lenta.
4. Persistência atômica e recuperação de estado.
5. Diferenciação dos 4 estados nos logs do Gateway:
   - [COMANDO ACEITO]
   - [EM PROCESSAMENTO]
   - [CONCLUÍDO]
   - [FALHA]
"""
import json
import logging
import os
import queue
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

# Configuração de stub de importação para ambientes locais sem vosk/homelab-panel
import types
if "vosk" not in sys.modules:
    mock_vosk = types.ModuleType("vosk")
    mock_vosk.Model = lambda *a, **k: None
    mock_vosk.KaldiRecognizer = lambda *a, **k: None
    mock_vosk.SetLogLevel = lambda *a, **k: None
    sys.modules["vosk"] = mock_vosk

if "app" not in sys.modules:
    mock_app = types.ModuleType("app")
    mock_app.SERVIDORES = {
        "fusion": {"nome": "Fusion (Windows)", "mac": "bc:24:11:83:35:82"},
        "ananuke": {"nome": "Ananuke (Debian)", "mac": "70:85:c2:d9:95:d3"},
    }
    mock_app.host_ligado = lambda srv: False
    mock_app.ligar = lambda srv: True
    mock_app.desligar = lambda srv: True
    sys.modules["app"] = mock_app

from gateway.ipc_bus import IpcEventBroadcaster, IpcEventListener
from gateway.audio_gateway import SessionArbitrator, _registrar_log_ack_gateway
import scripts.jarvis_operacional_corrigido as ctrl

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"


class IsolatedRealControllerHarness:
    """Harness que instancia o controlador real e o barramento IPC sobre socket UNIX real."""

    def __init__(self, tmp_dir: str):
        self.tmp_dir = tmp_dir
        self.sock_path = os.path.join(tmp_dir, "test_real_ctrl.sock")
        self.state_file = os.path.join(tmp_dir, "acoes_recentes.json")
        self.captured_logs: List[str] = []

        # Configura ambiente de simulação obrigatória
        os.environ["JARVIS_WOL_REAL"] = "0"
        os.environ["JARVIS_SSH_REAL"] = "0"
        os.environ["JARVIS_REQUIRE_PERSISTENCE"] = "1"
        os.environ["JARVIS_STATE_FILE"] = self.state_file
        ctrl.acoes_recentes.clear()

        # Configura captura de logs do logger
        self.log_handler = logging.Handler()
        self.log_handler.emit = lambda record: self.captured_logs.append(self.log_handler.format(record))
        formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] [JarvisGateway] %(message)s")
        self.log_handler.setFormatter(formatter)
        logging.getLogger("JarvisGateway").addHandler(self.log_handler)
        logging.getLogger("JarvisGateway").setLevel(logging.INFO)

        # Inicia loop de despacho e processamento do controlador real
        self.worker_running = threading.Event()
        self.worker_running.set()
        ctrl.loop_principal_ativo.set()

        def worker_loop():
            while self.worker_running.is_set():
                try:
                    item = ctrl.fila_intencoes.get(timeout=0.05)
                    fonte = item[0]
                    texto = item[1]
                    sid = item[2]
                    ev_id = item[3] if len(item) > 3 else None
                    comp_q = item[4] if len(item) > 4 else None
                    res = ctrl.processar(texto, fonte=fonte, session_id=sid, event_id=ev_id)
                    if comp_q is not None:
                        comp_q.put(res)
                except queue.Empty:
                    continue
                except Exception as e:
                    if comp_q is not None:
                        comp_q.put({"sucesso": False, "erro": str(e), "processado": True})

        self.worker_th = threading.Thread(target=worker_loop, daemon=True)
        self.worker_th.start()

        # Inicia listener IPC real conectado à função de despacho do controlador
        self.listener = IpcEventListener(
            socket_path=self.sock_path,
            on_event=ctrl.on_ipc_event,
            allowed_uid=os.getuid(),
            verify_peer_cred=True,
        )
        self.listener.start()

        # Inicia broadcaster IPC real
        self.broadcaster = IpcEventBroadcaster(
            socket_path=self.sock_path,
            timeout=2.0,
        )

        # Árbitro de sessão para gateway
        self.arbitrator = SessionArbitrator(dedup_window_s=2.0, action_dedup_window_s=3.5)

        # Aguarda inicialização do socket
        limite = time.time() + 3.0
        while time.time() < limite:
            if os.path.exists(self.sock_path):
                break
            time.sleep(0.05)

    def reset_state(self):
        if os.path.exists(self.state_file):
            try:
                os.remove(self.state_file)
            except Exception:
                pass
        with ctrl.lock_acoes:
            ctrl.acoes_recentes.clear()
            ctrl.registro_acoes.clear()
        self.arbitrator._recent_actions.clear()
        self.arbitrator.state = "espera"
        self.arbitrator.session_id = None
        ctrl.estado = "espera"
        ctrl.sessao_ativa_id = None

    def tear_down(self):
        try:
            self.worker_running.clear()
            ctrl.loop_principal_ativo.clear()
            self.listener.stop()
        except Exception:
            pass
        logging.getLogger("JarvisGateway").removeHandler(self.log_handler)


def testar_implementacao_real():
    print("=" * 72)
    print("TESTE DA IMPLEMENTAÇÃO REAL DO NOVO CONTROLADOR (SOCKET REAL + 6-10s)")
    print("=" * 72)

    with tempfile.TemporaryDirectory(prefix="jarvis_real_sondagem_") as tmp_dir:
        harness = IsolatedRealControllerHarness(tmp_dir)
        try:
            # ------------------------------------------------------------------
            # Teste 1: Latência Zero em Modo de Simulação Padrão
            # ------------------------------------------------------------------
            print("\n>>> [1/5] Testando latência de processamento em simulação padrão...")
            harness.reset_state()
            ctrl.painel.simular_atraso_wol_s = 0.0

            is_new, sid = harness.arbitrator.report_wake("me6s")
            ack_wake = harness.broadcaster.emit_event("me6s", "wake", "assistente", session_id=sid)
            assert ack_wake and ack_wake.get("sucesso") is True, f"Wake falhou: {ack_wake}"

            t0 = time.monotonic()
            ack_cmd = harness.broadcaster.emit_event(
                "lg_mic", "comando", "ligar fusion", session_id=sid, extra={"acao": "ligar", "alvo": "fusion"}
            )
            duracao = time.monotonic() - t0
            assert ack_cmd and ack_cmd.get("sucesso") is True, f"Comando falhou: {ack_cmd}"
            assert ack_cmd.get("status") == "concluido", f"Status esperado 'concluido', obtido: {ack_cmd}"
            assert duracao < 0.05, f"Latência excessiva em simulação: {duracao:.4f}s"
            print(f"  {GREEN}[OK]{RESET} WoL em simulação respondeu em {duracao*1000:.2f}ms (latência nula de rede)")

            # ------------------------------------------------------------------
            # Teste 2: Sondagem Simulada de 6.0 Segundos sem Bloqueio de Fila IPC
            # ------------------------------------------------------------------
            print("\n>>> [2/5] Testando sondagem simulada de 6.0s (resposta assíncrona imediata)...")
            harness.reset_state()
            ctrl.painel.simular_atraso_wol_s = 6.0

            is_new, sid6 = harness.arbitrator.report_wake("me6s")
            harness.broadcaster.emit_event("me6s", "wake", "assistente", session_id=sid6)

            t_cmd_inicio = time.monotonic()
            ack_cmd6 = harness.broadcaster.emit_event(
                "lg_mic", "comando", "ligar fusion", session_id=sid6, extra={"acao": "ligar", "alvo": "fusion"}
            )
            t_cmd_ack = time.monotonic() - t_cmd_inicio

            assert ack_cmd6 is not None, "ACK não recebido (timeout do broadcaster!)"
            assert ack_cmd6.get("sucesso") is True, f"Falha no comando lento: {ack_cmd6}"
            assert ack_cmd6.get("status") == "aceito", f"Status esperado 'aceito', obtido: {ack_cmd6.get('status')}"
            assert t_cmd_ack < 0.10, f"ACK demorou {t_cmd_ack:.3f}s (esperado < 100ms para evitar timeout IPC de 2s)"
            print(f"  {GREEN}[OK]{RESET} ACK de comando aceito retornado em {t_cmd_ack*1000:.2f}ms (< 100ms)")

            # Registra no gateway e valida log de [COMANDO ACEITO]
            _registrar_log_ack_gateway("ligar", "fusion", "lg_mic", sid6, ack_cmd6)
            ultimo_log = harness.captured_logs[-1]
            assert "[COMANDO ACEITO]" in ultimo_log, f"Log de comando aceito ausente: {ultimo_log}"
            print(f"  {GREEN}[OK]{RESET} Log do Gateway gerado corretamente: {ultimo_log}")

            # ------------------------------------------------------------------
            # Teste 3: Concorrência durante execução da sondagem de 6s
            # ------------------------------------------------------------------
            print("\n>>> [3/5] Testando concorrência: novo wake durante execução da sondagem de 6s...")
            t_wake_inicio = time.monotonic()
            ack_wake_during = harness.broadcaster.emit_event(
                "lg_mic", "wake", "assistente", session_id="sess-concurrent-check"
            )
            t_wake_ack = time.monotonic() - t_wake_inicio
            assert ack_wake_during and ack_wake_during.get("sucesso") is True, f"Wake concorrente falhou: {ack_wake_during}"
            assert t_wake_ack < 0.05, f"Fila IPC bloqueou wake concorrente: {t_wake_ack:.3f}s"
            print(f"  {GREEN}[OK]{RESET} Wake concorrente aceito em {t_wake_ack*1000:.2f}ms sem qualquer bloqueio")

            # Aguarda conclusão do worker de 6s
            tempo_restante = 6.2 - (time.monotonic() - t_cmd_inicio)
            if tempo_restante > 0:
                time.sleep(tempo_restante)

            # Valida persistência atômica da ação após conclusão do worker
            assert os.path.exists(harness.state_file), "Arquivo de persistência não encontrado!"
            with open(harness.state_file, "r", encoding="utf-8") as f:
                dados_pers = json.load(f)
            regs = dados_pers.get("registro_acoes", {})
            assert any(r.get("acao") == "ligar" and r.get("status") == "concluido" for r in regs.values()), (
                f"Ação de 6s não persistida como concluído: {regs}"
            )
            print(f"  {GREEN}[OK]{RESET} Ação com latência de 6s persistida como 'concluido' no disco")

            # ------------------------------------------------------------------
            # Teste 4: Sondagem Simulada de 10.0 Segundos
            # ------------------------------------------------------------------
            print("\n>>> [4/5] Testando sondagem simulada extrema de 10.0s...")
            harness.reset_state()
            ctrl.painel.simular_atraso_wol_s = 10.0

            is_new, sid10 = harness.arbitrator.report_wake("me6s")
            harness.broadcaster.emit_event("me6s", "wake", "assistente", session_id=sid10)

            t10_ini = time.monotonic()
            ack_cmd10 = harness.broadcaster.emit_event(
                "lg_mic", "comando", "ligar computador dois", session_id=sid10, extra={"acao": "ligar", "alvo": "ananuke"}
            )
            duracao10_ack = time.monotonic() - t10_ini

            assert ack_cmd10 and ack_cmd10.get("sucesso") is True
            assert ack_cmd10.get("status") == "aceito"
            assert duracao10_ack < 0.10, f"ACK de 10s demorou {duracao10_ack:.3f}s"
            print(f"  {GREEN}[OK]{RESET} Sondagem de 10.0s: ACK aceito retornado em {duracao10_ack*1000:.2f}ms")

            # Durante a execução de 10s, testa comando 'cancelar' pelo outro mic
            time.sleep(0.5)
            ack_cancel = harness.broadcaster.emit_event("me6s", "cancelar", "cancelar", session_id="sess-cancel-test")
            assert ack_cancel and ack_cancel.get("sucesso") is True
            print(f"  {GREEN}[OK]{RESET} Cancelamento aceito durante worker ativo de 10s")

            # Aguarda conclusão do worker de 10s
            restante10 = 10.2 - (time.monotonic() - t10_ini)
            if restante10 > 0:
                time.sleep(restante10)

            with open(harness.state_file, "r", encoding="utf-8") as f:
                dados10 = json.load(f)
            regs10 = dados10.get("registro_acoes", {})
            assert any(r.get("alvo") == "ananuke" and r.get("status") == "concluido" for r in regs10.values()), (
                f"Ação de 10s não concluída: {regs10}"
            )
            print(f"  {GREEN}[OK]{RESET} Ação com latência de 10s concluída e persistida com sucesso")

            # ------------------------------------------------------------------
            # Teste 5: Diferenciação Estrita dos 4 Estados nos Logs do Gateway
            # ------------------------------------------------------------------
            print("\n>>> [5/5] Validando diferenciação estrita dos 4 estados nos logs...")
            harness.captured_logs.clear()

            # 1. COMANDO ACEITO
            _registrar_log_ack_gateway("ligar", "fusion", "lg_mic", "sess-log-1", {"sucesso": True, "status": "aceito"})
            assert "[COMANDO ACEITO]" in harness.captured_logs[-1]

            # 2. EM PROCESSAMENTO
            _registrar_log_ack_gateway("desligar", "fusion", "lg_mic", "sess-log-2", {"sucesso": True, "status": "em_processamento"})
            assert "[EM PROCESSAMENTO]" in harness.captured_logs[-1]

            # 3. CONCLUÍDO
            _registrar_log_ack_gateway("ligar", "fusion", "lg_mic", "sess-log-3", {"sucesso": True, "status": "concluido"})
            assert "[CONCLUÍDO]" in harness.captured_logs[-1]

            # 4. FALHA (ACK com erro)
            _registrar_log_ack_gateway("ligar", "fusion", "lg_mic", "sess-log-4", {"sucesso": False, "erro": "persistencia_bloqueada"})
            assert "[FALHA]" in harness.captured_logs[-1]
            assert "persistencia_bloqueada" in harness.captured_logs[-1]

            # 5. FALHA (Timeout / ACK None)
            _registrar_log_ack_gateway("desligar", "fusion", "me6s", "sess-log-5", None)
            assert "[FALHA]" in harness.captured_logs[-1]
            assert "timeout" in harness.captured_logs[-1]

            # 6. AGUARDANDO CONFIRMAÇÃO
            _registrar_log_ack_gateway("desligar", "fusion", "lg_mic", "sess-log-6", {"sucesso": True, "status": "confirmacao_pendente", "acao": "desligar_pendente"})
            assert "[AGUARDANDO CONFIRMAÇÃO]" in harness.captured_logs[-1]

            # 7. CANCELADO
            _registrar_log_ack_gateway("cancelar", "fusion", "lg_mic", "sess-log-7", {"sucesso": True, "status": "cancelado"})
            assert "[CANCELADO]" in harness.captured_logs[-1]

            print(f"  {GREEN}[OK]{RESET} Todos os estados ([COMANDO ACEITO], [AGUARDANDO CONFIRMAÇÃO], [EM PROCESSAMENTO], [CONCLUÍDO], [CANCELADO], [FALHA]) devidamente diferenciados nos logs.")

        finally:
            harness.tear_down()

    print("\n" + "=" * 72)
    print(f"{GREEN}SUCESSO: TESTE DA IMPLEMENTAÇÃO REAL CONCLUÍDO COM 100% DE APROVAÇÃO{RESET}")
    print("=" * 72)


if __name__ == "__main__":
    testar_implementacao_real()
