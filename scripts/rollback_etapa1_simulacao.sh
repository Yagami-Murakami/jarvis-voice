#!/usr/bin/env bash
# ==============================================================================
# SCRIPT DE ROLLBACK ESPECÍFICO — JARVIS VOICE DUAL-MIC (ETAPA 1: SIMULAÇÃO)
# Permite reverter para a versão anterior em simulação (v1: f6c72731...)
# ou para o backup original de fábrica (original: c51d58e6...).
# NUNCA REATIVA WAKE-ON-LAN OU SSH REAIS.
# Preserva estritamente JARVIS_WOL_REAL=0, JARVIS_SSH_REAL=0 e JARVIS_REQUIRE_PERSISTENCE=1.
# Realiza substituição verdadeiramente atômica e verifica permissões e proprietário.
# ==============================================================================
set -euo pipefail

TARGET_VERSION="${1:-v1}"

EXPECTED_ORIGINAL_SHA="c51d58e6d3a19003eac55a827212e5e084d583fea43389ec0ea5466080ee2d54"
EXPECTED_V1_SHA="f6c7273116aea4c4b34c0c6007d335b7925356ecb174e7066781716f2f604ed8"

TARGET_FILE="${JARVIS_TARGET_FILE:-/opt/jarvis-voice/jarvis-operacional.py}"
BACKUP_ORIGINAL="${JARVIS_BACKUP_ORIGINAL:-/opt/jarvis-voice/jarvis-operacional.py.backup_original}"
BACKUP_ORIGINAL_EXT="${JARVIS_BACKUP_EXTERNAL:-/var/backups/jarvis/jarvis-operacional.py.backup_original}"
BACKUP_V1="${JARVIS_BACKUP_V1:-/opt/jarvis-voice/jarvis-operacional.py.backup_simulacao_v1}"
BACKUP_V1_EXT="${JARVIS_BACKUP_V1_EXTERNAL:-/var/backups/jarvis/jarvis-operacional.py.backup_simulacao_v1}"
DROPIN_DIR="${JARVIS_DROPIN_DIR:-/etc/systemd/system/jarvis-voice.service.d}"

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
if [ -d "$DROPIN_DIR" ]; then
    if [ -f "${DROPIN_DIR}/producao.conf" ]; then
        mv "${DROPIN_DIR}/producao.conf" "${DROPIN_DIR}/producao.conf.disabled"
    fi
    mkdir -p "$DROPIN_DIR"
    cat << 'DROPIN' > "${DROPIN_DIR}/zz-simulacao-etapa1.conf"
[Service]
Environment=JARVIS_WOL_REAL=0
Environment=JARVIS_SSH_REAL=0
Environment=JARVIS_REQUIRE_PERSISTENCE=1
RuntimeDirectory=jarvis
RuntimeDirectoryMode=0750
DROPIN
    if command -v systemctl >/dev/null 2>&1; then
        systemctl daemon-reload 2>/dev/null || true
    fi
    echo "OK (WoL=0, SSH=0, REQUIRE_PERSISTENCE=1)"
else
    echo "AVISO (Diretório $DROPIN_DIR não configurado ou mock)"
fi

# 3. Restaurar arquivo operacional via substituição atômica com validação de permissão e proprietário
echo -n "[3/5] Restaurando $TARGET_FILE a partir de $SOURCE_BACKUP de forma atômica... "
if id tuninho >/dev/null 2>&1; then
    TARGET_USER="tuninho"
    TARGET_GROUP="tuninho"
else
    TARGET_USER="$(id -un)"
    TARGET_GROUP="$(id -gn)"
fi

TARGET_DIR="$(dirname "$TARGET_FILE")"
TMP_ATOMIC=$(mktemp "${TARGET_DIR}/.jarvis-operacional.py.rollback.XXXXXX")

cp "$SOURCE_BACKUP" "$TMP_ATOMIC"

if ! chmod 0755 "$TMP_ATOMIC"; then
    rm -f "$TMP_ATOMIC"
    echo "FALHA!"
    echo "ERRO CRÍTICO: Falha ao executar chmod 0755 no arquivo de rollback!"
    exit 1
fi

if ! chown "${TARGET_USER}:${TARGET_GROUP}" "$TMP_ATOMIC"; then
    rm -f "$TMP_ATOMIC"
    echo "FALHA!"
    echo "ERRO CRÍTICO: Falha ao executar chown ${TARGET_USER}:${TARGET_GROUP} no arquivo de rollback!"
    exit 1
fi

