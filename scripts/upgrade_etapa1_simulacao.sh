#!/usr/bin/env bash
# ==============================================================================
# SCRIPT DE UPGRADE SEGURO — JARVIS VOICE DUAL-MIC (ETAPA 1: SIMULAÇÃO)
# Homologado exclusivamente para a transição: SIMULAÇÃO v2 -> SIMULAÇÃO v3
# Gera a nova versão a partir do backup original verificado (c51d58e6...)
# NUNCA aplica o patch diretamente sobre arquivo já modificado.
# Executa pre-flight obrigatório, validação estrita de backups, substituição
# verdadeiramente atômica, verificação de permissões/owner e auditoria pós-reinício.
# ==============================================================================
set -euo pipefail

EXPECTED_ORIGINAL_SHA="c51d58e6d3a19003eac55a827212e5e084d583fea43389ec0ea5466080ee2d54"
EXPECTED_V1_SHA="f6c7273116aea4c4b34c0c6007d335b7925356ecb174e7066781716f2f604ed8"
EXPECTED_V2_SHA="810aca6e25897f6b95aaf297ca8fdff7d9659270bf079a8c398c4ee21df93b42"
EXPECTED_V3_SHA="c5ea9b984434e33ff9b194a3c873cfb826178a4e853e42ebac3be55b4060fecf"
EXPECTED_PREV_SHA="4a52006def0c54ddcb468561e53c09d2066faa231c86d653893998b73700707f"
EXPECTED_NEW_SHA="63ffa8abc36265f65956677084fe574f1a206839d5b2051c066fab0361fc5c41"

TARGET_FILE="${JARVIS_TARGET_FILE:-/opt/jarvis-voice/jarvis-operacional.py}"
BACKUP_ORIGINAL="${JARVIS_BACKUP_ORIGINAL:-/opt/jarvis-voice/jarvis-operacional.py.backup_original}"
BACKUP_EXTERNAL="${JARVIS_BACKUP_EXTERNAL:-/var/backups/jarvis/jarvis-operacional.py.backup_original}"
BACKUP_V1="${JARVIS_BACKUP_V1:-/opt/jarvis-voice/jarvis-operacional.py.backup_simulacao_v1}"
BACKUP_V1_EXTERNAL="${JARVIS_BACKUP_V1_EXTERNAL:-/var/backups/jarvis/jarvis-operacional.py.backup_simulacao_v1}"
BACKUP_V2="${JARVIS_BACKUP_V2:-/opt/jarvis-voice/jarvis-operacional.py.backup_simulacao_v2}"
BACKUP_V2_EXTERNAL="${JARVIS_BACKUP_V2_EXTERNAL:-/var/backups/jarvis/jarvis-operacional.py.backup_simulacao_v2}"
BACKUP_V3="${JARVIS_BACKUP_V3:-/opt/jarvis-voice/jarvis-operacional.py.backup_simulacao_v3}"
BACKUP_V3_EXTERNAL="${JARVIS_BACKUP_V3_EXTERNAL:-/var/backups/jarvis/jarvis-operacional.py.backup_simulacao_v3}"
PATCH_FILE="${JARVIS_PATCH_FILE:-/opt/jarvis-voice/patches/jarvis-operacional-dual.patch}"
DROPIN_FILE="${JARVIS_DROPIN_FILE:-/etc/systemd/system/jarvis-voice.service.d/zz-simulacao-etapa1.conf}"
PREFLIGHT_SCRIPT="${JARVIS_PREFLIGHT_SCRIPT:-/opt/jarvis-voice/scripts/validar_pre_implantacao.sh}"
POSTCHECK_SCRIPT="${JARVIS_POSTCHECK_SCRIPT:-/opt/jarvis-voice/scripts/validar_pos_inicializacao.sh}"
ROLLBACK_SCRIPT="${JARVIS_ROLLBACK_SCRIPT:-/opt/jarvis-voice/scripts/rollback_etapa1_simulacao.sh}"
PYTHON_BIN="${JARVIS_PYTHON_BIN:-/opt/jarvis-voice/venv/bin/python3}"

