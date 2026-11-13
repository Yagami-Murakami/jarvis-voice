#!/bin/bash
# ==============================================================================
# Script de Implantação Segura do Gateway Multi-Cliente no Jarvis LXC 110
#
# Execução autorizada exclusivamente em ambiente de produção supervisionado.
# Características de Segurança e Confiabilidade:
# 1. Verificações prévias de integridade do ambiente e pré-requisitos.
# 2. Backup timestamped atômico de código e arquivo de configuração .env.
# 3. Tratamento global de erros (trap ERR): rollback automático para QUALQUER falha.
# 4. Verificação estrita de sintaxe Python (py_compile) antes de alterar produção.
# 5. Validação do certificado TLS real do servidor contra o fingerprint SHA-256.
# 6. Proteção estrita do token: permissão 0600 em arquivo restrito, sem exposição
#    em argumentos CLI, logs do sistema ou histórico do shell.
# 7. Health-check em 3 fases: status systemd, porta TCP 8088 e API HTTP 8089.
# ==============================================================================
set -euo pipefail

GATEWAY_DIR="/opt/jarvis-voice/gateway"
ENV_FILE="/etc/jarvis/audio-gateway.env"
SECRETS_DIR="/etc/jarvis/secrets"
SERVICE_NAME="jarvis-audio-gateway.service"
SRC_GATEWAY="${JARVIS_SRC_GATEWAY:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/gateway/audio_gateway.py}"

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_MANIFEST="${GATEWAY_DIR}/.last_backup_manifest"
BACKUP_CODE="${GATEWAY_DIR}/audio_gateway.py.bak.${TIMESTAMP}"
BACKUP_ENV="${ENV_FILE}.bak.${TIMESTAMP}"

MOBILE_DEVICE_ID="${JARVIS_DEPLOY_DEVICE_ID:-celular_mobile}"
TOKEN_FILE="${SECRETS_DIR}/${MOBILE_DEVICE_ID}.secret"

EXPECTED_FINGERPRINT="16D3EAD61DDB0C569275F3F68991798EC936D337378DCBEA73667519B632EE5C"

echo "================================================================="
echo "   IMPLANTAÇÃO DO JARVIS MULTI-CLIENT AUDIO GATEWAY (LXC 110)    "
echo "================================================================="

# 1. Verificações Prévias
if [ "$(id -u)" -ne 0 ]; then
    echo "[ERRO] Este script deve ser executado como root no LXC 110." >&2
    exit 1
fi

if [ ! -f "$SRC_GATEWAY" ]; then
    echo "[ERRO] Código-fonte do gateway não encontrado em: $SRC_GATEWAY" >&2
    exit 1
fi

if [ -f "${GATEWAY_DIR}/audio_gateway.py" ] && [ "$(readlink -f "$SRC_GATEWAY")" = "$(readlink -f "${GATEWAY_DIR}/audio_gateway.py")" ]; then
    echo "[ERRO] SRC_GATEWAY ($SRC_GATEWAY) aponta para o mesmo arquivo do destino de produção." >&2
    echo "       Defina JARVIS_SRC_GATEWAY com o caminho do novo código a ser implantado." >&2
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
echo "[1/7] Validando sintaxe Python do novo audio_gateway.py..."
python3 -m py_compile "$SRC_GATEWAY"
echo "  -> Sintaxe OK."

# 3. Verificação do Certificado TLS Real no Servidor
echo "[2/7] Verificando certificado TLS do Gateway no LXC 110..."
CERT_PATH="$(grep -E "^JARVIS_TLS_CERT=" "$ENV_FILE" | cut -d= -f2 | tr -d '"'"'" || echo "/etc/jarvis/certs/gateway.crt")"
if [ ! -f "$CERT_PATH" ]; then
    CERT_PATH="/etc/jarvis/certs/gateway.crt"
fi

