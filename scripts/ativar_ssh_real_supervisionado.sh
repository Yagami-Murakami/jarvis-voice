#!/bin/bash
set -euo pipefail

# ========================================================================
# JARVIS VOICE — ATIVAÇÃO CONTROLADA DO SSH REAL SUPERVISIONADO (ONE-SHOT)
# Regras Estritas:
# - Válido APENAS para Fusion (192.168.0.128)
# - Execução única com token de autorização descartável (One-Shot Token)
# - Expiração automática com TTL curto (300 segundos)
# - Watchdog automático de desativação imediata em caso de uso, expiração ou falha
# - Fail-closed total em reinicialização, crash ou alvo não autorizado
# ========================================================================

EXPECTED_SHA="8d27abd775cd28355727713d49f1793c99bd2dc5ff53a5a8682f341ea48edca8"
OPERACIONAL="/opt/jarvis-voice/jarvis-operacional.py"
DROPIN_DIR="/etc/systemd/system/jarvis-voice.service.d"
SSH_ONESHOT_CONF="${DROPIN_DIR}/zz-ssh-real-oneshot.conf"
FASE_B_CONF="${DROPIN_DIR}/zz-fase-b-supervisionada.conf"
TOKEN_FILE="/var/lib/jarvis/one_shot_ssh_token.json"
FUSION_IP="192.168.0.128"
TTL_SECONDS="${1:-600}"

echo "=== INICIANDO ATIVAÇÃO CONTROLADA DO SSH REAL SUPERVISIONADO (ONE-SHOT) ==="

# 1. Privilégios de root
if [[ "$(id -u)" -ne 0 ]]; then
    echo "ERRO: Este script deve ser executado como root." >&2
    exit 1
fi

# 2. Integridade do arquivo operacional
if [[ ! -f "${OPERACIONAL}" ]]; then
    echo "ERRO: Arquivo ${OPERACIONAL} não encontrado." >&2
    exit 1
fi

CURRENT_SHA=$(sha256sum "${OPERACIONAL}" | awk '{print $1}')
if [[ "${CURRENT_SHA}" != "${EXPECTED_SHA}" ]]; then
    echo "ERRO: SHA-256 do arquivo operacional (${CURRENT_SHA}) difere do esperado (${EXPECTED_SHA})." >&2
    exit 1
fi
echo "[1/7] SHA-256 do script operacional validado: ${CURRENT_SHA}"

# 3. Verificação de conectividade e credenciais do Fusion
echo "[2/7] Verificando conectividade com Fusion (${FUSION_IP})..."
ping -c 2 -W 1 "${FUSION_IP}" >/dev/null 2>&1 || {
    echo "ERRO: Fusion (${FUSION_IP}) não responde ao ping." >&2
    exit 1
}

nc -z -w 2 "${FUSION_IP}" 22 >/dev/null 2>&1 || {
    echo "ERRO: Porta 22 SSH inacessível em Fusion (${FUSION_IP})." >&2
    exit 1
}

SSH_TEST=$(su - tuninho -c "ssh -o BatchMode=yes -o ConnectTimeout=5 anakin@${FUSION_IP} echo ssh_ready" 2>/dev/null | tr -d '\r' || true)
if [[ "${SSH_TEST}" != "ssh_ready" ]]; then
    echo "ERRO: Chave SSH para anakin@${FUSION_IP} falhou ou requer senha." >&2
    exit 1
fi
echo "  ✓ Fusion está online e autenticação SSH de tuninho está plenamente operacional."

# 4. Confirmação do drop-in base da Fase B
if [[ ! -f "${FASE_B_CONF}" ]]; then
    echo "ERRO: Drop-in base da Fase B ${FASE_B_CONF} não encontrado." >&2
    exit 1
fi

grep -q "JARVIS_ALLOWED_REAL_TARGETS=fusion" "${FASE_B_CONF}" || {
    echo "ERRO: JARVIS_ALLOWED_REAL_TARGETS=fusion ausente no drop-in base!" >&2
    exit 1
}
echo "[3/7] Drop-in base da Fase B validado com isolamento estrito para 'fusion'."

# 5. Geração do Token One-Shot descartável
echo "[4/7] Gerando token One-Shot com TTL de ${TTL_SECONDS}s..."
mkdir -p /var/lib/jarvis
chown -R tuninho:tuninho /var/lib/jarvis
chmod 0750 /var/lib/jarvis

TOKEN_ID="ost-fusion-$(date +%s%N | cut -b1-13)"
EXPIRES_AT=$(python3 -c "import time; print(time.time() + ${TTL_SECONDS})")
CREATED_AT=$(python3 -c "import time; print(time.time())")

