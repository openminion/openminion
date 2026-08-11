#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
source "$(dirname "${BASH_SOURCE[0]}")/../../helpers/runtime_roots.sh"
isolate_openminion_test_roots openminion-live-cli-chat-matrix
PY_BIN="${OPENMINION_PYTHON:-$ROOT/.venv/bin/python3.11}"

if [[ ! -x "$PY_BIN" ]]; then
  echo "python binary not found: $PY_BIN" >&2
  exit 1
fi

export OPENMINION_LIVE_CLI_CHAT_E2E=1
export OPENMINION_TRACE_REQUESTS=1
export PYTHONPATH="${PYTHONPATH:-$ROOT/src}"

exec "$PY_BIN" -m pytest -q "$ROOT/tests/e2e/test_live_cli_chat_alibaba_minimax_matrix.py" "$@"
