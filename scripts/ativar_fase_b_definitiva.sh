#!/usr/bin/env bash
# ==============================================================================
# ATIVAÇÃO DEFINITIVA DA PRODUÇÃO — JARVIS VOICE DUAL-MIC
# Escopo:
# - WoL Real: Ativo para Fusion
# - SSH Real: Ativo para Fusion (com confirmação vocal obrigatória em 2 etapas)
# - Ananuke: 100% blindado em simulação (JARVIS_ALLOWED_REAL_TARGETS=fusion)
# - One-Shot Token: Desativado (sem expiração de 10 minutos)
# ==============================================================================
set -euo pipefail

DROPIN_DIR="/etc/systemd/system/jarvis-voice.service.d"
DEFINITIVO_CONF="${DROPIN_DIR}/zz-producao-definitiva.conf"

echo "=== ATIVANDO MODO DEFINITIVO DO JARVIS VOICE ==="

# 1. Encerrar qualquer watchdog de expiração temporária
pkill -f "jarvis_ssh_watchdog.py" 2>/dev/null || true
rm -f /var/lib/jarvis/one_shot_ssh_token.json

# 2. Desabilitar drop-ins anteriores temporários
mkdir -p "$DROPIN_DIR"
for old in "${DROPIN_DIR}"/zz-fase-b-supervisionada.conf "${DROPIN_DIR}"/zz-ssh-real-oneshot.conf; do
    if [ -f "$old" ]; then
        mv "$old" "${old}.disabled"
    fi
done

# 3. Criar configuração definitiva
cat << 'DROPIN' > "$DEFINITIVO_CONF"
[Service]
Environment=PYTHONUNBUFFERED=1
Environment=JARVIS_WOL_REAL=1
Environment=JARVIS_SSH_REAL=1
Environment=JARVIS_ALLOWED_REAL_TARGETS=fusion
Environment=JARVIS_REQUIRE_PERSISTENCE=1
Environment=JARVIS_REQUIRE_ONE_SHOT_TOKEN=0
RuntimeDirectory=jarvis
RuntimeDirectoryMode=0750
DROPIN

# 4. Recarregar systemd e reiniciar jarvis-voice
systemctl daemon-reload
systemctl restart jarvis-voice.service
sleep 2

# 5. Auditoria de processo
MAIN_PID=$(systemctl show -p MainPID --value jarvis-voice.service)
PROC_ENV=$(strings "/proc/${MAIN_PID}/environ" 2>/dev/null || true)

echo "Auditoria do PID ${MAIN_PID}:"
for var in "JARVIS_WOL_REAL=1" "JARVIS_SSH_REAL=1" "JARVIS_ALLOWED_REAL_TARGETS=fusion" "JARVIS_REQUIRE_PERSISTENCE=1" "JARVIS_REQUIRE_ONE_SHOT_TOKEN=0"; do
    if echo "$PROC_ENV" | grep -q "^${var}$"; then
        echo "  [OK] $var"
    else
        echo "  [ERRO] $var NÃO encontrado no processo!"
        exit 1
    fi
done

echo ""
echo "=== PRODUÇÃO DEFINITIVA ATIVA COM SUCESSO ==="
echo "Controle do Fusion liberado permanentemente (sem expiração de token)."
echo "Ananuke mantido blindado contra qualquer ação física."