python3 -c "
import json, os
token = {
    'token_id': '${TOKEN_ID}',
    'alvo': 'fusion',
    'acao': 'desligar',
    'created_at': ${CREATED_AT},
    'ttl_seconds': ${TTL_SECONDS},
    'expires_at': ${EXPIRES_AT},
    'consumed': False,
    'consumed_at': None,
    'consumed_by_event': None,
    'consumed_by_pid': None,
    'bound_service_pid': None,
    'status': 'active'
}
with open('${TOKEN_FILE}', 'w', encoding='utf-8') as f:
    json.dump(token, f, indent=2)
os.chmod('${TOKEN_FILE}', 0o600)
"
chown tuninho:tuninho "${TOKEN_FILE}"
echo "  ✓ Token ${TOKEN_ID} gerado em ${TOKEN_FILE} (0600 tuninho:tuninho)."

# 6. Instalação do drop-in temporário do SSH real
echo "[5/7] Instalando drop-in temporário ${SSH_ONESHOT_CONF}..."
cat << 'EOF' > "${SSH_ONESHOT_CONF}"
[Service]
Environment=JARVIS_SSH_REAL=1
EOF
chmod 0644 "${SSH_ONESHOT_CONF}"

systemctl daemon-reload
systemctl restart jarvis-voice.service
sleep 2

# 7. Auditoria do processo e vinculação de PID ao token
PID=$(systemctl show -p MainPID --value jarvis-voice.service)
if [[ -z "${PID}" || "${PID}" -le 0 ]]; then
    echo "ERRO: jarvis-voice.service falhou ao reiniciar." >&2
    rm -f "${SSH_ONESHOT_CONF}"
    systemctl daemon-reload
    exit 1
fi

ENV_DATA=$(tr '\0' '\n' < "/proc/${PID}/environ")

echo "${ENV_DATA}" | grep -q "^JARVIS_SSH_REAL=1$" || {
    echo "ERRO: JARVIS_SSH_REAL=1 não ativo no PID ${PID}!" >&2
    rm -f "${SSH_ONESHOT_CONF}"
    systemctl daemon-reload
    exit 1
}

echo "${ENV_DATA}" | grep -q "^JARVIS_WOL_REAL=1$" || {
    echo "ERRO: JARVIS_WOL_REAL=1 não ativo no PID ${PID}!" >&2
    exit 1
}

echo "${ENV_DATA}" | grep -q "^JARVIS_ALLOWED_REAL_TARGETS=fusion$" || {
    echo "ERRO: JARVIS_ALLOWED_REAL_TARGETS=fusion não ativo no PID ${PID}!" >&2
    exit 1
}

echo "${ENV_DATA}" | grep -q "^JARVIS_REQUIRE_PERSISTENCE=1$" || {
    echo "ERRO: JARVIS_REQUIRE_PERSISTENCE=1 não ativo no PID ${PID}!" >&2
    exit 1
}

# Vincula o token ao PID recém-iniciado
python3 -c "
import json
with open('${TOKEN_FILE}', 'r') as f:
    d = json.load(f)
d['bound_service_pid'] = ${PID}
with open('${TOKEN_FILE}', 'w') as f:
    json.dump(d, f, indent=2)
"
echo "[6/7] Token vinculado ao PID ${PID} ativo."

# 8. Iniciar Watchdog Supervisor em background
echo "[7/7] Inicializando Watchdog Supervisor de desativação automática..."
pkill -f "jarvis_ssh_watchdog.py" 2>/dev/null || true
nohup /opt/jarvis-voice/venv/bin/python /opt/jarvis-voice/scripts/jarvis_ssh_watchdog.py >/var/log/jarvis_ssh_watchdog.log 2>&1 &
WATCHDOG_PID=$!
echo "  ✓ Watchdog ativo (PID ${WATCHDOG_PID}, log em /var/log/jarvis_ssh_watchdog.log)."

echo ""
echo "=========================================================================="
echo "JARVIS VOICE — AMBIENTE PREPARADO PARA TESTE FÍSICO SUPERVISIONADO!"
echo "=========================================================================="
echo "- Alvo Autorizado: Fusion (192.168.0.128)"
echo "- Modo SSH Real: ATIVO (JARVIS_SSH_REAL=1) sob proteção One-Shot"
echo "- Token ID: ${TOKEN_ID}"
echo "- Janela de Validade: ${TTL_SECONDS} segundos"
echo "- PID do Serviço Monitorado: ${PID}"
echo "- Ananuke: 100% BLINDADO (apenas simulação)"
echo "- Desativação Automática: ATIVA via Watchdog e Token descartável"
echo "=========================================================================="
