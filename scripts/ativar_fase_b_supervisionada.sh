#!/bin/bash
set -euo pipefail

# ========================================================================
# JARVIS VOICE — ATIVAÇÃO DA FASE B SUPERVISIONADA (REAL WOL: FUSION ONLY)
# Regra de Segurança Crítica:
# - JARVIS_WOL_REAL=1
# - JARVIS_SSH_REAL=0 (desligamento real permanece estritamente bloqueado)
# - JARVIS_ALLOWED_REAL_TARGETS="fusion" (Ananuke estritamente em simulação)
# - JARVIS_REQUIRE_PERSISTENCE=1
# ========================================================================

EXPECTED_SHA="569799f94cd476f0cdac20dbbaee20e4fe7dce87672af2f666278635f18c05d8"
OPERACIONAL="/opt/jarvis-voice/jarvis-operacional.py"
DROPIN_DIR="/etc/systemd/system/jarvis-voice.service.d"
ETAPA1_CONF="${DROPIN_DIR}/zz-simulacao-etapa1.conf"
ETAPA1_CONF_DIS="${DROPIN_DIR}/zz-simulacao-etapa1.conf.disabled"
FASE_B_CONF="${DROPIN_DIR}/zz-fase-b-supervisionada.conf"

echo "=== INICIANDO ATIVAÇÃO DA FASE B (WoL REAL SUPERVISIONADO PARA FUSION) ==="

# 1. Checagem de privilégios
if [[ "$(id -u)" -ne 0 ]]; then
    echo "ERRO: Este script deve ser executado como root." >&2
    exit 1
fi

# 2. Verificação de integridade do arquivo operacional
if [[ ! -f "${OPERACIONAL}" ]]; then
    echo "ERRO: Arquivo operacional ${OPERACIONAL} inexistente." >&2
    exit 1
fi

CURRENT_SHA=$(sha256sum "${OPERACIONAL}" | awk '{print $1}')
if [[ "${CURRENT_SHA}" != "${EXPECTED_SHA}" ]]; then
    echo "ERRO: SHA-256 do arquivo operacional (${CURRENT_SHA}) difere do esperado (${EXPECTED_SHA})." >&2
    exit 1
fi
echo "[1/5] SHA-256 do arquivo operacional validado: ${CURRENT_SHA}"

# 3. Gerenciamento de drop-ins systemd
mkdir -p "${DROPIN_DIR}"
if [[ -f "${ETAPA1_CONF}" ]]; then
    echo "[2/5] Desativando drop-in de simulação da Etapa 1..."
    mv "${ETAPA1_CONF}" "${ETAPA1_CONF_DIS}"
fi

echo "[3/5] Instalando drop-in da Fase B supervisionada..."
cat << 'EOF' > "${FASE_B_CONF}"
[Service]
Environment=JARVIS_WOL_REAL=1
Environment=JARVIS_SSH_REAL=0
Environment=JARVIS_ALLOWED_REAL_TARGETS=fusion
Environment=JARVIS_REQUIRE_PERSISTENCE=1
RuntimeDirectory=jarvis
RuntimeDirectoryMode=0750
EOF
chmod 0644 "${FASE_B_CONF}"

# 4. Recarregar systemd e reiniciar jarvis-voice.service
echo "[4/5] Recarregando systemd e reiniciando jarvis-voice.service..."
systemctl daemon-reload
systemctl restart jarvis-voice.service

sleep 2

# 5. Auditoria de processo e variáveis de ambiente
PID=$(systemctl show -p MainPID --value jarvis-voice.service)
if [[ -z "${PID}" || "${PID}" -le 0 ]]; then
    echo "ERRO: jarvis-voice.service não está em execução." >&2
    exit 1
fi

ENV_DATA=$(tr '\0' '\n' < "/proc/${PID}/environ")

echo "${ENV_DATA}" | grep -q "^JARVIS_WOL_REAL=1$" || {
    echo "ERRO: JARVIS_WOL_REAL=1 não encontrado no processo ativo." >&2
    exit 1
}

echo "${ENV_DATA}" | grep -q "^JARVIS_SSH_REAL=0$" || {
    echo "ERRO: JARVIS_SSH_REAL=0 não encontrado no processo ativo." >&2
    exit 1
}

echo "${ENV_DATA}" | grep -q "^JARVIS_ALLOWED_REAL_TARGETS=fusion$" || {
    echo "ERRO: JARVIS_ALLOWED_REAL_TARGETS=fusion não encontrado no processo ativo." >&2
    exit 1
}

echo "${ENV_DATA}" | grep -q "^JARVIS_REQUIRE_PERSISTENCE=1$" || {
    echo "ERRO: JARVIS_REQUIRE_PERSISTENCE=1 não encontrado no processo ativo." >&2
    exit 1
}

echo "[5/5] Variáveis de ambiente auditadas com sucesso no PID ${PID}."
echo ""
echo "=========================================================================="
echo "FASE B ATIVADA COM SUCESSO!"
echo "- WoL Real: ATIVO apenas para 'fusion'"
echo "- SSH Real: BLOQUEADO (JARVIS_SSH_REAL=0)"
echo "- Ananuke (192.168.0.131): 100% PROTEGIDO EM SIMULAÇÃO"
echo "=========================================================================="
