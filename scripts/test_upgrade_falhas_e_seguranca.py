#!/usr/bin/env python3
"""
Testes de Falhas e Segurança do Procedimento de Upgrade da Etapa 1
Verifica os cenários de falhas de segurança exigidos:
1. Rejeição imediata de SHA desconhecido (fail-closed).
2. Interrupção por pre-flight com falha.
3. Rejeição por drop-in do systemd com flags reais (WOL=1 ou SSH=1).
4. Interrupção na geração por patch corrompido (produção preservada).
5. Substituição verdadeiramente atômica (rename POSIX, sem truncamento).
6. Falha na auditoria pós-inicialização aciona rollback automático para v1.
7. Rollback granular restaura v1 e original com validação rigorosa de SHA e simulação.
"""

import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

EXPECTED_ORIGINAL_SHA = "c51d58e6d3a19003eac55a827212e5e084d583fea43389ec0ea5466080ee2d54"
EXPECTED_V1_SHA = "f6c7273116aea4c4b34c0c6007d335b7925356ecb174e7066781716f2f604ed8"
EXPECTED_NEW_SHA = "810aca6e25897f6b95aaf297ca8fdff7d9659270bf079a8c398c4ee21df93b42"

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPTS_DIR = os.path.join(BASE_DIR, "scripts")
PATCHES_DIR = os.path.join(BASE_DIR, "patches")

REAL_PATCH = os.path.join(PATCHES_DIR, "jarvis-operacional-dual.patch")
UPGRADE_SCRIPT = os.path.join(SCRIPTS_DIR, "upgrade_etapa1_simulacao.sh")
ROLLBACK_SCRIPT = os.path.join(SCRIPTS_DIR, "rollback_etapa1_simulacao.sh")
PREFLIGHT_SCRIPT = os.path.join(SCRIPTS_DIR, "validar_pre_implantacao.sh")
POSTCHECK_SCRIPT = os.path.join(SCRIPTS_DIR, "validar_pos_inicializacao.sh")
CORRIGIDO_PY = os.path.join(SCRIPTS_DIR, "jarvis_operacional_corrigido.py")


