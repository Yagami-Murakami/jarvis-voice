#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ANDROID_PROJECT="$DIR/android"
GRADLE_BIN="/home/anakin/tools/gradle-8.2/bin/gradle"
SDK_ROOT="/home/anakin/tools/android-sdk"
OUTPUT_DIR="$DIR/bin"

echo "================================================="
echo "  COMPILANDO JARVIS VOICE MOBILE (APK ANDROID)   "
echo "================================================="

if [ ! -f "$ANDROID_PROJECT/local.properties" ]; then
    echo "Configurando local.properties..."
    echo "sdk.dir=$SDK_ROOT" > "$ANDROID_PROJECT/local.properties"
fi

cd "$ANDROID_PROJECT"

echo "[1/3] Executando Gradle assembleDebug..."
"$GRADLE_BIN" assembleDebug

echo "[2/3] Copiando APK para $OUTPUT_DIR..."
mkdir -p "$OUTPUT_DIR"
cp "$ANDROID_PROJECT/app/build/outputs/apk/debug/app-debug.apk" "$OUTPUT_DIR/JarvisVoiceMobile-v1.0.0.apk"

echo "[3/3] Verificando integridade e assinatura do APK..."
"$SDK_ROOT/build-tools/34.0.0/apksigner" verify "$OUTPUT_DIR/JarvisVoiceMobile-v1.0.0.apk"

echo "================================================="
echo "  SUCESSO! APK GERADO COM SUCESSO:               "
echo "  $OUTPUT_DIR/JarvisVoiceMobile-v1.0.0.apk"
echo "================================================="
ls -lh "$OUTPUT_DIR/JarvisVoiceMobile-v1.0.0.apk"