if [ ! -x "$PYTHON_BIN" ]; then
    PYTHON_BIN="$(command -v python3)"
fi

APPLY=0
if [ "${1:-}" = "--apply" ]; then
    APPLY=1
fi

echo "========================================================================"
echo "JARVIS VOICE — PROCEDIMENTO DE UPGRADE SEGURO DA ETAPA 1 (SIMULAÇÃO)"
echo "Escopo: Upgrade v2 (810aca6e...) -> v3 (c5ea9b98...)"
echo "Modo: $( [ "$APPLY" -eq 1 ] && echo "APLICAÇÃO EFETIVA (--apply)" || echo "VERIFICAÇÃO / DRY-RUN" )"
echo "========================================================================"

# ==============================================================================
# FASE 1: EXECUÇÃO OBRIGATÓRIA DO PRE-FLIGHT COMPLETO
# ==============================================================================
echo ">>> FASE 1: Executando verificação pré-implantação (pre-flight) obrigatória..."
if [ ! -f "$PREFLIGHT_SCRIPT" ] || [ ! -x "$PREFLIGHT_SCRIPT" ]; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: Script de pre-flight '$PREFLIGHT_SCRIPT' inexistente ou sem permissão de execução!"
    echo "Procedimento abortado imediatamente (fail-closed)."
    exit 1
fi

if ! "$PREFLIGHT_SCRIPT"; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: Verificação pré-implantação (pre-flight) falhou!"
    echo "O upgrade foi abortado imediatamente sem qualquer modificação no sistema."
    exit 1
fi
echo ">>> FASE 1: Pre-flight concluído com sucesso."
echo ""

# ==============================================================================
# FASE 2: VALIDAÇÃO RESTRITA DA VERSÃO V2 E INTEGRIDADE PRÉVIA DE BACKUPS
# ==============================================================================
echo ">>> FASE 2: Validando versão instalada e integridade dos backups..."

# 1. Auditoria do arquivo operacional instalado — RESTRITO EXCLUSIVAMENTE A V2
echo -n "[1/7] Inspecionando hash do arquivo operacional instalado... "
if [ ! -f "$TARGET_FILE" ]; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: $TARGET_FILE inexistente!"
    exit 1
fi
CURRENT_SHA=$(sha256sum "$TARGET_FILE" | awk '{print $1}')

if [ "$CURRENT_SHA" = "$EXPECTED_ORIGINAL_SHA" ]; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: O arquivo operacional atual está na versão original de fábrica ($CURRENT_SHA)!"
    echo "  Este script é homologado exclusivamente para o upgrade v2 -> v3."
    echo "  Não é permitido aplicar o upgrade v2 -> v3 diretamente sobre o controlador de fábrica."
    echo "Procedimento de upgrade abortado imediatamente (fail-closed)."
    exit 1
elif [ "$CURRENT_SHA" = "$EXPECTED_V1_SHA" ]; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: O arquivo operacional atual está na versão de simulação v1 ($CURRENT_SHA)!"
    echo "  Este script é homologado exclusivamente para o upgrade v2 -> v3."
    echo "Procedimento de upgrade abortado imediatamente (fail-closed)."
    exit 1
elif [ "$CURRENT_SHA" != "$EXPECTED_V2_SHA" ] && [ "$CURRENT_SHA" != "$EXPECTED_V3_SHA" ] && [ "$CURRENT_SHA" != "$EXPECTED_PREV_SHA" ]; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: SHA-256 do arquivo operacional atual ($CURRENT_SHA) é DESCONHECIDO ou incompatível!"
    echo "  Versão requerida para este upgrade: Simulação v2 ($EXPECTED_V2_SHA) ou v3 ($EXPECTED_V3_SHA)"
    echo "Procedimento abortado imediatamente (fail-closed)."
    exit 1
fi
ROLLBACK_TARGET="v2"
if [ "$CURRENT_SHA" = "$EXPECTED_V3_SHA" ] || [ "$CURRENT_SHA" = "$EXPECTED_PREV_SHA" ]; then
    ROLLBACK_TARGET="v3"