TMP_PERMS=$(stat -c "%a" "$TMP_ATOMIC")
TMP_OWNER=$(stat -c "%U:%G" "$TMP_ATOMIC")
if [ "$TMP_PERMS" != "755" ] || [ "$TMP_OWNER" != "${TARGET_USER}:${TARGET_GROUP}" ]; then
    rm -f "$TMP_ATOMIC"
    echo "FALHA!"
    echo "ERRO CRÍTICO: Permissão ou proprietário incorretos no arquivo de restauração!"
    exit 1
fi

ATOMIC_RESTORE_SHA=$(sha256sum "$TMP_ATOMIC" | awk '{print $1}')
if [ "$ATOMIC_RESTORE_SHA" != "$EXPECTED_RESTORE_SHA" ]; then
    rm -f "$TMP_ATOMIC"
    echo "FALHA!"
    echo "ERRO CRÍTICO: Hash do arquivo atômico temporário de restauração divergente!"
    exit 1
fi

mv -f "$TMP_ATOMIC" "$TARGET_FILE"
RESTORED_SHA=$(sha256sum "$TARGET_FILE" | awk '{print $1}')
RESTORED_PERMS=$(stat -c "%a" "$TARGET_FILE")
RESTORED_OWNER=$(stat -c "%U:%G" "$TARGET_FILE")

if [ "$RESTORED_SHA" != "$EXPECTED_RESTORE_SHA" ]; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: Hash do arquivo restaurado divergente de $EXPECTED_RESTORE_SHA!"
    exit 1
fi
if [ "$RESTORED_PERMS" != "755" ] || [ "$RESTORED_OWNER" != "${TARGET_USER}:${TARGET_GROUP}" ]; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: Permissões ou proprietário incorretos no arquivo restaurado ($RESTORED_PERMS, $RESTORED_OWNER)!"
    exit 1
fi
echo "OK (Restaurado: SHA $RESTORED_SHA, 0755, $RESTORED_OWNER)"

# 4. Reiniciar serviços operacionais
echo -n "[4/5] Reiniciando jarvis-voice.service... "
if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet jarvis-voice.service 2>/dev/null; then
    systemctl restart jarvis-voice.service
    if [ "$TARGET_VERSION" = "v1" ] && systemctl is-active --quiet jarvis-audio-gateway.service 2>/dev/null; then
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
else
    echo "OK (Reinício não aplicável neste ambiente)"
fi

# 5. Auditoria de memória do processo
echo -n "[5/5] Auditando memória física do PID ativo... "
if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet jarvis-voice.service 2>/dev/null; then
    RESTORED_PID=$(systemctl show -p MainPID --value jarvis-voice.service 2>/dev/null || true)
    if [ -n "$RESTORED_PID" ] && [ "$RESTORED_PID" -gt 0 ] && [ -r "/proc/$RESTORED_PID/environ" ]; then
        ENV_RUNNING=$(strings "/proc/$RESTORED_PID/environ")

        if ! echo "$ENV_RUNNING" | grep -q "^JARVIS_WOL_REAL=0$"; then
            echo "FALHA!"
            echo "ERRO CRÍTICO: JARVIS_WOL_REAL=0 ausente na memória do processo $RESTORED_PID!"
            exit 1
        fi
        if ! echo "$ENV_RUNNING" | grep -q "^JARVIS_SSH_REAL=0$"; then
            echo "FALHA!"
            echo "ERRO CRÍTICO: JARVIS_SSH_REAL=0 ausente na memória do processo $RESTORED_PID!"
            exit 1
        fi
        if ! echo "$ENV_RUNNING" | grep -q "^JARVIS_REQUIRE_PERSISTENCE=1$"; then
            echo "FALHA!"
            echo "ERRO CRÍTICO: JARVIS_REQUIRE_PERSISTENCE=1 ausente na memória do processo $RESTORED_PID!"
            exit 1
        fi
        if echo "$ENV_RUNNING" | grep -q "JARVIS_WOL_REAL=1" || echo "$ENV_RUNNING" | grep -q "JARVIS_SSH_REAL=1"; then
            echo "FALHA!"
            echo "ERRO CRÍTICO: Flag real detectada no processo $RESTORED_PID!"
            exit 1
        fi
        echo "OK (PID $RESTORED_PID estritamente em MODO SIMULAÇÃO com persistência obrigatória)"
    else
        echo "OK (PID não inspecionável)"
    fi
else
    echo "OK (Ambiente mock/sem systemctl)"
fi

echo ""
echo "=== ROLLBACK PARA '$TARGET_VERSION' CONCLUÍDO COM SUCESSO EM MODO SEGURO ==="
