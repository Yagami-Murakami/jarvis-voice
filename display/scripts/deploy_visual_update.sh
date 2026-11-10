#!/usr/bin/env bash
# ==============================================================================
# Script de Backup e Publicação das Alterações Visuais (HTML/CSS) no LXC 110
# ==============================================================================
set -euo pipefail

LXC_HOST="192.168.0.126"
LXC_TARGET_DIR="/opt/jarvis-voice/display/static"
BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "=== Jarvis Voice Display — Atualização Visual ==="
echo "Origem local: $BASE_DIR/static/"
echo "Destino LXC:  root@$LXC_HOST:$LXC_TARGET_DIR/"
echo ""

echo "[1/3] Realizando backup no LXC 110..."
ssh root@"$LXC_HOST" "
  set -e
  cp -pv '$LXC_TARGET_DIR/index.html' '$LXC_TARGET_DIR/index.html.bak'
  cp -pv '$LXC_TARGET_DIR/style.css' '$LXC_TARGET_DIR/style.css.bak'
"

echo ""
echo "[2/3] Publicando exclusivamente os dois arquivos visuais..."
scp "$BASE_DIR/static/index.html" root@"$LXC_HOST":"$LXC_TARGET_DIR/index.html"
scp "$BASE_DIR/static/style.css" root@"$LXC_HOST":"$LXC_TARGET_DIR/style.css"

echo ""
echo "[3/3] Verificando versão servida pelo LXC 110..."
curl -s "http://$LXC_HOST:8080/" | grep -E "style\.css\?v=|ambient-label" || true

echo ""
echo "=== Publicação concluída com sucesso! ==="