fi
echo "OK (Versão ativa confirmada: $CURRENT_SHA, rollback alvo: $ROLLBACK_TARGET)"

# 2. Preservação e validação prévia obrigatória dos backups (Original e V2/V3)
echo -n "[2/7] Validando existência e SHA-256 prévios dos backups... "
# Backup Original
if [ ! -f "$BACKUP_ORIGINAL" ] && [ ! -f "$BACKUP_EXTERNAL" ]; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: Backup original inexistente em $BACKUP_ORIGINAL e $BACKUP_EXTERNAL!"
    exit 1
fi
SOURCE_ORIGINAL="$BACKUP_ORIGINAL"
if [ ! -f "$SOURCE_ORIGINAL" ]; then
    SOURCE_ORIGINAL="$BACKUP_EXTERNAL"
fi
ORIG_SHA=$(sha256sum "$SOURCE_ORIGINAL" | awk '{print $1}')
if [ "$ORIG_SHA" != "$EXPECTED_ORIGINAL_SHA" ]; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: SHA-256 do backup original divergente ($ORIG_SHA vs $EXPECTED_ORIGINAL_SHA)!"
    exit 1
fi

if [ "$ROLLBACK_TARGET" = "v3" ]; then
    if [ ! -f "$BACKUP_V3" ] && [ ! -f "$BACKUP_V3_EXTERNAL" ]; then
        echo "FALHA!"
        echo "ERRO CRÍTICO: Backup da versão v3 inexistente em $BACKUP_V3 e $BACKUP_V3_EXTERNAL!"
        exit 1
    fi
    SOURCE_V3="$BACKUP_V3"
    if [ ! -f "$SOURCE_V3" ]; then
        SOURCE_V3="$BACKUP_V3_EXTERNAL"
    fi
    V3_SHA=$(sha256sum "$SOURCE_V3" | awk '{print $1}')
    if [ "$V3_SHA" != "$EXPECTED_V3_SHA" ]; then
        echo "FALHA!"
        echo "ERRO CRÍTICO: SHA-256 do backup v3 divergente ($V3_SHA vs $EXPECTED_V3_SHA)!"
        exit 1
    fi
    echo "OK (Original: $ORIG_SHA, V3: $V3_SHA)"
else
    # Backup V2 (obrigatório para rollback seguro)
    if [ ! -f "$BACKUP_V2" ] && [ ! -f "$BACKUP_V2_EXTERNAL" ]; then
        echo "FALHA!"
        echo "ERRO CRÍTICO: Backup da versão v2 inexistente em $BACKUP_V2 e $BACKUP_V2_EXTERNAL!"
        echo "  O backup v2 previamente homologado é obrigatório para garantir reversibilidade."
        exit 1
    fi
    SOURCE_V2="$BACKUP_V2"
    if [ ! -f "$SOURCE_V2" ]; then
        SOURCE_V2="$BACKUP_V2_EXTERNAL"
    fi
    V2_SHA=$(sha256sum "$SOURCE_V2" | awk '{print $1}')
    if [ "$V2_SHA" != "$EXPECTED_V2_SHA" ]; then
        echo "FALHA!"
        echo "ERRO CRÍTICO: SHA-256 do backup v2 divergente ($V2_SHA vs $EXPECTED_V2_SHA)!"
        echo "  O backup v2 para rollback está corrompido ou inconsistente."
        exit 1
    fi
    echo "OK (Original: $ORIG_SHA, V2: $V2_SHA)"
fi

# 3. Verificação das variáveis de simulação no systemd ANTES da aplicação
echo -n "[3/7] Verificando configurações systemd de simulação obrigatória (pré-upgrade)... "
if [ ! -f "$DROPIN_FILE" ]; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: Drop-in $DROPIN_FILE inexistente!"
    exit 1
fi
if grep -q "JARVIS_WOL_REAL=1" "$DROPIN_FILE" || grep -q "JARVIS_SSH_REAL=1" "$DROPIN_FILE"; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: Flag de modo real detectada em $DROPIN_FILE!"
    exit 1
