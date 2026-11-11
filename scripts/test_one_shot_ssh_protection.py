#!/usr/bin/env python3
"""
Bateria de Testes Unitários e de Integração da Proteção de Execução Única (One-Shot SSH).
Valida exaustivamente todas as regras de segurança contra reexecuções, reinicializações e falhas:
1. Token válido consumido exatamente uma vez.
2. Bloqueio estrito de reexecução (fail-closed, tanto em disco quanto em memória).
3. Arquivo de token ausente -> bloqueio imediato e fallback para simulação.
4. Arquivo de token corrompido -> bloqueio imediato e fallback para simulação.
5. Token expirado por TTL -> bloqueio imediato.
6. Isolamento estrito de alvo (token de fusion não autoriza ananuke e vice-versa).
7. Isolamento de ação (token de ligar não autoriza desligar).
8. Proteção contra reinicialização: token com PID vinculado diferente é rejeitado.
9. Reconciliação na inicialização: tokens órfãos são invalidados automaticamente.
10. Concorrência: 20 threads disputando o mesmo token -> estritamente 1 vence.
11. Integração fim a fim no worker SSH: comando real despachado uma única vez.
12. Isolamento fim a fim: Ananuke NUNCA executa SSH real mesmo com flag JARVIS_SSH_REAL=1.
13. Falha no subprocesso consome o token e não permite retentativa automática.
"""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Configuração de paths
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "scripts"))
sys.path.insert(0, "/home/tuninho/homelab-panel")

# Mock de vosk antes de importar o script operacional
import types
if "vosk" not in sys.modules:
    mock_vosk = types.ModuleType("vosk")
    mock_vosk.Model = MagicMock()
    mock_vosk.KaldiRecognizer = MagicMock()
    mock_vosk.SetLogLevel = MagicMock()
    sys.modules["vosk"] = mock_vosk

if "app" not in sys.modules:
    mock_app = types.ModuleType("app")
    mock_app.SERVIDORES = {
        "fusion": {"nome": "Fusion (Windows)", "mac": "18:31:BF:6A:EA:50", "ip": "192.168.0.128", "ssh": "anakin@192.168.0.128", "cmd": "shutdown /s /t 0"},
        "ananuke": {"nome": "Ananuke (Debian)", "mac": "0a:e0:af:c3:06:fc", "ip": "192.168.0.131", "ssh": "anakin@192.168.0.131", "cmd": "sudo -n poweroff"},
    }
    mock_app.host_ligado = lambda srv: True
    mock_app.montar_comando_ssh = lambda cfg: ["ssh", "-o", "BatchMode=yes", cfg.get("ssh", "user@host"), cfg.get("cmd", "poweroff")]
    sys.modules["app"] = mock_app

import jarvis_operacional_corrigido as jarvis


