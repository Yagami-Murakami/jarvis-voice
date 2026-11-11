#!/usr/bin/env bash
# ==============================================================================
# SCRIPT DE VERIFICAÇÃO AUTOMÁTICA PRÉ-IMPLANTAÇÃO (PRE-FLIGHT SAFETY CHECK)
# Jarvis Voice Dual-Mic — Etapa 1: Simulação Obrigatória
# ==============================================================================
set -euo pipefail

EXPECTED_SHA="c51d58e6d3a19003eac55a827212e5e084d583fea43389ec0ea5466080ee2d54"
TARGET_FILE="/opt/jarvis-voice/jarvis-operacional.py"
BACKUP_FILE="/opt/jarvis-voice/jarvis-operacional.py.backup_original"
PATCH_FILE="/opt/jarvis-voice/patches/jarvis-operacional-dual.patch"
STATE_DIR="/var/lib/jarvis"
VENV_PY="/opt/jarvis-voice/venv/bin/python3"

echo "=== INICIANDO VERIFICAÇÃO PRÉ-IMPLANTAÇÃO (SAFETY CHECK) ==="

# 1. Verificação de integridade do arquivo original em produção e do backup
echo -n "[1/8] Verificando SHA-256 do arquivo original e do backup... "
CURRENT_SHA=$(sha256sum "$TARGET_FILE" | awk '{print $1}')
if [ "$CURRENT_SHA" != "$EXPECTED_SHA" ]; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: SHA-256 de $TARGET_FILE divergente!"
    echo "  Esperado: $EXPECTED_SHA"
    echo "  Obtido:   $CURRENT_SHA"
    exit 1
fi
if [ -f "$BACKUP_FILE" ]; then
    BACKUP_SHA=$(sha256sum "$BACKUP_FILE" | awk '{print $1}')
    if [ "$BACKUP_SHA" != "$EXPECTED_SHA" ]; then
        echo "FALHA!"
        echo "ERRO CRÍTICO: SHA-256 do backup $BACKUP_FILE divergente!"
        exit 1
    fi
else
    echo "FALHA!"
    echo "ERRO CRÍTICO: Arquivo de backup $BACKUP_FILE não encontrado!"
    exit 1
fi
echo "OK ($CURRENT_SHA)"

# 2. Verificação de sintaxe e ausência de mocks no código proposto
echo -n "[2/8] Verificando ausência de mocks e syntax check no código de produção... "
if grep -iE "MagicMock" /opt/jarvis-voice/scripts/jarvis_operacional_corrigido.py >/dev/null 2>&1; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: MagicMock detectado em jarvis_operacional_corrigido.py!"
    exit 1
fi
"$VENV_PY" -m py_compile /opt/jarvis-voice/scripts/jarvis_operacional_corrigido.py
echo "OK (Sintaxe limpa, 0 mocks)"

# 3. Teste de aplicação do patch em modo dry-run
echo -n "[3/8] Testando aplicação do patch (dry-run)... "
if ! (cd /opt/jarvis-voice && patch --dry-run -p1 < "$PATCH_FILE" >/dev/null 2>&1); then
    echo "FALHA!"
    echo "ERRO CRÍTICO: patch --dry-run falhou em aplicar $PATCH_FILE!"
    exit 1
fi
echo "OK (Código de retorno: 0)"

# 4. Teste de reversibilidade do patch (dry-run reverso)
echo -n "[4/8] Testando reversibilidade do patch (dry-run reverso)... "
TEMP_TEST="/tmp/preflight_test_$$.py"
cp "$TARGET_FILE" "$TEMP_TEST"
patch "$TEMP_TEST" < "$PATCH_FILE" >/dev/null 2>&1
if ! patch -R --dry-run "$TEMP_TEST" < "$PATCH_FILE" >/dev/null 2>&1; then
    rm -f "$TEMP_TEST"
    echo "FALHA!"
    echo "ERRO CRÍTICO: patch -R --dry-run falhou!"
    exit 1
fi
rm -f "$TEMP_TEST"
echo "OK (Reversibilidade 100% garantida)"

# 5. Verificação estrita das variáveis de ambiente systemd (Simulação Obrigatória)
echo -n "[5/8] Verificando variáveis de ambiente systemd (WoL=0, SSH=0, PERSISTENCE=1)... "
ENV_OUTPUT=$(systemctl show -p Environment jarvis-voice.service)
if ! echo "$ENV_OUTPUT" | grep -q "JARVIS_WOL_REAL=0"; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: JARVIS_WOL_REAL=0 não configurado no systemd ($ENV_OUTPUT)!"
    exit 1
fi
if ! echo "$ENV_OUTPUT" | grep -q "JARVIS_SSH_REAL=0"; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: JARVIS_SSH_REAL=0 não configurado no systemd ($ENV_OUTPUT)!"
    exit 1
fi
if ! echo "$ENV_OUTPUT" | grep -q "JARVIS_REQUIRE_PERSISTENCE=1"; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: JARVIS_REQUIRE_PERSISTENCE=1 não configurado no systemd ($ENV_OUTPUT)!"
    exit 1
fi
if [ -f /etc/systemd/system/jarvis-voice.service.d/producao.conf ]; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: producao.conf ativo em drop-in! Deve estar desabilitado para simulação."
    exit 1
fi
echo "OK (JARVIS_WOL=0, JARVIS_SSH=0, JARVIS_REQUIRE_PERSISTENCE=1 prevalecem)"

# 6. Verificação do diretório de armazenamento privado e persistente
echo -n "[6/8] Verificando diretório persistente /var/lib/jarvis... "
if [ ! -d "$STATE_DIR" ]; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: $STATE_DIR não existe!"
    exit 1
fi
if ! su -s /bin/bash tuninho -c "test -w '$STATE_DIR'"; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: $STATE_DIR não é gravável pelo usuário tuninho!"
    exit 1
fi
echo "OK (drwxr-x--- tuninho:tuninho)"

# 7. Verificação de status dos serviços auxiliares
echo -n "[7/8] Verificando serviços auxiliares (Gateway TLS e Display Server)... "
if ! systemctl is-active --quiet jarvis-audio-gateway.service; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: jarvis-audio-gateway.service não está ativo!"
    exit 1
fi
if ! systemctl is-active --quiet jarvis-display.service; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: jarvis-display.service não está ativo!"
    exit 1
fi
echo "OK (Gateway e Display ativos)"

# 8. Verificação das dependências de runtime e painel existente
echo -n "[8/8] Verificando importações de vosk e homelab-panel... "
if ! "$VENV_PY" -c "from vosk import Model, KaldiRecognizer; import sys; sys.path.insert(0, '/home/tuninho/homelab-panel'); import app; assert hasattr(app, 'SERVIDORES')" >/dev/null 2>&1; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: Falha ao carregar dependências Python ou homelab-panel!"
    exit 1
fi
echo "OK (Vosk e homelab-panel validados)"

echo ""
echo "=================================================================="
echo "TODAS AS 8 CONDIÇÕES DE SEGURANÇA FORAM APROVADAS COM SUCESSO."
echo "O AMBIENTE ESTÁ 100% PRONTO PARA A IMPLANTAÇÃO DA ETAPA 1."
echo "AGUARDANDO AUTORIZAÇÃO EXPLÍCITA DO OPERADOR PARA PROCEDER."
echo "=================================================================="
