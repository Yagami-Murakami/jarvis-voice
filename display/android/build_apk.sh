#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SDK_ROOT="/home/anakin/tools/android-sdk"
BUILD_TOOLS="$SDK_ROOT/build-tools/34.0.0"
PLATFORM="$SDK_ROOT/platforms/android-34/android.jar"
KEYSTORE="/home/anakin/.android/debug.keystore"

AAPT2="$BUILD_TOOLS/aapt2"
D8="$BUILD_TOOLS/d8"
ZIPALIGN="$BUILD_TOOLS/zipalign"
APKSIGNER="$BUILD_TOOLS/apksigner"

echo "=== COMPILANDO JARVIS VOICE DISPLAY APK ==="
rm -rf "$DIR/build"
mkdir -p "$DIR/bin" "$DIR/build/obj"

# 1. Compila recursos
echo "[1/6] Compilando recursos com AAPT2..."
$AAPT2 compile --dir "$DIR/res" -o "$DIR/build/resources.zip"

# 2. Linka recursos e gera R.java
echo "[2/6] Linkando recursos..."
$AAPT2 link -I "$PLATFORM" \
    --manifest "$DIR/AndroidManifest.xml" \
    -o "$DIR/build/unaligned.apk" \
    "$DIR/build/resources.zip" \
    --java "$DIR/build/gen"

# 3. Compila código Java (Java 8 bytecode para o D8)
echo "[3/6] Compilando Java com javac..."
javac -g -source 8 -target 8 \
    -cp "$PLATFORM:$DIR/build/gen" \
    -d "$DIR/build/obj" \
    "$DIR/src/com/jarvis/display/"*.java \
    $(find "$DIR/build/gen" -name "*.java" 2>/dev/null || true)

# 4. Converte bytecode para classes.dex com D8
echo "[4/6] Gerando classes.dex com D8..."
$D8 --output "$DIR/build" \
    --lib "$PLATFORM" \
    $(find "$DIR/build/obj" -name "*.class")

# 5. Adiciona classes.dex ao APK usando Python (portável e sem dependência de zip)
echo "[5/6] Adicionando DEX ao APK..."
python3 -c "
import zipfile
with zipfile.ZipFile('$DIR/build/unaligned.apk', 'a') as z:
    z.write('$DIR/build/classes.dex', 'classes.dex')
"

# 6. Alinha e assina o APK
echo "[6/6] Zipalign e assinatura com apksigner..."
$ZIPALIGN -f -p 4 "$DIR/build/unaligned.apk" "$DIR/bin/JarvisDisplay.apk"
$APKSIGNER sign --ks "$KEYSTORE" \
    --ks-pass pass:android \
    --key-pass pass:android \
    "$DIR/bin/JarvisDisplay.apk"

echo "=== SUCESSO! APK GERADO EM: ==="
ls -lh "$DIR/bin/JarvisDisplay.apk"
