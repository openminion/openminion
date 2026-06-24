#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
PY="${OPENMINION_PY:-$ROOT/.venv/bin/python3.11}"

if [[ ! -x "$PY" ]]; then
  echo "error: python interpreter not found at $PY" >&2
  exit 1
fi

cd "$ROOT"
PYTHONPATH=src "$PY" -m pytest -q \
  tests/runtime/test_module_cli_exemptions_policy.py \
  tests/runtime/test_cli_main_signature_contract.py \
  tests/runtime/test_module_cli_guards.py \
  tests/runtime/test_module_cli_inventory_contract.py \
  tests/runtime/test_module_cli_debug_command_map.py \
  tests/runtime/test_module_main_delegation.py \
  tests/runtime/test_module_cli_helper_adoption.py