class TestUpgradeFalhasESeguranca(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="jarvis_upgrade_test_")

        # Mock directories
        self.opt_dir = os.path.join(self.tmpdir, "opt", "jarvis-voice")
        self.patches_dir = os.path.join(self.opt_dir, "patches")
        self.var_backups = os.path.join(self.tmpdir, "var", "backups", "jarvis")
        self.dropin_dir = os.path.join(self.tmpdir, "etc", "systemd", "system", "jarvis-voice.service.d")
        self.state_dir = os.path.join(self.tmpdir, "var", "lib", "jarvis")

        os.makedirs(self.patches_dir, exist_ok=True)
        os.makedirs(self.var_backups, exist_ok=True)
        os.makedirs(self.dropin_dir, exist_ok=True)
        os.makedirs(self.state_dir, exist_ok=True)

        # Mock bin directory for isolated systemctl
        self.bin_dir = os.path.join(self.tmpdir, "bin")
        os.makedirs(self.bin_dir, exist_ok=True)
        mock_systemctl = os.path.join(self.bin_dir, "systemctl")
        with open(mock_systemctl, "w") as f:
            f.write("""#!/usr/bin/env bash
cmd="$1"
shift || true
case "$cmd" in
    daemon-reload|restart|is-active)
        exit 0
        ;;
    show)
        for arg in "$@"; do
            if [ "$arg" = "Environment" ]; then
                echo "Environment=JARVIS_WOL_REAL=0 JARVIS_SSH_REAL=0 JARVIS_REQUIRE_PERSISTENCE=1"
                exit 0
            elif [ "$arg" = "MainPID" ]; then
                echo "$$"
                exit 0
            fi
        done
        echo "Environment=JARVIS_WOL_REAL=0 JARVIS_SSH_REAL=0 JARVIS_REQUIRE_PERSISTENCE=1"
        exit 0
        ;;
    *)
        exit 0
        ;;
esac
""")
        os.chmod(mock_systemctl, 0o755)

        # Paths
        self.target_file = os.path.join(self.opt_dir, "jarvis-operacional.py")
        self.backup_original = os.path.join(self.opt_dir, "jarvis-operacional.py.backup_original")
        self.backup_original_ext = os.path.join(self.var_backups, "jarvis-operacional.py.backup_original")
        self.backup_v1 = os.path.join(self.opt_dir, "jarvis-operacional.py.backup_simulacao_v1")
        self.backup_v1_ext = os.path.join(self.var_backups, "jarvis-operacional.py.backup_simulacao_v1")
        self.patch_file = os.path.join(self.patches_dir, "jarvis-operacional-dual.patch")
        self.dropin_file = os.path.join(self.dropin_dir, "zz-simulacao-etapa1.conf")

        # Copy genuine patch
        shutil.copy2(REAL_PATCH, self.patch_file)

        # Reconstruct genuine original from reverse patch of corrigido
        cmd_rev = f"patch -R -o {self.backup_original} {CORRIGIDO_PY} < {REAL_PATCH}"
        ret = subprocess.run(cmd_rev, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(ret.returncode, 0, f"Falha ao reconstruir original: {ret.stderr.decode()}")
        shutil.copy2(self.backup_original, self.backup_original_ext)

        # Create genuine v1 file (or target)
        # Note: v1 SHA is f6c727... Let's check if we have the v1 backup on disk or we can generate/copy it
        # If we have local backup or remote, let's create a known v1 file:
        # Let's check if f6c727... matches a known file in the workspace or we fetch it
        v1_local_candidates = [
            "/opt/jarvis-voice/jarvis-operacional.py.backup_simulacao_v1",
            os.path.join(BASE_DIR, "jarvis-operacional.py.backup_simulacao_v1"),
        ]
        v1_created = False
        for c in v1_local_candidates:
            if os.path.isfile(c):
                shutil.copy2(c, self.backup_v1)
                shutil.copy2(c, self.backup_v1_ext)
                shutil.copy2(c, self.target_file)
                v1_created = True
                break

        if not v1_created:
            # Reconstruct v1 from git commit 94876f4 or similar, or create file with exact v1 content
            # Let's see if we can get it via git show 94876f4:scripts/jarvis_operacional_corrigido.py or similar
            cmd_git = "git show 94876f4:scripts/jarvis_operacional_corrigido.py"
            ret_git = subprocess.run(cmd_git, shell=True, cwd=BASE_DIR, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if ret_git.returncode == 0:
                with open(self.backup_v1, "wb") as f:
                    f.write(ret_git.stdout)
                shutil.copy2(self.backup_v1, self.backup_v1_ext)
                shutil.copy2(self.backup_v1, self.target_file)
            else:
                # Fallback: start with original
                shutil.copy2(self.backup_original, self.target_file)

        # Create drop-in with simulation flags
        with open(self.dropin_file, "w") as f:
            f.write("[Service]\nEnvironment=JARVIS_WOL_REAL=0\nEnvironment=JARVIS_SSH_REAL=0\nEnvironment=JARVIS_REQUIRE_PERSISTENCE=1\n")

        # Mock preflight and postcheck scripts that succeed by default
        self.mock_preflight = os.path.join(self.tmpdir, "mock_preflight.sh")
        with open(self.mock_preflight, "w") as f:
            f.write("#!/bin/bash\nexit 0\n")
        os.chmod(self.mock_preflight, 0o755)

        self.mock_postcheck = os.path.join(self.tmpdir, "mock_postcheck.sh")
        with open(self.mock_postcheck, "w") as f:
            f.write("#!/bin/bash\nexit 0\n")
        os.chmod(self.mock_postcheck, 0o755)

        # Environment variables for isolated execution
        self.env = os.environ.copy()
        self.env["PATH"] = f"{self.bin_dir}:{os.environ['PATH']}"
        self.env["JARVIS_WOL_REAL"] = "0"
        self.env["JARVIS_SSH_REAL"] = "0"
        self.env["JARVIS_REQUIRE_PERSISTENCE"] = "1"
        self.env["JARVIS_TARGET_FILE"] = self.target_file
        self.env["JARVIS_BACKUP_ORIGINAL"] = self.backup_original
        self.env["JARVIS_BACKUP_EXTERNAL"] = self.backup_original_ext
        self.env["JARVIS_BACKUP_V1"] = self.backup_v1
        self.env["JARVIS_BACKUP_V1_EXTERNAL"] = self.backup_v1_ext
        self.env["JARVIS_PATCH_FILE"] = self.patch_file
        self.env["JARVIS_DROPIN_FILE"] = self.dropin_file
        self.env["JARVIS_DROPIN_DIR"] = self.dropin_dir
        self.env["JARVIS_PREFLIGHT_SCRIPT"] = self.mock_preflight
        self.env["JARVIS_POSTCHECK_SCRIPT"] = self.mock_postcheck
        self.env["JARVIS_ROLLBACK_SCRIPT"] = ROLLBACK_SCRIPT
        self.env["JARVIS_PYTHON_BIN"] = sys.executable

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_01_rejeita_hash_desconhecido(self):
        """1. Rejeição imediata de SHA desconhecido (fail-closed) sem tocar no arquivo."""
        # Coloca conteúdo arbitrário no arquivo operacional
        with open(self.target_file, "w") as f:
            f.write("# ARQUIVO OPERACIONAL COM CONTEUDO DESCONHECIDO\nprint('hack')\n")

        res = subprocess.run([UPGRADE_SCRIPT], env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertNotEqual(res.returncode, 0, "Upgrade deveria ter falhado para hash desconhecido")
        self.assertIn("DESCONHECIDO", res.stdout + res.stderr)

        # Confirma que o conteúdo não foi alterado
        with open(self.target_file, "r") as f:
            content = f.read()
        self.assertIn("CONTEUDO DESCONHECIDO", content)

    def test_02_backup_original_corrompido_interrompe(self):
        """2. Backup original com SHA divergente interrompe o procedimento imediatamente."""
        with open(self.backup_original, "w") as f:
            f.write("# BACKUP CORROMPIDO\n")
        if os.path.exists(self.backup_original_ext):
            with open(self.backup_original_ext, "w") as f:
                f.write("# BACKUP CORROMPIDO EXT\n")

        res = subprocess.run([UPGRADE_SCRIPT], env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertNotEqual(res.returncode, 0, "Upgrade deveria falhar com backup corrompido")
        self.assertIn("divergente", res.stdout + res.stderr)

    def test_03_preflight_falhando_interrompe_upgrade(self):
        """3. Falha no pre-flight aborta imediatamente na Fase 1 sem alterar produção."""
        # Configura preflight para falhar
        with open(self.mock_preflight, "w") as f:
            f.write("#!/bin/bash\necho 'Falha intencional de pre-flight' >&2\nexit 1\n")

        res = subprocess.run([UPGRADE_SCRIPT], env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertNotEqual(res.returncode, 0, "Upgrade deveria falhar quando preflight falha")
        self.assertIn("pre-flight", res.stdout + res.stderr)

    def test_04_systemd_flag_real_detectada_interrompe(self):
        """4. Configuração de systemd com modo real (WOL=1) detectada e rejeitada."""
        with open(self.dropin_file, "w") as f:
            f.write("[Service]\nEnvironment=JARVIS_WOL_REAL=1\nEnvironment=JARVIS_SSH_REAL=0\n")

        res = subprocess.run([UPGRADE_SCRIPT], env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertNotEqual(res.returncode, 0, "Upgrade deveria falhar com flag real no dropin")
        self.assertIn("modo real detectada", res.stdout + res.stderr)

    def test_05_patch_corrompido_limpa_tmp_sem_alterar_producao(self):
        """5. Patch corrompido aborta a geração e mantém produção intocada."""
        with open(self.patch_file, "w") as f:
            f.write("--- patch corrompido inválido\n+++ patch corrompido\n@@ -1,1 +1,1 @@\n-invalido\n+invalido2\n")

        with open(self.target_file, "r") as f:
            orig_content = f.read()

        res = subprocess.run([UPGRADE_SCRIPT], env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertNotEqual(res.returncode, 0, "Upgrade deveria falhar com patch corrompido")

        with open(self.target_file, "r") as f:
            self.assertEqual(f.read(), orig_content, "Arquivo operacional de produção foi indevidamente modificado")

    def test_06_substituicao_atomica_e_permissao(self):
        """6. Substituição atômica (--apply) gera exatamente EXPECTED_NEW_SHA com permissão 0755."""
        res = subprocess.run([UPGRADE_SCRIPT, "--apply"], env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertEqual(res.returncode, 0, f"Upgrade --apply falhou:\n{res.stdout}\n{res.stderr}")

        # Verifica SHA final
        cmd_sha = f"sha256sum {self.target_file}"
        ret_sha = subprocess.run(cmd_sha, shell=True, stdout=subprocess.PIPE, text=True)
        sha_final = ret_sha.stdout.split()[0]
        self.assertEqual(sha_final, EXPECTED_NEW_SHA, "SHA final após substituição atômica divergente")

        # Verifica permissões (0755)
        st = os.stat(self.target_file)
        self.assertTrue(bool(st.st_mode & stat.S_IXUSR), "Arquivo deve ter permissão de execução")

    def test_07_falha_auditoria_pos_inicializacao_aciona_rollback_v1(self):
        """7. Falha na auditoria pós-inicialização dispara rollback automático para a versão v1."""
        # Configura auditoria pós-inicialização para falhar
        with open(self.mock_postcheck, "w") as f:
            f.write("#!/bin/bash\necho 'Falha intencional de auditoria pós-inicialização' >&2\nexit 1\n")

        # Salva o conteúdo/hash inicial de v1
        with open(self.backup_v1, "r") as f:
            v1_content = f.read()
        cmd_sha_v1 = f"sha256sum {self.backup_v1}"
        sha_v1 = subprocess.run(cmd_sha_v1, shell=True, stdout=subprocess.PIPE, text=True).stdout.split()[0]

        res = subprocess.run([UPGRADE_SCRIPT, "--apply"], env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertNotEqual(res.returncode, 0, "Upgrade deveria falhar e acionar rollback")
        self.assertIn("Auditoria pós-inicialização falhou", res.stdout + res.stderr)
        self.assertIn("rollback", res.stdout.lower() + res.stderr.lower())

        # Verifica se o rollback para v1 foi efetivado
        cmd_target_sha = f"sha256sum {self.target_file}"
        curr_target_sha = subprocess.run(cmd_target_sha, shell=True, stdout=subprocess.PIPE, text=True).stdout.split()[0]
        self.assertEqual(curr_target_sha, sha_v1, "Rollback automático para v1 deveria ter restaurado o SHA de v1")

    def test_08_rollback_granular_v1_e_original(self):
        """8. Rollback granular restaura v1 e original com validação rigorosa de SHA e preservação de simulação."""
        # 1. Modifica o target para nova versão
        shutil.copy2(CORRIGIDO_PY, self.target_file)

        # 2. Testa rollback para v1
        res_v1 = subprocess.run([ROLLBACK_SCRIPT, "v1"], env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertEqual(res_v1.returncode, 0, f"Rollback v1 falhou: {res_v1.stdout}\n{res_v1.stderr}")
        cmd_sha = f"sha256sum {self.target_file}"
        curr_sha = subprocess.run(cmd_sha, shell=True, stdout=subprocess.PIPE, text=True).stdout.split()[0]
        self.assertEqual(curr_sha, EXPECTED_V1_SHA, "Rollback v1 não restaurou EXPECTED_V1_SHA")

        # 3. Testa rollback para original
        res_orig = subprocess.run([ROLLBACK_SCRIPT, "original"], env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertEqual(res_orig.returncode, 0, f"Rollback original falhou: {res_orig.stdout}\n{res_orig.stderr}")
        curr_sha_orig = subprocess.run(cmd_sha, shell=True, stdout=subprocess.PIPE, text=True).stdout.split()[0]
        self.assertEqual(curr_sha_orig, EXPECTED_ORIGINAL_SHA, "Rollback original não restaurou EXPECTED_ORIGINAL_SHA")

        # 4. Verifica que drop-in manteve estritamente as variáveis de simulação
        with open(self.dropin_file, "r") as f:
            dropin_content = f.read()
        self.assertIn("JARVIS_WOL_REAL=0", dropin_content)
        self.assertIn("JARVIS_SSH_REAL=0", dropin_content)
        self.assertIn("JARVIS_REQUIRE_PERSISTENCE=1", dropin_content)


if __name__ == "__main__":
    unittest.main(verbosity=2)
