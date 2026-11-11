#!/usr/bin/env bash
# ==============================================================================
# SCRIPT DE ROLLBACK ESPECÍFICO — JARVIS VOICE DUAL-MIC (ETAPA 1: SIMULAÇÃO)
# Permite reverter para a versão anterior em simulação (v1: f6c72731...)
# ou para o backup original de fábrica (original: c51d58e6...).
# NUNCA REATIVA WAKE-ON-LAN OU SSH REAIS.
# ==============================================================================
set -euo pipefail

TARGET_VERSION="${1:-v1}"

EXPECTED_ORIGINAL_SHA="c51d58e6d3a19003eac55a827212e5e084d583fea43389ec0ea5466080ee2d54"
EXPECTED_V1_SHA="f6c7273116aea4c4b34c0c6007d335b7925356ecb174e7066781716f2f604ed8"

TARGET_FILE="/opt/jarvis-voice/jarvis-operacional.py"
BACKUP_ORIGINAL="/opt/jarvis-voice/jarvis-operacional.py.backup_original"
BACKUP_ORIGINAL_EXT="/var/backups/jarvis/jarvis-operacional.py.backup_original"
BACKUP_V1="/opt/jarvis-voice/jarvis-operacional.py.backup_simulacao_v1"
BACKUP_V1_EXT="/var/backups/jarvis/jarvis-operacional.py.backup_simulacao_v1"

echo "=== INICIANDO PROCEDIMENTO DE ROLLBACK SEGURO (ALVO: $TARGET_VERSION) ==="

SOURCE_BACKUP=""
EXPECTED_RESTORE_SHA=""

if [ "$TARGET_VERSION" = "v1" ]; then
    echo "[INFO] Revertendo para a versão anterior de simulação (v1, pre-latência)..."
    if [ -f "$BACKUP_V1" ]; then
        SOURCE_BACKUP="$BACKUP_V1"
    elif [ -f "$BACKUP_V1_EXT" ]; then
        SOURCE_BACKUP="$BACKUP_V1_EXT"
    else
        echo "FALHA!"
        echo "ERRO CRÍTICO: Backup da versão v1 inexistente em $BACKUP_V1 e $BACKUP_V1_EXT!"
        exit 1
    fi
    EXPECTED_RESTORE_SHA="$EXPECTED_V1_SHA"
elif [ "$TARGET_VERSION" = "original" ]; then
    echo "[INFO] Revertendo para a versão original de fábrica (sem dual-mic)..."
    if [ -f "$BACKUP_ORIGINAL" ]; then
        SOURCE_BACKUP="$BACKUP_ORIGINAL"
    elif [ -f "$BACKUP_ORIGINAL_EXT" ]; then
        SOURCE_BACKUP="$BACKUP_ORIGINAL_EXT"
    else
        echo "FALHA!"
        echo "ERRO CRÍTICO: Backup original inexistente em $BACKUP_ORIGINAL e $BACKUP_ORIGINAL_EXT!"
        exit 1
    fi
    EXPECTED_RESTORE_SHA="$EXPECTED_ORIGINAL_SHA"
else
    echo "FALHA!"
    echo "ERRO: Versão de rollback desconhecida: $TARGET_VERSION. Use 'v1' ou 'original'."
    exit 1
fi

# 1. Validação prévia de hash ANTES de copiar (Fail-Closed)
echo -n "[1/5] Validando integridade do backup $SOURCE_BACKUP... "
SOURCE_SHA=$(sha256sum "$SOURCE_BACKUP" | awk '{print $1}')
if [ "$SOURCE_SHA" != "$EXPECTED_RESTORE_SHA" ]; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: SHA-256 do backup divergente!"
    echo "  Esperado: $EXPECTED_RESTORE_SHA"
    echo "  Obtido:   $SOURCE_SHA"
    exit 1
fi
echo "OK ($SOURCE_SHA)"

# 2. Garantir que simulação permaneça estritamente ativa no systemd (NUNCA reativar WoL/SSH)
echo -n "[2/5] Garantindo que simulação permaneça ativa no systemd... "
if [ -f /etc/systemd/system/jarvis-voice.service.d/producao.conf ]; then
    mv /etc/systemd/system/jarvis-voice.service.d/producao.conf /etc/systemd/system/jarvis-voice.service.d/producao.conf.disabled
fi
mkdir -p /etc/systemd/system/jarvis-voice.service.d
cat << 'DROPIN' > /etc/systemd/system/jarvis-voice.service.d/zz-simulacao-etapa1.conf
[Service]
Environment=JARVIS_WOL_REAL=0
Environment=JARVIS_SSH_REAL=0
Environment=JARVIS_REQUIRE_PERSISTENCE=1
RuntimeDirectory=jarvis
RuntimeDirectoryMode=0750
DROPIN
systemctl daemon-reload
echo "OK (WoL=0, SSH=0, REQUIRE_PERSISTENCE=1)"

# 3. Restaurar arquivo operacional
echo -n "[3/5] Restaurando $TARGET_FILE a partir de $SOURCE_BACKUP... "
install -m 0755 -o tuninho -g tuninho "$SOURCE_BACKUP" "$TARGET_FILE"
RESTORED_SHA=$(sha256sum "$TARGET_FILE" | awk '{print $1}')
if [ "$RESTORED_SHA" != "$EXPECTED_RESTORE_SHA" ]; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: Hash do arquivo restaurado divergente de $EXPECTED_RESTORE_SHA!"
    exit 1
fi
echo "OK ($RESTORED_SHA)"

# 4. Reiniciar serviços operacionais
echo -n "[4/5] Reiniciando jarvis-voice.service... "
systemctl restart jarvis-voice.service
if [ "$TARGET_VERSION" = "v1" ]; then
    systemctl restart jarvis-audio-gateway.service
fi
sleep 2

if ! systemctl is-active --quiet jarvis-voice.service; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: jarvis-voice.service falhou ao reiniciar após rollback!"
    systemctl status jarvis-voice.service --no-pager | head -n 20
    exit 1
fi
echo "OK (Serviço ativo)"

# 5. Auditoria de memória do processo
echo -n "[5/5] Auditando memória física do PID ativo... "
RESTORED_PID=$(systemctl show -p MainPID --value jarvis-voice.service)
ENV_RUNNING=$(strings "/proc/$RESTORED_PID/environ")

if ! echo "$ENV_RUNNING" | grep -q "JARVIS_WOL_REAL=0"; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: JARVIS_WOL_REAL=0 ausente na memória do processo $RESTORED_PID!"
    exit 1
fi
if ! echo "$ENV_RUNNING" | grep -q "JARVIS_SSH_REAL=0"; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: JARVIS_SSH_REAL=0 ausente na memória do processo $RESTORED_PID!"
    exit 1
fi
if echo "$ENV_RUNNING" | grep -q "JARVIS_WOL_REAL=1" || echo "$ENV_RUNNING" | grep -q "JARVIS_SSH_REAL=1"; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: Flag real detectada no processo $RESTORED_PID!"
    exit 1
fi
echo "OK (PID $RESTORED_PID estritamente em MODO SIMULAÇÃO)"

echo ""
echo "=== ROLLBACK PARA '$TARGET_VERSION' CONCLUÍDO COM SUCESSO EM MODO SEGURO ==="
