#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_NAME="Hermes 2.0.app"
BUILD_APP="$ROOT/dist/$APP_NAME"
DESKTOP_APP="$HOME/Desktop/$APP_NAME"
FORCE=0

if [[ "${1:-}" == "--force" ]]; then
  FORCE=1
fi

if [[ -e "$BUILD_APP" && "$FORCE" != "1" ]]; then
  echo "$BUILD_APP already exists. Re-run with --force to replace it." >&2
  exit 1
fi

if [[ -e "$DESKTOP_APP" && "$FORCE" != "1" ]]; then
  echo "$DESKTOP_APP already exists. Re-run with --force to replace it." >&2
  exit 1
fi

mkdir -p "$ROOT/dist"
rm -rf "$BUILD_APP"

if command -v npm >/dev/null 2>&1; then
  (cd "$ROOT/frontend" && npm run build)
fi

if ! command -v osacompile >/dev/null 2>&1; then
  echo "osacompile is required to create the desktop app." >&2
  exit 1
fi

osacompile -o "$BUILD_APP" -e 'do shell script quoted form of POSIX path of (path to resource "run-hermes2.sh")'
mkdir -p "$BUILD_APP/Contents/Resources"

cat > "$BUILD_APP/Contents/Resources/run-hermes2.sh" <<LAUNCHER
#!/bin/zsh
export HERMES2_REPO="$ROOT"
exec "$ROOT/scripts/hermes2-launch.sh"
LAUNCHER
chmod +x "$BUILD_APP/Contents/Resources/run-hermes2.sh"

/usr/libexec/PlistBuddy -c "Set :CFBundleDisplayName Hermes 2.0" "$BUILD_APP/Contents/Info.plist" >/dev/null 2>&1 || true
/usr/libexec/PlistBuddy -c "Set :CFBundleName Hermes 2.0" "$BUILD_APP/Contents/Info.plist" >/dev/null 2>&1 || true
/usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier local.hermes2.desktop" "$BUILD_APP/Contents/Info.plist" >/dev/null 2>&1 || true
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString 0.1.0" "$BUILD_APP/Contents/Info.plist" >/dev/null 2>&1 || true
/usr/libexec/PlistBuddy -c "Set :CFBundleVersion 1" "$BUILD_APP/Contents/Info.plist" >/dev/null 2>&1 || true

ICON_SRC="$BUILD_APP/Contents/Resources/hermes2-1024.png"
PYTHON_BIN=""
if command -v uv >/dev/null 2>&1; then
  PYTHON_BIN="$(cd "$ROOT" && uv run python -c 'import sys; print(sys.executable)' 2>/dev/null || true)"
fi
if [[ -z "$PYTHON_BIN" && -x /usr/bin/python3 ]]; then
  PYTHON_BIN="/usr/bin/python3"
fi

if [[ -n "$PYTHON_BIN" ]]; then
  "$PYTHON_BIN" - "$ICON_SRC" <<'PY'
import math
import struct
import sys
import zlib

path = sys.argv[1]
size = 1024
rows = []
for y in range(size):
    row = bytearray([0])
    for x in range(size):
        nx = (x / (size - 1)) - 0.5
        ny = (y / (size - 1)) - 0.5
        dist = math.sqrt(nx * nx + ny * ny)
        mask = 1.0 if max(abs(nx), abs(ny)) < 0.42 else max(0.0, 1.0 - (max(abs(nx), abs(ny)) - 0.42) * 20)
        r = int(25 + 32 * (1 - y / size))
        g = int(104 + 45 * (x / size))
        b = int(113 + 26 * (1 - dist))
        a = int(255 * mask)
        if abs(nx) < 0.08 or abs(ny) < 0.08:
            r = min(255, r + 32)
            g = min(255, g + 36)
            b = min(255, b + 34)
        row.extend([r, g, b, a])
    rows.append(bytes(row))

def chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

png = b"\x89PNG\r\n\x1a\n"
png += chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
png += chunk(b"IDAT", zlib.compress(b"".join(rows), 9))
png += chunk(b"IEND", b"")
with open(path, "wb") as handle:
    handle.write(png)
PY

  ICONSET="$BUILD_APP/Contents/Resources/hermes2.iconset"
  mkdir -p "$ICONSET"
  for size in 16 32 128 256 512; do
    /usr/bin/sips -z "$size" "$size" "$ICON_SRC" --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
    doubled=$((size * 2))
    /usr/bin/sips -z "$doubled" "$doubled" "$ICON_SRC" --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
  done
  /usr/bin/iconutil -c icns "$ICONSET" -o "$BUILD_APP/Contents/Resources/hermes2.icns" >/dev/null
  /usr/libexec/PlistBuddy -c "Set :CFBundleIconFile hermes2" "$BUILD_APP/Contents/Info.plist" >/dev/null 2>&1 || \
    /usr/libexec/PlistBuddy -c "Add :CFBundleIconFile string hermes2" "$BUILD_APP/Contents/Info.plist" >/dev/null 2>&1 || true
  rm -rf "$ICONSET"
fi

if command -v codesign >/dev/null 2>&1; then
  /usr/bin/codesign --force --deep --sign - "$BUILD_APP" >/dev/null
fi

rm -rf "$DESKTOP_APP"
cp -R "$BUILD_APP" "$DESKTOP_APP"
touch "$BUILD_APP" "$DESKTOP_APP"

echo "Created $BUILD_APP"
echo "Installed $DESKTOP_APP"
