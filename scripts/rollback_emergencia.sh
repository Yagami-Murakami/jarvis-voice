#!/usr/bin/env bash
# ==============================================================================
# SCRIPT DE ROLLBACK DE EMERGÊNCIA SEGURO (NUNCA REATIVA HARDWARE)
# Jarvis Voice Dual-Mic — Etapa 1
# ==============================================================================
set -euo pipefail

EXPECTED_SHA="c51d58e6d3a19003eac55a827212e5e084d583fea43389ec0ea5466080ee2d54"
TARGET_FILE="/opt/jarvis-voice/jarvis-operacional.py"
BACKUP_FILE="/opt/jarvis-voice/jarvis-operacional.py.backup_original"
EXTERNAL_BACKUP="/var/backups/jarvis/jarvis-operacional.py.backup_original"

echo "=== INICIANDO PROCEDIMENTO DE ROLLBACK SEGURO ==="

# 1. Validação prévia e estrita do arquivo de backup ANTES de qualquer alteração (Fail-Closed)
echo -n "[1/5] Validando integridade do backup original antes de restaurar... "
SOURCE_BACKUP=""
if [ -f "$BACKUP_FILE" ]; then
    SOURCE_BACKUP="$BACKUP_FILE"
elif [ -f "$EXTERNAL_BACKUP" ]; then
    SOURCE_BACKUP="$EXTERNAL_BACKUP"
else
    echo "FALHA!"
    echo "ERRO CRÍTICO: Nenhum arquivo de backup original encontrado em $BACKUP_FILE ou $EXTERNAL_BACKUP!"
    exit 1
fi

BACKUP_SHA=$(sha256sum "$SOURCE_BACKUP" | awk '{print $1}')
if [ "$BACKUP_SHA" != "$EXPECTED_SHA" ]; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: SHA-256 do backup $SOURCE_BACKUP divergente!"
    echo "  Esperado: $EXPECTED_SHA"
    echo "  Obtido:   $BACKUP_SHA"
    exit 1
fi
echo "OK ($BACKUP_SHA de $SOURCE_BACKUP)"

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

# 3. Restaurar arquivo operacional a partir do backup verificado
echo -n "[3/5] Restaurando arquivo operacional a partir do backup... "
cp -p "$SOURCE_BACKUP" "$TARGET_FILE"
RESTORED_SHA=$(sha256sum "$TARGET_FILE" | awk '{print $1}')
if [ "$RESTORED_SHA" != "$EXPECTED_SHA" ]; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: Hash do arquivo restaurado divergente de $EXPECTED_SHA!"
    exit 1
fi
echo "OK ($RESTORED_SHA)"

# 4. Reiniciar serviço operacional com segurança
echo -n "[4/5] Reiniciando jarvis-voice.service... "
systemctl restart jarvis-voice.service
sleep 1
if ! systemctl is-active --quiet jarvis-voice.service; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: jarvis-voice.service falhou ao reiniciar após rollback!"
    systemctl status jarvis-voice.service --no-pager | head -n 20
    exit 1
fi
echo "OK (Serviço ativo)"

# 5. Auditoria do ambiente em execução do novo processo
echo -n "[5/5] Auditando ambiente em execução do PID ativo... "
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
echo "OK (PID $RESTORED_PID estritamente em MODO SIMULACAO)"

echo ""
echo "=== ROLLBACK CONCLUÍDO COM SUCESSO. CÓDIGO ORIGINAL ATIVO EM MODO SEGURO ==="
