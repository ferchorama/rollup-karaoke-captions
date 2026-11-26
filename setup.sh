#!/usr/bin/env bash
# macOS / Linux entry point. Installs uv (user-space) if needed, then runs scripts/bootstrap.py.
# No sudo, no shell rc edits, no system packages.
set -euo pipefail
SKILL="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOLS="$SKILL/.tools"
UV="$TOOLS/uv/uv"
if [ ! -x "$UV" ]; then
  if [ -x "$TOOLS/uv/bin/uv" ]; then UV="$TOOLS/uv/bin/uv"
  elif command -v uv >/dev/null 2>&1; then UV="$(command -v uv)"
  else
    echo "[setup] installing uv into .tools/uv"
    mkdir -p "$TOOLS/uv"
    curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$TOOLS/uv" UV_NO_MODIFY_PATH=1 sh
    UV="$TOOLS/uv/uv"
    [ -x "$UV" ] || UV="$TOOLS/uv/bin/uv"
  fi
fi
export UV_CACHE_DIR="$TOOLS/cache/uv"
export UV_PYTHON_INSTALL_DIR="$TOOLS/python"
export UV_LINK_MODE=copy
mkdir -p "$UV_CACHE_DIR" "$UV_PYTHON_INSTALL_DIR"
exec "$UV" run --python 3.12 --python-preference only-managed --no-project -- "$SKILL/scripts/bootstrap.py" "$@"
