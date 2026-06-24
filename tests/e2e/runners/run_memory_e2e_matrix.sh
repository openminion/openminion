#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
DEFAULT_EVAL_ROOT="${REPO_ROOT}/.deps/openminion-eval"
if [[ ! -d "${DEFAULT_EVAL_ROOT}" ]]; then
  DEFAULT_EVAL_ROOT="$(cd "${REPO_ROOT}/.." && pwd)/openminion-eval"
fi
OPENMINION_EVAL_ROOT="${OPENMINION_EVAL_ROOT:-${DEFAULT_EVAL_ROOT}}"

cd "${REPO_ROOT}"

PYTHON_BIN="${OPENMINION_PY:-.venv/bin/python3.11}"
if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "error: python executable not found: ${PYTHON_BIN}" >&2
  exit 1
fi
OPENMINION_PY_BIN="$(cd "$(dirname "${PYTHON_BIN}")" && pwd)/$(basename "${PYTHON_BIN}")"

echo "[memory-e2e-matrix] Phase 7b operability slice"
PYTHONPATH=src "${PYTHON_BIN}" -m pytest \
  tests/memory/test_operability.py \
  tests/integration/test_operability.py \
  -q --tb=short

echo "[memory-e2e-matrix] Phase 7 E2E slice"
PYTHONPATH=src "${PYTHON_BIN}" -m pytest \
  tests/helpers/test_memory_e2e_helpers.py \
  tests/integration/test_e2e_full_lifecycle.py \
  tests/integration/test_e2e_reflection_to_capsule.py \
  tests/integration/test_e2e_candidate_contradiction.py \
  tests/integration/test_e2e_disuse_decay_recall.py \
  tests/integration/test_e2e_scope_isolation.py \
  tests/integration/test_e2e_paraphrase_capsule.py \
  tests/integration/test_e2e_preference_stability.py \
  tests/integration/test_e2e_feature_matrix.py \
  -q --tb=short

echo "[memory-e2e-matrix] Memory regression slice"
PYTHONPATH=src "${PYTHON_BIN}" -m pytest \
  tests/services/agent/test_memory_gateway_adapter.py \
  tests/services/agent/test_memory_long_term.py \
  tests/services/agent/test_continuity_hardening.py \
  tests/integration/test_continuity_hardening.py \
  tests/services/agent/test_typed_durable_memory.py \
  tests/integration/test_typed_durable_memory.py \
  tests/services/agent/test_ranking_unification.py \
  tests/integration/test_ranking_unification.py \
  tests/services/agent/test_candidate_first_learning.py \
  tests/integration/test_candidate_first_learning.py \
  tests/services/agent/test_truth_maintenance.py \
  tests/integration/test_truth_maintenance.py \
  tests/services/agent/test_reflection_promotion.py \
  tests/integration/test_reflection_promotion.py \
  -q --tb=short

# Memory eval / eval-core regression slice now lives in the standalone openminion-eval package.
if [[ ! -d "${OPENMINION_EVAL_ROOT}" ]]; then
  echo "error: openminion-eval checkout not found: ${OPENMINION_EVAL_ROOT}" >&2
  echo "set OPENMINION_EVAL_ROOT to a valid openminion-eval repo root" >&2
  exit 1
fi

echo "[memory-e2e-matrix] openminion-eval memory regression slice"
(cd "${OPENMINION_EVAL_ROOT}" && \
  PYTHONPATH=src "${OPENMINION_PY_BIN}" -m pytest \
    tests/eval/test_memory_eval.py \
    tests/eval/test_eval.py \
    tests/eval/test_interfaces_contract.py \
    -q --tb=short)

echo "[memory-e2e-matrix] Eval baseline regression check"
(cd "${OPENMINION_EVAL_ROOT}" && \
  PYTHONPATH=src "${OPENMINION_PY_BIN}" tests/eval/runners/run_memory_eval_baseline.py --check)

echo "[memory-e2e-matrix] All checks passed"
