#!/usr/bin/env bash
# ==============================================================================
# SCRIPT DE AUDITORIA PÓS-INICIALIZAÇÃO (POST-STARTUP VERIFICATION)
# Jarvis Voice Dual-Mic — Etapa 1: Simulação Obrigatória
# ==============================================================================
set -euo pipefail

echo "=== INICIANDO AUDITORIA DO PROCESSO EM EXECUÇÃO (ETAPA 1) ==="

# 1. Verificar status ativo do serviço
echo -n "[1/4] Verificando se jarvis-voice.service está ativo... "
if ! systemctl is-active --quiet jarvis-voice.service; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: jarvis-voice.service não está em execução!"
    systemctl status jarvis-voice.service --no-pager | head -n 25
    exit 1
fi
ACTIVE_PID=$(systemctl show -p MainPID --value jarvis-voice.service)
echo "OK (PID: $ACTIVE_PID)"

# 2. Inspecionar variáveis de ambiente físicas na memória do processo (/proc/$PID/environ)
echo -n "[2/4] Auditando variáveis de ambiente no processo ativo (/proc/$ACTIVE_PID/environ)... "
if [ ! -r "/proc/$ACTIVE_PID/environ" ]; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: Não foi possível ler /proc/$ACTIVE_PID/environ!"
    exit 1
fi

PROC_ENV=$(strings "/proc/$ACTIVE_PID/environ")

# Validações estritas de segurança sem imprimir credenciais / tokens
if ! echo "$PROC_ENV" | grep -q "^JARVIS_WOL_REAL=0$"; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: JARVIS_WOL_REAL=0 não encontrado na memória do PID $ACTIVE_PID!"
    exit 1
fi

if ! echo "$PROC_ENV" | grep -q "^JARVIS_SSH_REAL=0$"; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: JARVIS_SSH_REAL=0 não encontrado na memória do PID $ACTIVE_PID!"
    exit 1
fi

if ! echo "$PROC_ENV" | grep -q "^JARVIS_REQUIRE_PERSISTENCE=1$"; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: JARVIS_REQUIRE_PERSISTENCE=1 não encontrado na memória do PID $ACTIVE_PID!"
    exit 1
fi

if echo "$PROC_ENV" | grep -q "^JARVIS_WOL_REAL=1$" || echo "$PROC_ENV" | grep -q "^JARVIS_SSH_REAL=1$"; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: Flag de modo real ativa na memória do PID $ACTIVE_PID!"
    exit 1
fi
echo "OK (WoL=0, SSH=0, PERSISTENCE=1 confirmados na memória)"

# 3. Confirmar mensagem de simulação nos logs de inicialização do journal
echo -n "[3/4] Verificando banner de simulação no systemd journal... "
JOURNAL_SNIPPET=$(journalctl _PID=$ACTIVE_PID --no-pager 2>/dev/null || journalctl -u jarvis-voice.service -n 150 --no-pager)
if ! echo "$JOURNAL_SNIPPET" | grep -q "MODO SIMULACAO - SEM ACOES REAIS"; then
    JOURNAL_SNIPPET=$(journalctl -u jarvis-voice.service -n 150 --no-pager)
fi
if ! echo "$JOURNAL_SNIPPET" | grep -q "MODO SIMULACAO - SEM ACOES REAIS"; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: Mensagem 'MODO SIMULACAO - SEM ACOES REAIS' não detectada no log!"
    echo "Últimos logs:"
    echo "$JOURNAL_SNIPPET" | tail -n 15
    exit 1
fi
echo "OK (Banner de simulação registrado no journal)"

# 4. Verificar disponibilidade do barramento IPC local
IPC_SOCK="${JARVIS_IPC_SOCK:-/run/jarvis/voice-event.sock}"
echo -n "[4/4] Verificando barramento IPC $IPC_SOCK... "
if [ ! -S "$IPC_SOCK" ]; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: Socket IPC $IPC_SOCK não foi criado!"
    exit 1
fi
SOCK_PERMS=$(stat -c "%a %U:%G" "$IPC_SOCK")
echo "OK (Socket UNIX ativo com permissões $SOCK_PERMS)"

echo ""
echo "=================================================================="
echo "AUDITORIA PÓS-INICIALIZAÇÃO CONCLUÍDA COM SUCESSO."
echo "O PROCESSO ESTÁ ATIVO, ISOLADO E ESTRITAMENTE EM MODO SIMULAÇÃO."
echo "=================================================================="
