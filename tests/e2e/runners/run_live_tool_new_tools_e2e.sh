#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
PY_BIN="${OPENMINION_PYTHON:-$ROOT/.venv/bin/python3.11}"

if [[ ! -x "$PY_BIN" ]]; then
  echo "python binary not found: $PY_BIN" >&2
  exit 1
fi

export OPENMINION_LIVE_TOOL_E2E_NEW_TOOLS=1
export PYTHONPATH="${PYTHONPATH:-$ROOT/src}"
export OPENMINION_HOME="${OPENMINION_HOME:-$(cd "$ROOT/.." && pwd)}"

exec "$PY_BIN" -m pytest -q "$ROOT/tests/e2e/test_live_tool_new_tools_openrouter_matrix.py" "$@"
