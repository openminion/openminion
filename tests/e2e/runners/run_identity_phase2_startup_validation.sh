#!/usr/bin/env bash
set -euo pipefail

OPENMINION_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
ARTIFACT_ROOT="${OPENMINION_TEST_ARTIFACT_ROOT:-${TMPDIR:-/tmp}/openminion-test-artifacts}"
ARTIFACT_DIR="${1:-$ARTIFACT_ROOT/openminion-identity-phase2-validation}"
STAMP="$(date +%Y%m%d-%H%M%S)"

mkdir -p "$ARTIFACT_DIR"
cd "$OPENMINION_DIR"

PY=.venv/bin/python3.11
test -x "$PY" || python3.11 -m venv .venv

PYTHONPATH=src "$PY" -m pytest -q \
  tests/test_env_registry.py \
  tests/test_config_helpers.py \
  tests/identity/test_identity.py \
  tests/services/identity/test_runtime.py \
  2>&1 | tee "$ARTIFACT_DIR/iaic-207-phase2-suite-$STAMP.log"

PYTHONPATH=src "$PY" -m pytest -q -k "identity and (identity_root or agent_dir or startup_sync or default_profile)" \
  2>&1 | tee "$ARTIFACT_DIR/iaic-207-focused-startup-$STAMP.log"

PYTHONPATH=src "$PY" -m pytest -q \
  tests/test_tool_registry.py \
  tests/test_tool_registry_manager.py \
  tests/test_tool_contracts_invariants.py \
  tests/test_llm_bridge_normalization.py \
  tests/test_tool_calling_minimax.py \
  tests/test_channel_envelope_regression.py \
  2>&1 | tee "$ARTIFACT_DIR/iaic-207-baseline-regression-$STAMP.log"

(
  unset OPENMINION_DATA_ROOT OPENMINION_TRACE_REQUESTS_DIR
  OPENMINION_HOME="${OPENMINION_HOME:-$OPENMINION_DIR}" \
  OPENMINION_TRACE_REQUESTS=1 \
  PYTHONPATH=src .venv/bin/python3.11 -m openminion \
    --config test-configs/per-agent-alibaba-minimax.json \
    chat --agent alibaba-minimax --session identity-authority-interop-redo --quiet --no-progress <<'EOCHAT'
hello
/exit
EOCHAT
) 2>&1 | tee "$ARTIFACT_DIR/iaic-207-minimax-e2e-$STAMP.log"

PYTHONPATH=src .venv/bin/python3.11 -m openminion status tools --json \
  2>&1 | tee "$ARTIFACT_DIR/iaic-207-status-tools-$STAMP.log"
