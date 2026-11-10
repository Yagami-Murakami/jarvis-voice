#!/usr/bin/env bash
# Ananuke: backup e publicação somente dos arquivos visuais do Jarvis Display.
set -euo pipefail

TARGET="root@192.168.0.126"
BASE="/opt/jarvis-voice/display"
IP="192.168.0.126"
BRANCH="feat/jarvis-voice-display"
STAMP="$(date +%Y%m%d-%H%M%S)"
STAGE="/tmp/jarvis-visual-${STAMP}-$$"
BACKUP="${BASE}/backups/visual-${STAMP}"

fail() { echo "ERRO: $*" >&2; exit 1; }
for cmd in git ssh scp sha256sum curl adb; do
  command -v "$cmd" >/dev/null 2>&1 || fail "Comando não encontrado: $cmd"
done

ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || fail "Execute dentro do clone jarvis-voice"
cd "$ROOT"
[ "$(git branch --show-current)" = "$BRANCH" ] || fail "Selecione a branch $BRANCH antes de publicar"
CSS="$ROOT/display/static/style.css"
HTML="$ROOT/display/static/index.html"
[ -f "$CSS" ] && [ -f "$HTML" ] || fail "Arquivos HTML/CSS não encontrados"
git diff --quiet -- "$CSS" "$HTML" || fail "Alterações locais nos arquivos visuais; revise/commite antes"
git diff --cached --quiet -- "$CSS" "$HTML" || fail "Alterações staged nos arquivos visuais; revise/commite antes"

grep -q 'font-size: 5.5rem' "$CSS" || fail "CSS sem relógio ampliado; atualize a branch"
grep -q 'font-size: 3.2rem' "$CSS" || fail "CSS sem temperatura ampliada; atualize a branch"
grep -q 'SISTEMA<span class="ambient-label-line">ATIVO</span>' "$HTML" || fail "HTML sem texto central corrigido"

CSS_SHA="$(sha256sum "$CSS" | cut -d ' ' -f 1)"
HTML_SHA="$(sha256sum "$HTML" | cut -d ' ' -f 1)"

echo "=== Verificando servidor e fazendo backup ==="
ssh "$TARGET" "test -f '$BASE/static/index.html' && test -f '$BASE/static/style.css'" \
  || fail "Arquivos remotos não encontrados; nenhuma alteração foi feita"
ssh "$TARGET" "mkdir -p '$STAGE' '$BACKUP' && cp -a '$BASE/static/index.html' '$BACKUP/index.html' && cp -a '$BASE/static/style.css' '$BACKUP/style.css'" \
  || fail "Falha ao fazer backup. Nenhum arquivo visual foi substituído"
echo "Backup remoto: $BACKUP"
scp -q "$HTML" "$CSS" "$TARGET:$STAGE/" || fail "Falha no upload; backup mantido"

echo "=== Verificando integridade e atualizando SOMENTE HTML/CSS ==="
ssh "$TARGET" bash -s -- "$STAGE" "$BACKUP" "$HTML_SHA" "$CSS_SHA" <<'REMOTE'
set -euo pipefail
STAGE="$1"; BACKUP="$2"; HTML_SHA="$3"; CSS_SHA="$4"
BASE="/opt/jarvis-voice/display/static"
printf '%s  %s\n' "$HTML_SHA" "$STAGE/index.html" | sha256sum -c -
printf '%s  %s\n' "$CSS_SHA" "$STAGE/style.css" | sha256sum -c -
if ! { install -m 0644 "$STAGE/index.html" "$BASE/index.html" &&
       install -m 0644 "$STAGE/style.css" "$BASE/style.css" &&
       printf '%s  %s\n' "$HTML_SHA" "$BASE/index.html" | sha256sum -c - &&
       printf '%s  %s\n' "$CSS_SHA" "$BASE/style.css" | sha256sum -c -; }; then
  echo "Falha na cópia: restaurando HTML e CSS do backup."
  cp -a "$BACKUP/index.html" "$BASE/index.html"
  cp -a "$BACKUP/style.css" "$BASE/style.css"
  exit 1
fi
rm -rf -- "$STAGE"
REMOTE

echo "=== Verificando API e conteúdo HTTP ==="
curl --noproxy '*' -fsS --max-time 6 "http://$IP:8080/api/status" >/dev/null \
  || fail "API não respondeu; backup em $BACKUP"
PAGE="$(curl --noproxy '*' -fsS --max-time 6 "http://$IP:8080/index.html")" \
  || fail "Página não respondeu"
grep -q 'ambient-label-line' <<< "$PAGE" || fail "Servidor não está entregando HTML atualizado"

echo "=== Recarregando aplicativo no LG (se ADB disponível) ==="
if adb devices | awk 'NR>1 && $2=="device" {found=1} END {exit !found}'; then
  adb shell am force-stop com.jarvis.display
  adb shell am start -n com.jarvis.display/.MainActivity
  sleep 5
  EVID="${HOME}/Jarvis-Display-Evidencias"
  mkdir -p "$EVID"
  SHOT="$EVID/lg-x230-visual-${STAMP}.png"
  adb exec-out screencap -p > "$SHOT"
  echo "Captura real do LG: $SHOT"
else
  echo "ADB sem dispositivo autorizado; capture a tela posteriormente."
fi

echo "=== CONCLUÍDO: HTML e CSS atualizados. Nenhum serviço reiniciado. ==="
echo "Backup para restauração: $BACKUP"
echo "Teste agora falando 'Assistente' e confira animação e som."
