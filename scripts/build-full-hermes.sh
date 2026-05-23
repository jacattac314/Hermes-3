#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
REPO_ROOT="${SCRIPT_DIR:h}"
SOURCE_DIR="${HERMES_AGENT_SOURCE:-$HOME/.hermes/hermes-agent}"
OUT_DIR="${HERMES_FULL_DIST_DIR:-$REPO_ROOT/dist/full-hermes}"

if [[ ! -d "$SOURCE_DIR" ]]; then
  echo "Hermes Agent source not found: $SOURCE_DIR" >&2
  exit 1
fi

if [[ ! -f "$SOURCE_DIR/pyproject.toml" ]]; then
  echo "Not a Python project: $SOURCE_DIR" >&2
  exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required to build Hermes Agent." >&2
  exit 1
fi

mkdir -p "$OUT_DIR"

(
  cd "$SOURCE_DIR"
  rm -rf dist build
  uv build
)

cp "$SOURCE_DIR"/dist/hermes_agent-*.whl "$OUT_DIR"/
cp "$SOURCE_DIR"/dist/hermes_agent-*.tar.gz "$OUT_DIR"/

echo "Copied Hermes Agent artifacts to $OUT_DIR"
shasum -a 256 "$OUT_DIR"/hermes_agent-*.whl "$OUT_DIR"/hermes_agent-*.tar.gz