class TestOneShotSshProtection(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="jarvis_oneshot_test_")
        self.state_file = os.path.join(self.tmpdir, "acoes_recentes.json")
        self.token_file = os.path.join(self.tmpdir, "one_shot_ssh_token.json")

        os.environ["JARVIS_STATE_FILE"] = self.state_file
        os.environ["JARVIS_ONE_SHOT_TOKEN_FILE"] = self.token_file
        os.environ["JARVIS_ALLOWED_REAL_TARGETS"] = "fusion"
        os.environ["JARVIS_REQUIRE_PERSISTENCE"] = "1"
        os.environ["JARVIS_WOL_REAL"] = "0"
        os.environ["JARVIS_SSH_REAL"] = "0"

        jarvis._resetar_estado_token_memoria()
        jarvis.acoes_recentes.clear()
        jarvis.registro_acoes.clear()
        jarvis.estado = "espera"
        jarvis.pendente = None
        jarvis.sessao_ativa_id = None
        jarvis.fonte_ativa = None

    def tearDown(self):
        jarvis._resetar_estado_token_memoria()
        if os.path.exists(self.tmpdir):
            shutil.rmtree(self.tmpdir)

    def _criar_token_valido(self, alvo="fusion", acao="desligar", ttl=300, bound_pid=None):
        agora = time.time()
        token = {
            "token_id": f"ost-{alvo}-{int(agora * 1000)}",
            "alvo": alvo,
            "acao": acao,
            "created_at": agora,
            "ttl_seconds": ttl,
            "expires_at": agora + ttl,
            "consumed": False,
            "consumed_at": None,
            "consumed_by_event": None,
            "consumed_by_pid": None,
            "bound_service_pid": bound_pid,
            "status": "active",
        }
        with open(self.token_file, "w", encoding="utf-8") as f:
            json.dump(token, f, indent=2)
        os.chmod(self.token_file, 0o600)
        return token

    def test_01_token_creation_and_validation(self):
        """1. Token válido recém-criado passa na validação."""
        self._criar_token_valido(alvo="fusion", acao="desligar")
        valido, dados = jarvis.verificar_token_one_shot("fusion", "desligar")
        self.assertTrue(valido)
        self.assertEqual(dados["alvo"], "fusion")
        self.assertEqual(dados["status"], "active")

    def test_02_token_consumed_once_and_blocks_further_attempts(self):
        """2. Token é consumido atomicamente e bloqueia qualquer tentativa subsequente."""
        self._criar_token_valido(alvo="fusion", acao="desligar")
        
        # Primeiro consumo: deve ter sucesso
        ok, res = jarvis.consumir_token_one_shot("fusion", "desligar", event_id="ev-teste-1")
        self.assertTrue(ok)
        self.assertTrue(res["consumed"])
        self.assertEqual(res["status"], "consumed")
        self.assertEqual(res["consumed_by_event"], "ev-teste-1")

        # Segundo consumo: deve ser rejeitado imediatamente
        ok2, res2 = jarvis.consumir_token_one_shot("fusion", "desligar", event_id="ev-teste-2")
        self.assertFalse(ok2)
        self.assertIn("consumido", res2)

        # Verificação em disco
        with open(self.token_file, "r", encoding="utf-8") as f:
            disco = json.load(f)
        self.assertTrue(disco["consumed"])
        self.assertEqual(disco["status"], "consumed")

    def test_03_token_missing_fail_closed(self):
        """3. Arquivo de token ausente resulta em falha estrita (fail-closed)."""
        if os.path.exists(self.token_file):
            os.unlink(self.token_file)
        valido, motivo = jarvis.verificar_token_one_shot("fusion", "desligar")
        self.assertFalse(valido)
        self.assertEqual(motivo, "token_arquivo_inexistente")

        ok, motivo_c = jarvis.consumir_token_one_shot("fusion", "desligar")
        self.assertFalse(ok)
        self.assertEqual(motivo_c, "token_arquivo_inexistente")

    def test_04_token_corrupted_fail_closed(self):
        """4. Arquivo de token corrompido é rejeitado com segurança sem quebrar o processo."""
        with open(self.token_file, "w") as f:
            f.write("CONTEUDO_INVALIDO_NAO_JSON{[[")
        valido, motivo = jarvis.verificar_token_one_shot("fusion", "desligar")
        self.assertFalse(valido)
        self.assertIn("token_corrompido", motivo)

    def test_05_token_expired_ttl(self):
        """5. Token com TTL vencido é categoricamente rejeitado."""
        self._criar_token_valido(alvo="fusion", acao="desligar", ttl=-10)
        valido, motivo = jarvis.verificar_token_one_shot("fusion", "desligar")
        self.assertFalse(valido)
        self.assertIn("token_expirado", motivo)

        ok, motivo_c = jarvis.consumir_token_one_shot("fusion", "desligar")
        self.assertFalse(ok)
        self.assertIn("token_expirado", motivo_c)

    def test_06_token_target_isolation(self):
        """6. Token para 'fusion' não autoriza 'ananuke' e vice-versa."""
        self._criar_token_valido(alvo="fusion", acao="desligar")
        valido, motivo = jarvis.verificar_token_one_shot("ananuke", "desligar")
        self.assertFalse(valido)
        self.assertIn("token_alvo_incompativel", motivo)

        ok, motivo_c = jarvis.consumir_token_one_shot("ananuke", "desligar")
        self.assertFalse(ok)
        self.assertIn("token_alvo_incompativel", motivo_c)

    def test_07_token_action_isolation(self):
        """7. Token de 'ligar' não autoriza 'desligar'."""
        self._criar_token_valido(alvo="fusion", acao="ligar")
        valido, motivo = jarvis.verificar_token_one_shot("fusion", "desligar")
        self.assertFalse(valido)
        self.assertIn("token_acao_incompativel", motivo)

    def test_08_token_bound_pid_protection(self):
        """8. Token vinculado a outro PID é rejeitado (proteção contra reinício de serviço)."""
        self._criar_token_valido(alvo="fusion", acao="desligar", bound_pid=9999999)
        valido, motivo = jarvis.verificar_token_one_shot("fusion", "desligar")
        self.assertFalse(valido)
        self.assertIn("token_invalido_por_reinicio", motivo)

    def test_09_token_reconciliation_on_restart(self):
        """9. Reconciliação na inicialização invalida tokens ativos de PIDs anteriores."""
        self._criar_token_valido(alvo="fusion", acao="desligar", bound_pid=9999999)
        jarvis._reconciliar_token_one_shot_inicializacao()

        with open(self.token_file, "r", encoding="utf-8") as f:
            disco = json.load(f)
        self.assertEqual(disco["status"], "invalidated_by_restart")
        self.assertIn("service_restarted", disco.get("invalidation_reason", ""))

    def test_10_concurrent_consumption_thread_safety(self):
        """10. Teste de estresse concorrente: 20 threads tentam consumir o token -> apenas 1 vence."""
        self._criar_token_valido(alvo="fusion", acao="desligar")
        resultados = []
        barreira = threading.Barrier(20)

        def _worker(tid):
            barreira.wait()
            ok, res = jarvis.consumir_token_one_shot("fusion", "desligar", event_id=f"ev-th-{tid}")
            resultados.append((tid, ok, res))

        threads = [threading.Thread(target=_worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        vencedores = [r for r in resultados if r[1] is True]
        perdedores = [r for r in resultados if r[1] is False]

        self.assertEqual(len(vencedores), 1, f"Exatamente um vencedor esperado, obtido: {len(vencedores)}")
        self.assertEqual(len(perdedores), 19)

    def test_11_end_to_end_ssh_worker_with_valid_token(self):
        """11. Integração fim a fim: Com token válido e SSH_REAL=1, executa exatamente UMA vez."""
        os.environ["JARVIS_SSH_REAL"] = "1"
        os.environ["JARVIS_ALLOWED_REAL_TARGETS"] = "fusion"
        self._criar_token_valido(alvo="fusion", acao="desligar", bound_pid=os.getpid())

        mock_cmd_ssh = ["ssh", "-o", "BatchMode=yes", "anakin@192.168.0.128", "shutdown /s /t 0"]
        mock_result = subprocess.CompletedProcess(args=mock_cmd_ssh, returncode=0, stdout="", stderr="")

        with patch.object(jarvis.painel, "host_ligado", return_value=True), \
             patch.object(jarvis.painel, "montar_comando_ssh", return_value=mock_cmd_ssh), \
             patch("subprocess.run", return_value=mock_result) as mock_run:

            # 1. Ativação
            r_wake = jarvis.processar("assistente", fonte="lg_mic")
            self.assertEqual(jarvis.estado, "comando")

            # 2. Comando desligar fusion
            r_desl = jarvis.processar("desligar fusion", fonte="lg_mic")
            self.assertEqual(jarvis.estado, "confirmacao")
            self.assertEqual(jarvis.pendente, "fusion")

            # 3. Confirmação
            r_conf = jarvis.processar("confirmar desligamento", fonte="me6s")
            time.sleep(0.2)

            self.assertEqual(mock_run.call_count, 1)
            mock_run.assert_called_once_with(
                mock_cmd_ssh,
                capture_output=True,
                text=True,
                timeout=25
            )

            # Valida consumo do token
            with open(self.token_file, "r", encoding="utf-8") as f:
                disco = json.load(f)
            self.assertTrue(disco["consumed"])

            # 4. Segunda tentativa imediata de desligamento: DEVE EXECUTAR EM SIMULAÇÃO (SEM NOVO SUBPROCESS.RUN)
            time.sleep(4.0)  # Passa a janela de idempotência para testar a proteção do token
            jarvis.processar("assistente", fonte="lg_mic")
            jarvis.processar("desligar fusion", fonte="lg_mic")
            jarvis.processar("confirmar desligamento", fonte="me6s")
            time.sleep(0.2)

            # mock_run continua com count == 1 (nenhuma chamada extra realizada)
            self.assertEqual(mock_run.call_count, 1)

    def test_12_end_to_end_ssh_worker_ananuke_fails_closed(self):
        """12. Integração fim a fim: Ananuke NUNCA executa subprocess.run mesmo com JARVIS_SSH_REAL=1."""
        os.environ["JARVIS_SSH_REAL"] = "1"
        os.environ["JARVIS_ALLOWED_REAL_TARGETS"] = "fusion"  # Apenas fusion
        self._criar_token_valido(alvo="fusion", acao="desligar", bound_pid=os.getpid())

        with patch.object(jarvis.painel, "host_ligado", return_value=True), \
             patch("subprocess.run") as mock_run:

            jarvis.processar("assistente", fonte="lg_mic")
            jarvis.processar("desligar computador dois", fonte="lg_mic")
            self.assertEqual(jarvis.estado, "confirmacao")
            self.assertEqual(jarvis.pendente, "ananuke")

            jarvis.processar("confirmar desligamento", fonte="me6s")
            time.sleep(0.1)

            mock_run.assert_not_called()

    def test_13_end_to_end_subprocess_error_consumes_token(self):
        """13. Falha ou timeout no subprocesso consome o token e não permite retentativa."""
        os.environ["JARVIS_SSH_REAL"] = "1"
        os.environ["JARVIS_ALLOWED_REAL_TARGETS"] = "fusion"
        self._criar_token_valido(alvo="fusion", acao="desligar", bound_pid=os.getpid())

        mock_cmd_ssh = ["ssh", "-o", "BatchMode=yes", "anakin@192.168.0.128", "shutdown /s /t 0"]
        mock_fail = subprocess.CompletedProcess(args=mock_cmd_ssh, returncode=255, stdout="", stderr="Connection refused")

        with patch.object(jarvis.painel, "host_ligado", return_value=True), \
             patch.object(jarvis.painel, "montar_comando_ssh", return_value=mock_cmd_ssh), \
             patch("subprocess.run", return_value=mock_fail) as mock_run:

            jarvis.processar("assistente", fonte="lg_mic")
            jarvis.processar("desligar fusion", fonte="lg_mic")
            jarvis.processar("confirmar desligamento", fonte="me6s")
            time.sleep(0.2)

            self.assertEqual(mock_run.call_count, 1)

            # Token foi consumido mesmo com retorno com falha
            with open(self.token_file, "r", encoding="utf-8") as f:
                disco = json.load(f)
            self.assertTrue(disco["consumed"])
            self.assertEqual(disco["status"], "consumed")


if __name__ == "__main__":
    unittest.main()
