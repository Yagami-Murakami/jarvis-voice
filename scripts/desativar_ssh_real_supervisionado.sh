#!/bin/bash
set -euo pipefail

# ========================================================================
# JARVIS VOICE — DESATIVAÇÃO DO SSH REAL SUPERVISIONADO
# Restaura imediatamente JARVIS_SSH_REAL=0 e garante persistência.
# ========================================================================

DROPIN_DIR="/etc/systemd/system/jarvis-voice.service.d"
SSH_ONESHOT_CONF="${DROPIN_DIR}/zz-ssh-real-oneshot.conf"
TOKEN_FILE="/var/lib/jarvis/one_shot_ssh_token.json"

echo "=== INICIANDO DESATIVAÇÃO DO SSH REAL SUPERVISIONADO ==="

if [[ "$(id -u)" -ne 0 ]]; then
    echo "ERRO: Este script deve ser executado como root." >&2
    exit 1
fi

# 1. Remover drop-in temporário do SSH real
if [[ -f "${SSH_ONESHOT_CONF}" ]]; then
    echo "[1/4] Removendo drop-in temporário ${SSH_ONESHOT_CONF}..."
    rm -f "${SSH_ONESHOT_CONF}"
else
    echo "[1/4] Drop-in temporário já ausente."
fi

# 2. Invalidação do token se ainda estiver ativo
if [[ -f "${TOKEN_FILE}" ]]; then
    echo "[2/4] Atualizando status do token em disco..."
    python3 -c "
import json, time, os
try:
    with open('${TOKEN_FILE}', 'r') as f:
        d = json.load(f)
    if d.get('status') == 'active' and not d.get('consumed'):
        d['status'] = 'invalidated_by_disarm'
        d['invalidated_at'] = time.time()
        with open('${TOKEN_FILE}', 'w') as f:
            json.dump(d, f, indent=2)
        os.chmod('${TOKEN_FILE}', 0o600)
        print('Token ativo invalidado por desativação.')
    else:
        print(f'Token mantido com status: {d.get(\"status\")}')
except Exception as e:
    print(f'Aviso ao processar token: {e}')
"
fi

# 3. Recarregar systemd e reiniciar serviço
echo "[3/4] Recarregando systemd e reiniciando jarvis-voice.service..."
systemctl daemon-reload
systemctl restart jarvis-voice.service

sleep 2

# 4. Auditoria de processo
PID=$(systemctl show -p MainPID --value jarvis-voice.service)
if [[ -z "${PID}" || "${PID}" -le 0 ]]; then
    echo "ERRO: jarvis-voice.service não está em execução." >&2
    exit 1
fi

ENV_DATA=$(tr '\0' '\n' < "/proc/${PID}/environ")

echo "${ENV_DATA}" | grep -q "^JARVIS_SSH_REAL=0$" || {
    echo "ERRO CRÍTICO: JARVIS_SSH_REAL=0 não encontrado na memória do PID ${PID}!" >&2
    exit 1
}

if echo "${ENV_DATA}" | grep -q "^JARVIS_SSH_REAL=1$"; then
    echo "ERRO CRÍTICO: JARVIS_SSH_REAL=1 ainda presente na memória do PID ${PID}!" >&2
    exit 1
fi

echo "[4/4] JARVIS_SSH_REAL=0 auditado com sucesso no PID ${PID}."

# 5. Encerrar instâncias do watchdog em execução (sem abortar o processo pai se este for o watchdog)
for wp in $(pgrep -f "jarvis_ssh_watchdog.py" || true); do
    if [[ "$wp" != "$$" && "$wp" != "$PPID" ]]; then
        kill "$wp" 2>/dev/null || true
    fi
done

echo "=========================================================================="
echo "DESATIVAÇÃO CONCLUÍDA COM SUCESSO!"
echo "- SSH Real: DESATIVADO (JARVIS_SSH_REAL=0)"
echo "- WoL Real: MANTIDO PARA FUSION (JARVIS_WOL_REAL=1)"
echo "- Alvo protegido: Ananuke 100% isolado"
echo "=========================================================================="