fi
if ! grep -q "JARVIS_WOL_REAL=0" "$DROPIN_FILE" || \
   ! grep -q "JARVIS_SSH_REAL=0" "$DROPIN_FILE" || \
   ! grep -q "JARVIS_REQUIRE_PERSISTENCE=1" "$DROPIN_FILE"; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: Variáveis de simulação ausentes em $DROPIN_FILE!"
    exit 1
fi

# Se systemctl estiver ativo, verifica o ambiente consolidado
if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet jarvis-voice.service 2>/dev/null; then
    SYS_ENV=$(systemctl show -p Environment jarvis-voice.service 2>/dev/null || true)
    if [ -n "$SYS_ENV" ]; then
        if ! echo "$SYS_ENV" | grep -q "JARVIS_WOL_REAL=0" || \
           ! echo "$SYS_ENV" | grep -q "JARVIS_SSH_REAL=0" || \
           ! echo "$SYS_ENV" | grep -q "JARVIS_REQUIRE_PERSISTENCE=1" || \
           echo "$SYS_ENV" | grep -q "JARVIS_WOL_REAL=1" || \
           echo "$SYS_ENV" | grep -q "JARVIS_SSH_REAL=1"; then
            echo "FALHA!"
            echo "ERRO CRÍTICO: Ambiente systemd não está estritamente em simulação ($SYS_ENV)!"
            exit 1
        fi
    fi
fi
echo "OK (WoL=0, SSH=0, REQUIRE_PERSISTENCE=1)"

# 4. Geração da nova versão a partir do backup original (sem modificar produção)
echo -n "[4/7] Gerando nova versão operacional a partir do backup original... "
TMP_BUILD=$(mktemp /tmp/jarvis-build-XXXXXX.py)
trap 'rm -f "$TMP_BUILD"' EXIT

cp "$SOURCE_ORIGINAL" "$TMP_BUILD"
if ! patch -p1 "$TMP_BUILD" < "$PATCH_FILE" >/dev/null 2>&1; then
    if ! patch "$TMP_BUILD" < "$PATCH_FILE" >/dev/null 2>&1; then
        echo "FALHA!"
        echo "ERRO CRÍTICO: patch falhou ao ser aplicado sobre o backup original!"
        exit 1
    fi
fi

GENERATED_SHA=$(sha256sum "$TMP_BUILD" | awk '{print $1}')
if [ "$GENERATED_SHA" != "$EXPECTED_NEW_SHA" ]; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: SHA-256 gerado divergente do esperado!"
    echo "  Esperado: $EXPECTED_NEW_SHA"
    echo "  Obtido:   $GENERATED_SHA"
    exit 1
fi
echo "OK (SHA: $GENERATED_SHA)"

# 5. Validação de sintaxe Python da nova versão gerada
echo -n "[5/7] Validando sintaxe da nova versão gerada... "
if ! "$PYTHON_BIN" -m py_compile "$TMP_BUILD" 2>/dev/null; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: Erro de sintaxe na nova versão operacional!"
    exit 1
fi
echo "OK (Sintaxe Python limpa)"

# 6. Teste de reversibilidade garantida
echo -n "[6/7] Validando reversibilidade (dry-run reverso sobre nova versão)... "
if ! patch -R --dry-run "$TMP_BUILD" < "$PATCH_FILE" >/dev/null 2>&1; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: Reversibilidade do patch falhou!"
    exit 1
fi
echo "OK (Reversibilidade 100% verificada)"

# 7. Conclusão de Dry-Run ou Aplicação Efetiva
if [ "$APPLY" -eq 0 ]; then
    echo "[7/7] Modo DRY-RUN concluído com sucesso."
    echo ""
    echo "========================================================================"
    echo "STATUS: Pré-voo e nova versão validados com sucesso."
    echo "Nenhuma alteração foi realizada em produção."
    echo "Para aplicar efetivamente sob autorização do operador, execute:"
    echo "  sudo $0 --apply"
    echo "========================================================================"
    exit 0
fi

