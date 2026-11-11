#!/bin/bash
set -euo pipefail

# ========================================================================
# JARVIS VOICE — DESATIVAÇÃO DA FASE B SUPERVISIONADA (RETORNO À ETAPA 1)
# ========================================================================

DROPIN_DIR="/etc/systemd/system/jarvis-voice.service.d"
ETAPA1_CONF="${DROPIN_DIR}/zz-simulacao-etapa1.conf"
ETAPA1_CONF_DIS="${DROPIN_DIR}/zz-simulacao-etapa1.conf.disabled"
FASE_B_CONF="${DROPIN_DIR}/zz-fase-b-supervisionada.conf"

echo "=== RETORNANDO JARVIS-VOICE AO MODO SIMULAÇÃO TOTAL (ETAPA 1) ==="

if [[ "$(id -u)" -ne 0 ]]; then
    echo "ERRO: Este script deve ser executado como root." >&2
    exit 1
fi

rm -f "${FASE_B_CONF}"

if [[ -f "${ETAPA1_CONF_DIS}" ]]; then
    mv "${ETAPA1_CONF_DIS}" "${ETAPA1_CONF}"
else
    cat << 'EOF' > "${ETAPA1_CONF}"
[Service]
Environment=JARVIS_WOL_REAL=0
Environment=JARVIS_SSH_REAL=0
Environment=JARVIS_REQUIRE_PERSISTENCE=1
RuntimeDirectory=jarvis
RuntimeDirectoryMode=0750
EOF
fi
chmod 0644 "${ETAPA1_CONF}"

systemctl daemon-reload
systemctl restart jarvis-voice.service

sleep 2

PID=$(systemctl show -p MainPID --value jarvis-voice.service)
ENV_DATA=$(tr '\0' '\n' < "/proc/${PID}/environ")

echo "${ENV_DATA}" | grep -q "^JARVIS_WOL_REAL=0$" || {
    echo "ERRO: JARVIS_WOL_REAL=0 não encontrado no processo ativo." >&2
    exit 1
}

echo "${ENV_DATA}" | grep -q "^JARVIS_SSH_REAL=0$" || {
    echo "ERRO: JARVIS_SSH_REAL=0 não encontrado no processo ativo." >&2
    exit 1
}

echo "=========================================================================="
echo "RETORNO À SIMULAÇÃO TOTAL CONCLUÍDO COM SUCESSO (PID ${PID})."
echo "=========================================================================="
