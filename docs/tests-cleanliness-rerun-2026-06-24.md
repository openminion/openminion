# OpenMinion Tests Cleanliness Rerun 2026-06-24

Status: active
Last updated: 2026-06-24

Purpose: record the refreshed `openminion/tests` cleanliness rerun against the
current live tree without mixing the scratch execution artifacts into the
package repo.

## Scope

This rerun refreshed the closed `2026-06-22` tests-cleanliness lane against the
current `openminion/tests` tree.

The live rerun artifacts remain under the workspace scratch root:

1. `workspace-tmp/openminion-tests-cleanliness-rerun-2026-06-24/latest-test-files.current.txt`
2. `workspace-tmp/openminion-tests-cleanliness-rerun-2026-06-24/per-file-ledger.tsv`
3. `workspace-tmp/openminion-tests-cleanliness-rerun-2026-06-24/summary.md`

## Current proven state

1. live test inventory: `1666` files
2. ledger rows: `1666`
3. path order matches the frozen inventory exactly
4. blank dispositions: `0`
5. remaining rows pending rerun review: `0`

Disposition totals in the refreshed ledger:

1. `301` `trim`
2. `1086` `keep`
3. `279` `defer-later:*`

## Drift reconciled in this rerun

This rerun carried forward the prior closed lane, then reconciled current-tree
drift:

1. `53` rename carry-forwards, mainly `tests/cli/tui/focus_terminal/...` to
   `tests/cli/tui/terminal/...`
2. `4` truly new files reviewed directly:
   1. `tests/brain/loop/test_goal_access.py`
   2. `tests/cli/tui/presentation/test_slash_commands_contract.py`
   3. `tests/scripts/test_validate_path_structure_hygiene.py`
   4. `tests/test_version_command.py`
3. `5` currently modified files re-reviewed against the live tree:
   1. `tests/cli/tui/test_token_usage_status.py`
   2. `tests/services/agent/test_runtime_client_structured_tool_choice.py`
   3. `tests/services/brain/test_llm_wrapper.py`
   4. `tests/services/brain/test_post_execution.py`
   5. `tests/services/gateway/test_gateway_service.py`

## Validation

Focused unchanged validation over the `4` true-new files plus the `5` currently
modified files:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python3.11 -m py_compile ...
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python3.11 -m ruff check ...
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python3.11 -m pytest -q ...
```

Results:

1. `py_compile`: passed
2. file-scoped Ruff: passed
3. focused pytest: `137 passed, 2 failed`

The two focused pytest failures were unchanged baseline drift in
`tests/services/gateway/test_gateway_service.py`, where
`_SequenceTextProvider` no longer satisfies the abstract provider contract.
That file remains explicitly recorded as:

1. `defer-later:preexisting-source-contract-drift`

Repo gates after the rerun:

```bash
.venv/bin/python3.11 -m ruff check .
make lint
```

Results:

1. repo-wide Ruff: passed
2. `make lint`: passed

## Closeout read

This rerun did not land new source edits. Its deliverable was refreshed live
proof:

1. fresh inventory regenerated
2. carry-forward ledger normalized to current paths
3. current drift files re-reviewed
4. current summary and ledger synchronized
5. remaining count verified at zero

Use this doc as the repo-owned pointer to the current scratch artifacts when a
future `openminion/tests` cleanup pass resumes.
