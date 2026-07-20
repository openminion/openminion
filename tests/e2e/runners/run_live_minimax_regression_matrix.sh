#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
PY_BIN="${OPENMINION_PYTHON:-$ROOT/.venv/bin/python3.11}"
MODE="${1:-tier1}"

if [[ ! -x "$PY_BIN" ]]; then
  echo "python binary not found: $PY_BIN" >&2
  exit 1
fi

shift || true

export OPENMINION_LIVE_CLI_CHAT_E2E="${OPENMINION_LIVE_CLI_CHAT_E2E:-1}"
export OPENMINION_LIVE_TOOL_E2E="${OPENMINION_LIVE_TOOL_E2E:-1}"
export OPENMINION_HOME="${OPENMINION_HOME:-$ROOT}"
export OPENMINION_DATA_ROOT="${OPENMINION_DATA_ROOT:-$OPENMINION_HOME/.openminion}"
export PYTHONPATH="${PYTHONPATH:-$ROOT/src}"

run_pytest() {
  "$PY_BIN" -m pytest -q "$@"
}

run_gate() {
  "$PY_BIN" "$ROOT/tests/e2e/runners/run_cli_e2e_gate.py" live
}

run_official_core() {
  run_pytest "$ROOT/tests/e2e/test_live_cli_chat_minimax_official_"*.py "$@"
}

run_skills() {
  run_pytest \
    "$ROOT/tests/e2e/test_live_skill_cli_smoke.py" \
    "$ROOT/tests/e2e/test_live_skill_dense_catalog_matrix.py" \
    "$ROOT/tests/e2e/test_live_skill_model_matrix.py" \
    "$@"
}

run_tasking() {
  run_pytest \
    "$ROOT/tests/e2e/test_live_cli_chat_minimax_official_task_cron.py" \
    "$ROOT/tests/e2e/test_live_cli_chat_minimax_official_ctgp_autonomous_plan.py" \
    "$ROOT/tests/e2e/test_live_cli_chat_minimax_official_ppl_proof_of_life.py" \
    "$ROOT/tests/e2e/test_live_cli_chat_minimax_official_aib_scaling.py" \
    "$@"
}

run_coding_research() {
  run_pytest \
    "$ROOT/tests/e2e/test_live_cli_chat_minimax_official_complex_task_integrity.py" \
    "$ROOT/tests/e2e/test_live_cli_chat_minimax_official_coding_project.py" \
    "$ROOT/tests/e2e/test_live_cli_chat_minimax_official_tool_matrix.py" \
    "$ROOT/tests/e2e/test_live_tool_profile_matrix.py" \
    "$@"
}

run_long() {
  run_pytest \
    "$ROOT/tests/e2e/test_live_cli_chat_minimax_official_task_cron.py" \
    "$ROOT/tests/e2e/test_live_cli_chat_minimax_official_complex_task_integrity.py" \
    "$ROOT/tests/e2e/test_live_cli_chat_minimax_official_ctgp_autonomous_plan.py" \
    "$ROOT/tests/e2e/test_live_cli_chat_minimax_official_coding_project.py" \
    "$@"
}

case "$MODE" in
  gate)
    run_gate "$@"
    ;;
  core)
    run_official_core "$@"
    ;;
  skills)
    run_skills "$@"
    ;;
  tasking)
    run_tasking "$@"
    ;;
  coding-research)
    run_coding_research "$@"
    ;;
  long)
    run_long "$@"
    ;;
  tier0)
    run_gate
    run_official_core "$@"
    ;;
  tier1)
    run_gate
    run_official_core "$@"
    run_skills "$@"
    run_tasking "$@"
    run_coding_research "$@"
    ;;
  tier2)
    run_gate
    run_official_core "$@"
    run_skills "$@"
    run_tasking "$@"
    run_coding_research "$@"
    run_long "$@"
    ;;
  *)
    cat >&2 <<'EOF'
usage: run_live_minimax_regression_matrix.sh [mode]

modes:
  gate              run canonical CLI and Focus gate
  core              run official MiniMax wildcard matrix
  skills            run live skill matrix
  tasking           run task/cron/autonomous/progress matrix
  coding-research   run complex-task + tool matrices
  long              run explicit >=10 minute lane
  tier0             gate + official core
  tier1             gate + core + skills + tasking + coding/research
  tier2             tier1 + explicit long-running lane
EOF
    exit 2
    ;;
esac