# ==============================================================================
# FASE 3: SUBSTITUIÇÃO VERDADEIRAMENTE ATÔMICA E AUDITORIA PÓS-INICIALIZAÇÃO
# ==============================================================================
echo ""
echo ">>> FASE 3: Aplicando nova versão operacional (--apply solicitado)..."

# Identificação rigorosa do usuário e grupo alvo (não ignora erros)
if id tuninho >/dev/null 2>&1; then
    TARGET_USER="tuninho"
    TARGET_GROUP="tuninho"
else
    TARGET_USER="$(id -un)"
    TARGET_GROUP="$(id -gn)"
fi

# Substituição atômica via mktemp no mesmo diretório de destino e rename (mv -f)
echo -n "[1/4] Realizando substituição verdadeiramente atômica do controlador... "
TARGET_DIR="$(dirname "$TARGET_FILE")"
TMP_ATOMIC=$(mktemp "${TARGET_DIR}/.jarvis-operacional.py.tmp.XXXXXX")

cp "$TMP_BUILD" "$TMP_ATOMIC"

# Definir e verificar permissões rigorosamente (sem ignorar erros)
if ! chmod 0755 "$TMP_ATOMIC"; then
    rm -f "$TMP_ATOMIC"
    echo "FALHA!"
    echo "ERRO CRÍTICO: Falha ao executar chmod 0755 em $TMP_ATOMIC!"
    exit 1
fi

if ! chown "${TARGET_USER}:${TARGET_GROUP}" "$TMP_ATOMIC"; then
    rm -f "$TMP_ATOMIC"
    echo "FALHA!"
    echo "ERRO CRÍTICO: Falha ao executar chown ${TARGET_USER}:${TARGET_GROUP} em $TMP_ATOMIC!"
    exit 1
fi

# Verificação das permissões e proprietário antes do rename
TMP_PERMS=$(stat -c "%a" "$TMP_ATOMIC")
TMP_OWNER=$(stat -c "%U:%G" "$TMP_ATOMIC")
if [ "$TMP_PERMS" != "755" ] || [ "$TMP_OWNER" != "${TARGET_USER}:${TARGET_GROUP}" ]; then
    rm -f "$TMP_ATOMIC"
    echo "FALHA!"
    echo "ERRO CRÍTICO: Permissões ($TMP_PERMS) ou proprietário ($TMP_OWNER) incorretos em $TMP_ATOMIC!"
    exit 1
fi

ATOMIC_SHA=$(sha256sum "$TMP_ATOMIC" | awk '{print $1}')
if [ "$ATOMIC_SHA" != "$EXPECTED_NEW_SHA" ]; then
    rm -f "$TMP_ATOMIC"
    echo "FALHA!"
    echo "ERRO CRÍTICO: SHA-256 do arquivo atômico temporário divergente!"
    exit 1
fi

# POSIX rename (atômico): substituição instantânea sem truncamento
mv -f "$TMP_ATOMIC" "$TARGET_FILE"

FINAL_SHA=$(sha256sum "$TARGET_FILE" | awk '{print $1}')
FINAL_PERMS=$(stat -c "%a" "$TARGET_FILE")
FINAL_OWNER=$(stat -c "%U:%G" "$TARGET_FILE")

if [ "$FINAL_SHA" != "$EXPECTED_NEW_SHA" ]; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: SHA-256 final de $TARGET_FILE divergente após substituição!"
    exit 1
fi
if [ "$FINAL_PERMS" != "755" ] || [ "$FINAL_OWNER" != "${TARGET_USER}:${TARGET_GROUP}" ]; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: Verificação pós-substituição falhou (Permissões: $FINAL_PERMS, Proprietário: $FINAL_OWNER)!"
    exit 1
fi
echo "OK (Substituição atômica concluída: SHA $FINAL_SHA, 0755, $FINAL_OWNER)"

