#!/bin/bash
# ==============================================================================
# Script de Implantação Segura do Gateway Multi-Cliente no Jarvis LXC 110
#
# Execução autorizada exclusivamente em ambiente de produção supervisionado.
# Realiza:
# 1. Verificações prévias de segurança e ambiente.
# 2. Backup timestamped do código e das variáveis de ambiente.
# 3. Compilação estática prévia do Python (py_compile).
# 4. Atualização atômica do arquivo audio_gateway.py.
# 5. Adição do token individual para o celular mobile em audio-gateway.env.
# 6. Reinicialização e validação de saúde do serviço (portas 8088 e 8089).
# 7. Rollback automático e imediato em caso de qualquer falha no health-check.
# ==============================================================================
set -euo pipefail

GATEWAY_DIR="/opt/jarvis-voice/gateway"
ENV_FILE="/etc/jarvis/audio-gateway.env"
SERVICE_NAME="jarvis-audio-gateway.service"
SRC_GATEWAY="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/gateway/audio_gateway.py"

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_MANIFEST="${GATEWAY_DIR}/.last_backup_manifest"

# Argumento opcional: token individual do novo celular móvel
MOBILE_TOKEN="${1:-}"
MOBILE_DEVICE_ID="${2:-celular_mobile}"

echo "================================================================="
echo "   IMPLANTAÇÃO DO JARVIS MULTI-CLIENT AUDIO GATEWAY (LXC 110)    "
echo "================================================================="

# 1. Verificações Prévias
if [ "$(id -u)" -ne 0 ]; then
    echo "[ERRO] Este script deve ser executado como root no LXC 110." >&2
    exit 1
fi

if [ ! -f "$SRC_GATEWAY" ]; then
    echo "[ERRO] Código fonte do gateway não encontrado em: $SRC_GATEWAY" >&2
    exit 1
fi

if [ ! -d "$GATEWAY_DIR" ]; then
    echo "[ERRO] Diretório de produção não encontrado: $GATEWAY_DIR" >&2
    exit 1
fi

if [ ! -f "$ENV_FILE" ]; then
    echo "[ERRO] Arquivo de ambiente não encontrado: $ENV_FILE" >&2
    exit 1
fi

# 2. Verificação de Sintaxe Python do Novo Código
echo "[1/6] Validando sintaxe Python do novo audio_gateway.py..."
python3 -m py_compile "$SRC_GATEWAY"
echo "  -> Sintaxe OK."

# 3. Geração ou Validação de Token Individual
if [ -z "$MOBILE_TOKEN" ]; then
    # Gera um token criptograficamente seguro de 64 hexadecimais
    MOBILE_TOKEN="$(openssl rand -hex 32)"
    echo "[2/6] Gerado novo token individual para $MOBILE_DEVICE_ID."
else
    echo "[2/6] Utilizando token individual fornecido."
fi

# 4. Backup Timestamped
echo "[3/6] Criando backups de segurança..."
BACKUP_CODE="${GATEWAY_DIR}/audio_gateway.py.bak.${TIMESTAMP}"
BACKUP_ENV="${ENV_FILE}.bak.${TIMESTAMP}"

cp -p "${GATEWAY_DIR}/audio_gateway.py" "$BACKUP_CODE"
cp -p "$ENV_FILE" "$BACKUP_ENV"

cat <<EOF > "$BACKUP_MANIFEST"
BACKUP_TIMESTAMP=${TIMESTAMP}
BACKUP_CODE=${BACKUP_CODE}
BACKUP_ENV=${BACKUP_ENV}
EOF
chmod 0600 "$BACKUP_MANIFEST"
echo "  -> Backup do código: $BACKUP_CODE"
echo "  -> Backup do env:    $BACKUP_ENV"

# Função de Rollback Automático
executar_rollback() {
    echo "================================================================="
    echo "  [ALERTA] FALHA NA VALIDAÇÃO! INICIANDO ROLLBACK AUTOMÁTICO... "
    echo "================================================================="
    if [ -f "$BACKUP_CODE" ]; then
        cp -p "$BACKUP_CODE" "${GATEWAY_DIR}/audio_gateway.py"
        echo "  -> Código restaurado para a versão anterior."
    fi
    if [ -f "$BACKUP_ENV" ]; then
        cp -p "$BACKUP_ENV" "$ENV_FILE"
        chmod 0600 "$ENV_FILE"
        echo "  -> Variáveis de ambiente restauradas."
    fi
    systemctl restart "$SERVICE_NAME" || true
    echo "  -> Serviço reiniciado no estado pré-implantação."
    echo "[FALHA] Rollback concluído. Produção restaurada."
    exit 1
}

