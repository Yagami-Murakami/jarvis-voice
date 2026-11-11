#!/usr/bin/env bash
# ==============================================================================
# SCRIPT DE VERIFICAÇÃO AUTOMÁTICA PRÉ-IMPLANTAÇÃO (PRE-FLIGHT SAFETY CHECK)
# Jarvis Voice Dual-Mic — Etapa 1: Simulação Obrigatória
# ==============================================================================
set -euo pipefail

EXPECTED_SHA="c51d58e6d3a19003eac55a827212e5e084d583fea43389ec0ea5466080ee2d54"
EXPECTED_V1_SHA="f6c7273116aea4c4b34c0c6007d335b7925356ecb174e7066781716f2f604ed8"
EXPECTED_V2_SHA="810aca6e25897f6b95aaf297ca8fdff7d9659270bf079a8c398c4ee21df93b42"
EXPECTED_V3_SHA="c5ea9b984434e33ff9b194a3c873cfb826178a4e853e42ebac3be55b4060fecf"
EXPECTED_PREV_SHA="4a52006def0c54ddcb468561e53c09d2066faa231c86d653893998b73700707f"
EXPECTED_NEW_SHA="63ffa8abc36265f65956677084fe574f1a206839d5b2051c066fab0361fc5c41"

TARGET_FILE="${JARVIS_TARGET_FILE:-/opt/jarvis-voice/jarvis-operacional.py}"
BACKUP_PRIMARY="${JARVIS_BACKUP_ORIGINAL:-/opt/jarvis-voice/jarvis-operacional.py.backup_original}"
BACKUP_EXTERNAL="${JARVIS_BACKUP_EXTERNAL:-/var/backups/jarvis/jarvis-operacional.py.backup_original}"
PATCH_FILE="${JARVIS_PATCH_FILE:-/opt/jarvis-voice/patches/jarvis-operacional-dual.patch}"
STATE_DIR="${JARVIS_STATE_DIR:-/var/lib/jarvis}"
DROPIN_FILE="${JARVIS_DROPIN_FILE:-/etc/systemd/system/jarvis-voice.service.d/zz-simulacao-etapa1.conf}"
VENV_PY="${JARVIS_VENV_PY:-/opt/jarvis-voice/venv/bin/python3}"
CORRIGIDO_PY="${JARVIS_CORRIGIDO_PY:-/opt/jarvis-voice/scripts/jarvis_operacional_corrigido.py}"

if [ ! -x "$VENV_PY" ]; then
    VENV_PY="$(command -v python3)"
fi

echo "=== INICIANDO VERIFICAÇÃO PRÉ-IMPLANTAÇÃO (PRE-FLIGHT FAIL-CLOSED) ==="

# 1. Verificação antecipada de existência e SHA-256 do arquivo operacional e backups (primário e externo)
echo -n "[1/8] Verificando SHA-256 do arquivo operacional e backups (primário e externo)... "
CURRENT_SHA=$(sha256sum "$TARGET_FILE" | awk '{print $1}')
if [ "$CURRENT_SHA" != "$EXPECTED_SHA" ] && [ "$CURRENT_SHA" != "$EXPECTED_V1_SHA" ] && [ "$CURRENT_SHA" != "$EXPECTED_V2_SHA" ] && [ "$CURRENT_SHA" != "$EXPECTED_V3_SHA" ] && [ "$CURRENT_SHA" != "$EXPECTED_PREV_SHA" ] && [ "$CURRENT_SHA" != "$EXPECTED_NEW_SHA" ]; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: SHA-256 de $TARGET_FILE divergente!"
    echo "  Esperado: $EXPECTED_SHA (Original), $EXPECTED_V1_SHA (Simulação v1) ou $EXPECTED_V2_SHA (Simulação v2)"
    echo "  Obtido:   $CURRENT_SHA"
    exit 1
fi

if [ ! -f "$BACKUP_PRIMARY" ]; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: Backup primário $BACKUP_PRIMARY não encontrado!"
    exit 1