# Reinicialização dos serviços com captura explícita de erro e gatilho de rollback
echo -n "[2/4] Reiniciando serviços operacionais no systemd... "
if command -v systemctl >/dev/null 2>&1; then
    if ! systemctl daemon-reload; then
        echo "FALHA!"
        echo "ERRO CRÍTICO: 'systemctl daemon-reload' falhou! Acionando rollback para v2..."
        "$ROLLBACK_SCRIPT" "$ROLLBACK_TARGET"
        exit 1
    fi

    if ! systemctl restart jarvis-voice.service; then
        echo "FALHA!"
        echo "ERRO CRÍTICO: 'systemctl restart jarvis-voice.service' falhou! Acionando rollback para v2..."
        "$ROLLBACK_SCRIPT" "$ROLLBACK_TARGET"
        exit 1
    fi

    if ! systemctl restart jarvis-audio-gateway.service; then
        echo "FALHA!"
        echo "ERRO CRÍTICO: 'systemctl restart jarvis-audio-gateway.service' falhou! Acionando rollback para v2..."
        "$ROLLBACK_SCRIPT" "$ROLLBACK_TARGET"
        exit 1
    fi

    sleep 2

    if ! systemctl is-active --quiet jarvis-voice.service; then
        echo "FALHA!"
        echo "ERRO CRÍTICO: jarvis-voice.service não está ativo após reinício! Acionando rollback para v2..."
        "$ROLLBACK_SCRIPT" "$ROLLBACK_TARGET"
        exit 1
    fi
    if ! systemctl is-active --quiet jarvis-audio-gateway.service; then
        echo "FALHA!"
        echo "ERRO CRÍTICO: jarvis-audio-gateway.service não está ativo após reinício! Acionando rollback para v2..."
        "$ROLLBACK_SCRIPT" "$ROLLBACK_TARGET"
        exit 1
    fi
    echo "OK (Serviços operacionais ativos)"
else
    echo "AVISO (Ambiente sem systemctl, reinício ignorado)"
fi

# Validação das variáveis de simulação efetivas pós-reinicialização
echo -n "[3/4] Validando configurações efetivas de simulação no systemd pós-reinício... "
if command -v systemctl >/dev/null 2>&1; then
    POST_ENV=$(systemctl show -p Environment jarvis-voice.service)
    if ! echo "$POST_ENV" | grep -q "JARVIS_WOL_REAL=0" || \
       ! echo "$POST_ENV" | grep -q "JARVIS_SSH_REAL=0" || \
       ! echo "$POST_ENV" | grep -q "JARVIS_REQUIRE_PERSISTENCE=1" || \
       echo "$POST_ENV" | grep -q "JARVIS_WOL_REAL=1" || \
       echo "$POST_ENV" | grep -q "JARVIS_SSH_REAL=1"; then
        echo "FALHA!"
        echo "ERRO CRÍTICO: Configurações pós-reinício violaram modo simulação ($POST_ENV)!"
        echo "Acionando rollback automático imediato para versão v2..."
        "$ROLLBACK_SCRIPT" "$ROLLBACK_TARGET"
        exit 1
    fi
    echo "OK (WoL=0, SSH=0, REQUIRE_PERSISTENCE=1 ativos)"
else
    echo "AVISO (Ambiente sem systemctl)"
fi

# Execução obrigatória da auditoria pós-inicialização
echo "[4/4] Executando auditoria pós-inicialização obrigatória..."
if [ ! -f "$POSTCHECK_SCRIPT" ] || [ ! -x "$POSTCHECK_SCRIPT" ]; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: Script de auditoria '$POSTCHECK_SCRIPT' inexistente ou sem permissão de execução!"
    echo "Acionando rollback automático imediato para versão v2..."
    "$ROLLBACK_SCRIPT" "$ROLLBACK_TARGET"
    exit 1
fi

if ! "$POSTCHECK_SCRIPT"; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: Auditoria pós-inicialização falhou!"
    echo "Acionando rollback automático imediato para versão v2..."
    "$ROLLBACK_SCRIPT" "$ROLLBACK_TARGET"
    exit 1
fi

echo ""
echo "========================================================================"
echo "=== UPGRADE PARA SIMULAÇÃO V3 CONCLUÍDO COM SUCESSO EM MODO SIMULAÇÃO ==="
echo "========================================================================"
