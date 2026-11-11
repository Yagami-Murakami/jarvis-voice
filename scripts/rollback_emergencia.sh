#!/usr/bin/env bash
# ==============================================================================
# SCRIPT DE ROLLBACK DE EMERGÊNCIA SEGURO
# Jarvis Voice Dual-Mic
# ==============================================================================
set -euo pipefail

EXPECTED_SHA="c51d58e6d3a19003eac55a827212e5e084d583fea43389ec0ea5466080ee2d54"
TARGET_FILE="/opt/jarvis-voice/jarvis-operacional.py"
BACKUP_FILE="/opt/jarvis-voice/jarvis-operacional.py.backup_original"
PATCH_FILE="/opt/jarvis-voice/patches/jarvis-operacional-dual.patch"

echo "=== INICIANDO PROCEDIMENTO DE ROLLBACK SEGURO ==="

# 1. Garantir que simulação permaneça ativa no systemd (NUNCA reativar WoL/SSH automaticamente)
echo "[1/4] Garantindo que simulação permaneça ativa no systemd..."
if [ -f /etc/systemd/system/jarvis-voice.service.d/producao.conf ]; then
    mv /etc/systemd/system/jarvis-voice.service.d/producao.conf /etc/systemd/system/jarvis-voice.service.d/producao.conf.disabled
fi
mkdir -p /etc/systemd/system/jarvis-voice.service.d
cat << 'EOF' > /etc/systemd/system/jarvis-voice.service.d/zz-simulacao-etapa1.conf
[Service]
Environment=JARVIS_WOL_REAL=0
Environment=JARVIS_SSH_REAL=0
EOF
systemctl daemon-reload
echo "Systemd configurado estritamente em MODO SIMULACAO."

# 2. Restaurar arquivo original a partir do backup ou reversão do patch
echo "[2/4] Restaurando código operacional original..."
cd /opt/jarvis-voice
if [ -f "$BACKUP_FILE" ]; then
    cp -p "$BACKUP_FILE" "$TARGET_FILE"
else
    patch -R -p1 < "$PATCH_FILE"
fi

# 3. Validar SHA-256 do arquivo restaurado
echo -n "[3/4] Validando integridade SHA-256 do arquivo restaurado... "
RESTORED_SHA=$(sha256sum "$TARGET_FILE" | awk '{print $1}')
if [ "$RESTORED_SHA" != "$EXPECTED_SHA" ]; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: Hash restaurado $RESTORED_SHA não confere com original $EXPECTED_SHA!"
    exit 1
fi
echo "OK ($RESTORED_SHA)"

# 4. Reiniciar serviço operacional com segurança
echo "[4/4] Reiniciando jarvis-voice.service..."
systemctl restart jarvis-voice.service
systemctl status jarvis-voice.service --no-pager | head -n 12

echo "=== ROLLBACK CONCLUÍDO COM SUCESSO. SISTEMA OPERACIONAL ORIGINAL ATIVO EM MODO SEGURO ==="