# 5. Atualização da Configuração de Tokens em audio-gateway.env
echo "[4/6] Configurando tokens permitidos em $ENV_FILE..."
python3 - <<PYEOF
import os, json, re

env_file = "$ENV_FILE"
device_id = "$MOBILE_DEVICE_ID"
token = "$MOBILE_TOKEN"

lines = []
with open(env_file, "r") as f:
    lines = f.readlines()

existing_tokens = {}
has_tokens_var = False
new_lines = []

for line in lines:
    stripped = line.strip()
    if stripped.startswith("JARVIS_AUDIO_TOKENS="):
        has_tokens_var = True
        val = stripped.split("=", 1)[1].strip().strip("'\"")
        try:
            parsed = json.loads(val)
            if isinstance(parsed, dict):
                existing_tokens.update(parsed)
        except Exception:
            pass
    else:
        new_lines.append(line)

# Mapeamento: token -> device_id
existing_tokens[token] = device_id
tokens_json = json.dumps(existing_tokens, separators=(',', ':'))

# Acrescenta a variável com aspas simples para segurança no shell
new_lines.append(f"JARVIS_AUDIO_TOKENS='{tokens_json}'\n")

with open(env_file, "w") as f:
    f.writelines(new_lines)
PYEOF

chmod 0600 "$ENV_FILE"
echo "  -> Token para $MOBILE_DEVICE_ID registrado com permissão 0600."

# 6. Atualização Atômica do Binário
echo "[5/6] Instalando nova versão do audio_gateway.py..."
TMP_TARGET="${GATEWAY_DIR}/audio_gateway.py.tmp.$$"
cp "$SRC_GATEWAY" "$TMP_TARGET"
chmod 0755 "$TMP_TARGET"
mv "$TMP_TARGET" "${GATEWAY_DIR}/audio_gateway.py"
echo "  -> Arquivo atualizado atomicamente."

# 7. Reinicialização e Health-Check Supervisionado
echo "[6/6] Reiniciando serviço e executando health-check..."
systemctl restart "$SERVICE_NAME" || executar_rollback

# Aguarda inicialização do socket
sleep 2

# Teste 1: Serviço Ativo
if ! systemctl is-active --quiet "$SERVICE_NAME"; then
    echo "[ERRO] Serviço $SERVICE_NAME não está ativo após reinicialização." >&2
    executar_rollback
fi

# Teste 2: Porta TCP 8088 em LISTEN
if ! ss -tlpn | grep -q ":8088"; then
    echo "[ERRO] Porta TCP 8088 não está em estado LISTEN." >&2
    executar_rollback
fi

# Teste 3: API HTTP de Diagnóstico na Porta 8089
HTTP_STATUS="$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8089/api/mics || echo "000")"
if [ "$HTTP_STATUS" != "200" ]; then
    echo "[ERRO] API de diagnóstico retornou HTTP $HTTP_STATUS (esperado 200)." >&2
    executar_rollback
fi

echo "================================================================="
echo "  IMPLANTAÇÃO MULTI-CLIENTE CONCLUÍDA COM 100% DE SUCESSO!       "
echo "================================================================="
echo "Serviço:             $SERVICE_NAME (Ativo)"
echo "Porta Áudio TCP:     8088 (LISTEN multi-cliente)"
echo "Porta Display HTTP:  8089 (/api/mics OK)"
echo "Device ID pareado:   $MOBILE_DEVICE_ID"
echo "Backup disponível:   $BACKUP_CODE"
echo "Manifesto salvo em:  $BACKUP_MANIFEST"
echo ""
echo "CREDENCIAIS DE PAREAMENTO DO APLICATIVO ANDROID:"
echo "-----------------------------------------------------------------"
echo "Servidor IP:         192.168.0.126"
echo "Porta Áudio:         8088"
echo "Porta Display:       8080"
echo "Device ID:           $MOBILE_DEVICE_ID"
echo "Token Secreto:       $MOBILE_TOKEN"
echo "TLS 1.2+:            Ativado (use_tls: true)"
echo "SHA-256 Fingerprint: 16D3EAD61DDB0C569275F3F68991798EC936D337378DCBEA73667519B632EE5C"
echo "-----------------------------------------------------------------"
echo "Script de rollback manual: $(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/rollback_gateway_multiclient.sh"
echo "================================================================="
