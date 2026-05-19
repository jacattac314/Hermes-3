#!/bin/zsh
set -euo pipefail

FORCE=0
if [[ "${1:-}" == "--force" ]]; then
  FORCE=1
fi

SCRIPT_DIR="${0:A:h}"
REPO_ROOT="${SCRIPT_DIR:h}"
TARGET_DIR="$HOME/.hermes/bin"
TARGET="$TARGET_DIR/hermes2ctl"
UV_BIN="${UV_BIN:-$HOME/.local/bin/uv}"

if [[ ! -x "$UV_BIN" ]]; then
  echo "uv not found at $UV_BIN. Set UV_BIN or install uv first." >&2
  exit 1
fi

mkdir -p "$TARGET_DIR"
chmod 700 "$TARGET_DIR"

if [[ -e "$TARGET" && "$FORCE" != "1" ]]; then
  echo "$TARGET already exists. Re-run with --force to overwrite." >&2
  exit 2
fi

tmp="$(mktemp)"
cat > "$tmp" <<EOF
#!/bin/zsh
set -euo pipefail
cd "$REPO_ROOT"
exec "$UV_BIN" run hermes2 "\$@"
EOF

install -m 700 "$tmp" "$TARGET"
rm -f "$tmp"

echo "Installed $TARGET"