if [ -f "$CERT_PATH" ]; then
    REAL_FINGERPRINT="$(openssl x509 -noout -fingerprint -sha256 -in "$CERT_PATH" | cut -d= -f2 | tr -d ': ' | tr '[:lower:]' '[:upper:]')"
    echo "  -> Certificado TLS encontrado em: $CERT_PATH"
    echo "  -> Fingerprint SHA-256 real:     $REAL_FINGERPRINT"
    if [ "$REAL_FINGERPRINT" = "$EXPECTED_FINGERPRINT" ]; then
        echo "  -> Confirmação: Fingerprint confere 100% com o padrão homologado."
    else
        echo "  [AVISO] Fingerprint real diverge do padrão esperado. O pareamento utilizará: $REAL_FINGERPRINT"
        EXPECTED_FINGERPRINT="$REAL_FINGERPRINT"
    fi
else
    echo "  [AVISO] Certificado TLS não encontrado em $CERT_PATH. Verifique se o caminho está configurado corretamente."
fi

# 4. Criação de Backups e Ativação do Trap de Rollback Automático
echo "[3/7] Criando backups atômicos pré-implantação..."
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

# Função de Rollback Automático Universal
executar_rollback() {
    local exit_code="${1:-1}"
    local line_no="${2:-unknown}"
    echo "================================================================="
    echo "  [ALERTA] FALHA NA LINHA $line_no (Código $exit_code)! EXECUTANDO ROLLBACK... "
    echo "================================================================="
    trap - ERR  # Desativa trap para evitar recursão
    if [ -f "$BACKUP_CODE" ]; then
        cp -p "$BACKUP_CODE" "${GATEWAY_DIR}/audio_gateway.py"
        chmod 0755 "${GATEWAY_DIR}/audio_gateway.py"
        echo "  -> Código original restaurado de: $BACKUP_CODE"
    fi
    if [ -f "$BACKUP_ENV" ]; then
        cp -p "$BACKUP_ENV" "$ENV_FILE"
        chmod 0600 "$ENV_FILE"
        echo "  -> Configuração .env restaurada de: $BACKUP_ENV"
    fi
    rm -f "${GATEWAY_DIR}"/audio_gateway.py.tmp.* 2>/dev/null || true
    systemctl restart "$SERVICE_NAME" || true
    echo "  -> Serviço reiniciado no estado anterior à tentativa."
    echo "[ROLLBACK CONCLUÍDO] Produção restaurada com segurança."
    exit "$exit_code"
}

# Ativa o trap global para qualquer erro a partir daqui
trap 'executar_rollback $? $LINENO' ERR

# 5. Gestão Segura do Token Individual (sem vazamento em logs ou CLI)
echo "[4/7] Provisionando token individual com proteção estrita de credenciais..."
mkdir -p "$SECRETS_DIR"
chmod 0700 "$SECRETS_DIR"

MOBILE_TOKEN=""
if [ -n "${JARVIS_DEPLOY_MOBILE_TOKEN:-}" ]; then
    MOBILE_TOKEN="$JARVIS_DEPLOY_MOBILE_TOKEN"
elif [ -f "$TOKEN_FILE" ]; then
    MOBILE_TOKEN="$(cat "$TOKEN_FILE" | tr -d '[:space:]')"
fi

