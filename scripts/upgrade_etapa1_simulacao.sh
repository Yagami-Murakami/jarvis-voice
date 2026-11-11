#!/usr/bin/env bash
# ==============================================================================
# SCRIPT DE UPGRADE SEGURO — JARVIS VOICE DUAL-MIC (ETAPA 1: SIMULAÇÃO)
# Gera a nova versão a partir do backup original verificado (c51d58e6...)
# NUNCA aplica o patch diretamente sobre arquivo já modificado.
# ==============================================================================
set -euo pipefail

EXPECTED_ORIGINAL_SHA="c51d58e6d3a19003eac55a827212e5e084d583fea43389ec0ea5466080ee2d54"
EXPECTED_V1_SHA="f6c7273116aea4c4b34c0c6007d335b7925356ecb174e7066781716f2f604ed8"
EXPECTED_NEW_SHA="810aca6e25897f6b95aaf297ca8fdff7d9659270bf079a8c398c4ee21df93b42"

TARGET_FILE="/opt/jarvis-voice/jarvis-operacional.py"
BACKUP_ORIGINAL="/opt/jarvis-voice/jarvis-operacional.py.backup_original"
BACKUP_EXTERNAL="/var/backups/jarvis/jarvis-operacional.py.backup_original"
BACKUP_V1="/opt/jarvis-voice/jarvis-operacional.py.backup_simulacao_v1"
BACKUP_V1_EXTERNAL="/var/backups/jarvis/jarvis-operacional.py.backup_simulacao_v1"
PATCH_FILE="/opt/jarvis-voice/patches/jarvis-operacional-dual.patch"
GATEWAY_FILE="/opt/jarvis-voice/gateway/audio_gateway.py"

APPLY=0
if [ "${1:-}" = "--apply" ]; then
    APPLY=1
fi

echo "========================================================================"
echo "JARVIS VOICE — PROCEDIMENTO DE UPGRADE SEGURO DA ETAPA 1 (SIMULAÇÃO)"
echo "Modo: $( [ "$APPLY" -eq 1 ] && echo "APLICAÇÃO EFETIVA (--apply)" || echo "VERIFICAÇÃO / DRY-RUN" )"
echo "========================================================================"

# 1. Auditoria prévia do arquivo de produção atual
echo -n "[1/7] Inspecionando estado do arquivo operacional instalado... "
if [ ! -f "$TARGET_FILE" ]; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: $TARGET_FILE inexistente!"
    exit 1
fi
CURRENT_SHA=$(sha256sum "$TARGET_FILE" | awk '{print $1}')
echo "OK (SHA: $CURRENT_SHA)"

# 2. Preservação e validação de backups (Original e V1)
echo -n "[2/7] Validando integridade dos backups (Original e V1)... "
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

# Se estiver na versão v1, garante que backup_v1 existe
if [ "$CURRENT_SHA" = "$EXPECTED_V1_SHA" ]; then
    if [ ! -f "$BACKUP_V1" ]; then
        echo -n "Criando $BACKUP_V1... "
        cp -p "$TARGET_FILE" "$BACKUP_V1"
        cp -p "$TARGET_FILE" "$BACKUP_V1_EXTERNAL"
        chmod 0440 "$BACKUP_V1" "$BACKUP_V1_EXTERNAL"
    fi
    V1_SHA=$(sha256sum "$BACKUP_V1" | awk '{print $1}')
    if [ "$V1_SHA" != "$EXPECTED_V1_SHA" ]; then
        echo "FALHA!"
        echo "ERRO CRÍTICO: Backup V1 divergente ($V1_SHA vs $EXPECTED_V1_SHA)!"
        exit 1
    fi
fi
echo "OK (Original e V1 íntegros)"

# 3. Verificação das variáveis de simulação no systemd
echo -n "[3/7] Verificando configurações systemd de simulação obrigatória... "
DROPIN="/etc/systemd/system/jarvis-voice.service.d/zz-simulacao-etapa1.conf"
if [ ! -f "$DROPIN" ]; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: Drop-in $DROPIN inexistente!"
    exit 1
fi
if ! grep -q "JARVIS_WOL_REAL=0" "$DROPIN" || ! grep -q "JARVIS_SSH_REAL=0" "$DROPIN" || ! grep -q "JARVIS_REQUIRE_PERSISTENCE=1" "$DROPIN"; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: Variáveis de simulação ausentes em $DROPIN!"
    exit 1
fi
echo "OK (WoL=0, SSH=0, REQUIRE_PERSISTENCE=1)"

# 4. Geração da nova versão a partir do backup original (sem modificar produção)
echo -n "[4/7] Gerando nova versão operacional a partir do backup original... "
TMP_BUILD=$(mktemp /tmp/jarvis-build-XXXXXX.py)
trap 'rm -f "$TMP_BUILD"' EXIT

cp "$SOURCE_ORIGINAL" "$TMP_BUILD"
if ! patch -p1 "$TMP_BUILD" < "$PATCH_FILE" >/dev/null 2>&1; then
    # Tentativa sem -p1 caso header seja sem prefixo
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
if ! /opt/jarvis-voice/venv/bin/python3 -m py_compile "$TMP_BUILD" 2>/dev/null; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: Erro de sintaxe na nova versão operacional!"
    exit 1
fi
echo "OK (Sintaxe Python 3.11 limpa)"

# 6. Teste de reversibilidade garantida
echo -n "[6/7] Validando reversibilidade (dry-run reverso sobre nova versão)... "
if ! patch -R --dry-run "$TMP_BUILD" < "$PATCH_FILE" >/dev/null 2>&1; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: Reversibilidade do patch falhou!"
    exit 1
fi
echo "OK (Reversibilidade 100% verificada)"

# 7. Conclusão ou Aplicação Efetiva
if [ "$APPLY" -eq 0 ]; then
    echo "[7/7] Modo DRY-RUN concluído com sucesso."
    echo ""
    echo "========================================================================"
    echo "STATUS: O arquivo operacional novo está pronto e verificado."
    echo "Nenhuma alteração foi realizada em produção."
    echo "Para aplicar efetivamente quando houver autorização do operador, execute:"
    echo "  sudo $0 --apply"
    echo "========================================================================"
    exit 0
fi

echo -n "[7/7] Aplicando nova versão em produção e reiniciando serviços... "
install -m 0755 -o tuninho -g tuninho "$TMP_BUILD" "$TARGET_FILE"
FINAL_SHA=$(sha256sum "$TARGET_FILE" | awk '{print $1}')
if [ "$FINAL_SHA" != "$EXPECTED_NEW_SHA" ]; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: SHA-256 final divergente após instalação!"
    exit 1
fi

systemctl daemon-reload
systemctl restart jarvis-voice.service
systemctl restart jarvis-audio-gateway.service
sleep 2

if ! systemctl is-active --quiet jarvis-voice.service; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: jarvis-voice.service falhou ao reiniciar!"
    exit 1
fi
if ! systemctl is-active --quiet jarvis-audio-gateway.service; then
    echo "FALHA!"
    echo "ERRO CRÍTICO: jarvis-audio-gateway.service falhou ao reiniciar!"
    exit 1
fi
echo "OK (Serviços operacionais ativos com nova versão)"

# Auditoria pós-inicialização
if [ -x /opt/jarvis-voice/scripts/validar_pos_inicializacao.sh ]; then
    /opt/jarvis-voice/scripts/validar_pos_inicializacao.sh
fi

echo ""
echo "=== UPGRADE PARA COMMIT 3b64d6d CONCLUÍDO COM SUCESSO EM MODO SIMULAÇÃO ==="