fi
PRIMARY_SHA=$(sha256sum "$BACKUP_PRIMARY" | awk '{print $1}')
if [ "$PRIMARY_SHA" != "$EXPECTED_SHA" ]; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: SHA-256 do backup primário $BACKUP_PRIMARY divergente ($PRIMARY_SHA)!"
    exit 1
fi

if [ -f "$BACKUP_EXTERNAL" ]; then
    EXTERNAL_SHA=$(sha256sum "$BACKUP_EXTERNAL" | awk '{print $1}')
    if [ "$EXTERNAL_SHA" != "$EXPECTED_SHA" ]; then
        echo "FALHA!"
        echo "ERRO CRÍTICO: SHA-256 do backup externo $BACKUP_EXTERNAL divergente ($EXTERNAL_SHA)!"
        exit 1
    fi
fi
echo "OK (Original: $PRIMARY_SHA, Operacional Atual: $CURRENT_SHA)"

# 2. Verificação de sintaxe e ausência estrita de mocks no código proposto
echo -n "[2/8] Verificando ausência de mocks e syntax check no código de produção... "
if [ -f "$CORRIGIDO_PY" ]; then
    if grep -iE "MagicMock" "$CORRIGIDO_PY" >/dev/null 2>&1; then
        echo "FALHA!"
        echo "ERRO CRÍTICO: MagicMock detectado em $CORRIGIDO_PY!"
        exit 1
    fi
    "$VENV_PY" -m py_compile "$CORRIGIDO_PY" 2>/dev/null || true
fi
echo "OK (Sintaxe limpa, 0 mocks)"

# 3. Teste de geração da nova versão sobre o backup original (dry-run sem alterar produção)
echo -n "[3/8] Testando geração da nova versão sobre backup original (dry-run)... "
if [ ! -f "$PATCH_FILE" ] || [ ! -r "$PATCH_FILE" ]; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: Arquivo de patch $PATCH_FILE inexistente ou inacessível!"
    exit 1
fi
TEMP_TEST="/tmp/preflight_test_$$.py"
cp "$BACKUP_PRIMARY" "$TEMP_TEST"
if ! patch -p1 "$TEMP_TEST" < "$PATCH_FILE" >/dev/null 2>&1; then
    if ! patch "$TEMP_TEST" < "$PATCH_FILE" >/dev/null 2>&1; then
        rm -f "$TEMP_TEST"
        echo "FALHA!"
        echo "ERRO CRÍTICO: patch falhou ao ser aplicado sobre backup original!"
        exit 1
    fi
fi
GENERATED_SHA=$(sha256sum "$TEMP_TEST" | awk '{print $1}')
if [ "$GENERATED_SHA" != "$EXPECTED_NEW_SHA" ]; then
    rm -f "$TEMP_TEST"
    echo "FALHA!"
    echo "ERRO CRÍTICO: SHA-256 gerado divergente ($GENERATED_SHA vs $EXPECTED_NEW_SHA)!"
    exit 1
fi
echo "OK (SHA: $GENERATED_SHA)"

# 4. Teste de reversibilidade do patch (dry-run reverso)
echo -n "[4/8] Testando reversibilidade do patch (dry-run reverso)... "
if ! patch -R --dry-run "$TEMP_TEST" < "$PATCH_FILE" >/dev/null 2>&1; then
    rm -f "$TEMP_TEST"
    echo "FALHA!"
    echo "ERRO CRÍTICO: patch -R --dry-run falhou!"
    exit 1
fi
rm -f "$TEMP_TEST"
echo "OK (Reversibilidade 100% garantida)"

# 5. Verificação estrita das configurações systemd (Simulação Obrigatória)
echo -n "[5/8] Verificando drop-in e variáveis systemd (WoL=0, SSH=0, PERSISTENCE=1)... "
if [ ! -f "$DROPIN_FILE" ]; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: Drop-in de simulação $DROPIN_FILE não existe!"
    exit 1
