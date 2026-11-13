#!/bin/bash
# ==============================================================================
# Script de Rollback Imediato do Gateway de Áudio para Versão Anterior (LXC 110)
#
# Restaura o código de audio_gateway.py e o arquivo /etc/jarvis/audio-gateway.env
# a partir do manifesto gerado pelo script de implantação.
# ==============================================================================
set -euo pipefail

GATEWAY_DIR="/opt/jarvis-voice/gateway"
ENV_FILE="/etc/jarvis/audio-gateway.env"
SERVICE_NAME="jarvis-audio-gateway.service"
BACKUP_MANIFEST="${GATEWAY_DIR}/.last_backup_manifest"

echo "================================================================="
echo "   ROLLBACK DO JARVIS AUDIO GATEWAY (LXC 110)                    "
echo "================================================================="

if [ "$(id -u)" -ne 0 ]; then
    echo "[ERRO] Este script deve ser executado como root no LXC 110." >&2
    exit 1
fi

if [ ! -f "$BACKUP_MANIFEST" ]; then
    echo "[ERRO] Manifesto de backup não encontrado em: $BACKUP_MANIFEST" >&2
    echo "Procurando backups manuais em $GATEWAY_DIR..."
    ls -l "${GATEWAY_DIR}"/audio_gateway.py.bak.* 2>/dev/null || true
    exit 1
fi

# Carrega variáveis do manifesto
# shellcheck disable=SC1090
source "$BACKUP_MANIFEST"

echo "Restaurando a partir do backup criado em: ${BACKUP_TIMESTAMP:-desconhecido}"

if [ -f "${BACKUP_CODE:-}" ]; then
    cp -p "$BACKUP_CODE" "${GATEWAY_DIR}/audio_gateway.py"
    chmod 0755 "${GATEWAY_DIR}/audio_gateway.py"
    echo "  -> Código restaurado de: $BACKUP_CODE"
else
    echo "[ERRO] Arquivo de backup do código não encontrado: ${BACKUP_CODE:-vazio}" >&2
    exit 1
fi

if [ -f "${BACKUP_ENV:-}" ]; then
    cp -p "$BACKUP_ENV" "$ENV_FILE"
    chmod 0600 "$ENV_FILE"
    echo "  -> Variáveis de ambiente restauradas de: $BACKUP_ENV"
else
    echo "[AVISO] Arquivo de backup de env não encontrado: ${BACKUP_ENV:-vazio}. Mantendo atual."
fi

echo "Reiniciando serviço $SERVICE_NAME..."
systemctl restart "$SERVICE_NAME"
sleep 2

if systemctl is-active --quiet "$SERVICE_NAME"; then
    echo "================================================================="
    echo "  ROLLBACK EXECUTADO COM SUCESSO! SERVIÇO EM OPERAÇÃO NORMAL.    "
    echo "================================================================="
else
    echo "[ERRO] Falha ao reiniciar serviço após rollback. Verifique journalctl -u $SERVICE_NAME" >&2
    exit 1
fi