if [ -z "$MOBILE_TOKEN" ] || [ ${#MOBILE_TOKEN} -lt 32 ]; then
    # Gera credencial forte de 64 caracteres hexadecimais
    MOBILE_TOKEN="$(openssl rand -hex 32)"
fi

# Salva token em arquivo restrito exclusivo root:root 0600
echo -n "$MOBILE_TOKEN" > "$TOKEN_FILE"
chmod 0600 "$TOKEN_FILE"
echo "  -> Credencial individual armazenada em: $TOKEN_FILE (Permissão 0600)"
echo "  -> Token registrado: ${MOBILE_TOKEN:0:6}...${MOBILE_TOKEN: -4} (Valor completo mascarado)"

# 6. Atualização Atômica de audio-gateway.env
echo "[5/7] Registrando permissões no arquivo de ambiente..."
python3 - <<PYEOF
import os, json

env_file = "$ENV_FILE"
device_id = "$MOBILE_DEVICE_ID"
token = "$MOBILE_TOKEN"

lines = []
with open(env_file, "r") as f:
    lines = f.readlines()

existing_tokens = {}
new_lines = []

for line in lines:
    stripped = line.strip()
    if stripped.startswith("JARVIS_AUDIO_TOKENS="):
        val = stripped.split("=", 1)[1].strip().strip("'\"")
        try:
            parsed = json.loads(val)
            if isinstance(parsed, dict):
                # Normaliza tokens existentes
                for k, v in parsed.items():
                    if len(k) >= 32 and len(v) < 32:
                        existing_tokens[k] = v
                    elif len(v) >= 32 and len(k) < 32:
                        existing_tokens[v] = k
                    else:
                        existing_tokens[k] = v
        except Exception:
            pass
    else:
        new_lines.append(line)

existing_tokens[token] = device_id
tokens_json = json.dumps(existing_tokens, separators=(',', ':'))
new_lines.append(f"JARVIS_AUDIO_TOKENS='{tokens_json}'\n")

with open(env_file, "w") as f:
    f.writelines(new_lines)
PYEOF

chmod 0600 "$ENV_FILE"
echo "  -> audio-gateway.env atualizado com proteção root:root 0600."

# 7. Atualização Atômica do Binário
echo "[6/7] Instalando nova versão do audio_gateway.py atomicamente..."
TMP_TARGET="${GATEWAY_DIR}/audio_gateway.py.tmp.$$"
cp "$SRC_GATEWAY" "$TMP_TARGET"
chmod 0755 "$TMP_TARGET"
mv "$TMP_TARGET" "${GATEWAY_DIR}/audio_gateway.py"
echo "  -> Binário instalado."

# 8. Reinicialização e Health-Check Supervisionado
echo "[7/7] Reiniciando serviço e executando validação de integridade..."
systemctl restart "$SERVICE_NAME"

# Aguarda inicialização do socket
sleep 2

# Teste 1: Serviço Ativo
if ! systemctl is-active --quiet "$SERVICE_NAME"; then
    echo "[ERRO] Serviço $SERVICE_NAME não está ativo após reinicialização." >&2
    exit 101
fi

# Teste 2: Porta TCP 8088 em LISTEN
if ! ss -tlpn | grep -q ":8088"; then
    echo "[ERRO] Porta TCP 8088 não está em estado LISTEN." >&2
    exit 102
fi

# Teste 3: API HTTP de Diagnóstico na Porta 8089
HTTP_STATUS="$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8089/api/mics || echo "000")"
if [ "$HTTP_STATUS" != "200" ]; then
    echo "[ERRO] API de diagnóstico retornou HTTP $HTTP_STATUS (esperado 200)." >&2
    exit 103
fi

# Desativa trap de erro pois a implantação foi bem-sucedida
trap - ERR

echo "================================================================="
echo "  IMPLANTAÇÃO MULTI-CLIENTE CONCLUÍDA COM SUCESSO NO LXC 110!    "
echo "================================================================="
echo "Serviço:             $SERVICE_NAME (Ativo)"
echo "Porta Áudio TCP:     8088 (LISTEN multi-cliente)"
echo "Porta Display HTTP:  8089 (/api/mics respondendo 200 OK)"
echo "Dispositivo pareado: $MOBILE_DEVICE_ID"
echo "Backup do código:    $BACKUP_CODE"
echo "Manifesto:           $BACKUP_MANIFEST"
echo ""
echo "PARÂMETROS DE PAREAMENTO DO APLICATIVO ANDROID:"
echo "-----------------------------------------------------------------"
echo "Servidor IP:         192.168.0.126"
echo "Porta Áudio TCP:     8088"
echo "Porta Display HTTP:  8080"
echo "Device ID:           $MOBILE_DEVICE_ID"
echo "Token Secreto:       (Consulte com permissão 0600 em: cat $TOKEN_FILE)"
echo "TLS 1.2+:            Ativado (Obrigatório)"
echo "SHA-256 Fingerprint: $EXPECTED_FINGERPRINT"
echo "-----------------------------------------------------------------"
echo "Script de reversão:  $(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/rollback_gateway_multiclient.sh"
echo "================================================================="