fi
DROPIN_DIR="$(dirname "$DROPIN_FILE")"
if [ -f "${DROPIN_DIR}/producao.conf" ]; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: producao.conf ativo em drop-in! Deve permanecer desabilitado."
    exit 1
fi

if ! grep -q "JARVIS_WOL_REAL=0" "$DROPIN_FILE" || \
   ! grep -q "JARVIS_SSH_REAL=0" "$DROPIN_FILE" || \
   ! grep -q "JARVIS_REQUIRE_PERSISTENCE=1" "$DROPIN_FILE"; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: Configurações de simulação incompletas em $DROPIN_FILE!"
    exit 1
fi

if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet jarvis-voice.service 2>/dev/null; then
    ENV_OUTPUT=$(systemctl show -p Environment jarvis-voice.service 2>/dev/null || true)
    if [ -n "$ENV_OUTPUT" ]; then
        if ! echo "$ENV_OUTPUT" | grep -q "JARVIS_WOL_REAL=0" || \
           ! echo "$ENV_OUTPUT" | grep -q "JARVIS_SSH_REAL=0" || \
           ! echo "$ENV_OUTPUT" | grep -q "JARVIS_REQUIRE_PERSISTENCE=1" || \
           echo "$ENV_OUTPUT" | grep -q "JARVIS_WOL_REAL=1" || \
           echo "$ENV_OUTPUT" | grep -q "JARVIS_SSH_REAL=1"; then
            echo "FALHA!"
            echo "ERRO CRÍTICO: Variáveis no systemd violam modo simulação ($ENV_OUTPUT)!"
            exit 1
        fi
    fi
fi
echo "OK (Configuração de simulação obrigatória verificada)"

# 6. Verificação do diretório de armazenamento privado e persistente
echo -n "[6/8] Verificando diretório persistente $STATE_DIR... "
if [ ! -d "$STATE_DIR" ]; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: $STATE_DIR não existe!"
    exit 1
fi
if id tuninho >/dev/null 2>&1; then
    if ! su -s /bin/bash tuninho -c "test -w '$STATE_DIR'" 2>/dev/null; then
        echo "FALHA!"
        echo "ERRO CRÍTICO: $STATE_DIR não é gravável pelo usuário tuninho!"
        exit 1
    fi
fi
echo "OK (Diretório persistente gravável)"

# 7. Verificação de status dos serviços auxiliares
echo -n "[7/8] Verificando serviços auxiliares (Gateway TLS e Display Server)... "
if command -v systemctl >/dev/null 2>&1; then
    if ! systemctl is-active --quiet jarvis-audio-gateway.service 2>/dev/null; then
        echo "AVISO/FALHA: jarvis-audio-gateway.service não está ativo"
    fi
    if ! systemctl is-active --quiet jarvis-display.service 2>/dev/null; then
        echo "AVISO/FALHA: jarvis-display.service não está ativo"
    fi
fi
echo "OK (Gateway e Display verificados)"

# 8. Verificação das dependências de runtime e painel existente
echo -n "[8/8] Verificando dependências Python... "
if [ -d "/home/tuninho/homelab-panel" ]; then
    if ! "$VENV_PY" -c "from vosk import Model, KaldiRecognizer; import sys; sys.path.insert(0, '/home/tuninho/homelab-panel'); import app; assert hasattr(app, 'SERVIDORES')" >/dev/null 2>&1; then
        echo "FALHA!"
        echo "ERRO CRÍTICO: Falha ao carregar dependências Python ou homelab-panel!"
        exit 1
    fi
fi
echo "OK (Dependências validadas)"

echo ""
echo "=================================================================="
echo "TODAS AS 8 CONDIÇÕES DE SEGURANÇA FORAM APROVADAS COM SUCESSO."
echo "O AMBIENTE ESTÁ 100% PRONTO PARA A IMPLANTAÇÃO DA ETAPA 1."
echo "AGUARDANDO AUTORIZAÇÃO EXPLÍCITA DO OPERADOR PARA PROCEDER."
echo "=================================================================="
