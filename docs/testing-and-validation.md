# OpenMinion Testing And Validation

Status: active
Last updated: 2026-06-30

Purpose: give package users and maintainers one package-local reference for the
basic validation commands that prove `openminion` installs and runs correctly.

## Install baseline

OpenMinion currently expects:

1. Python 3.11 or newer
2. a recent `pip` that supports PEP 660 editable installs

Recommended local setup from the package root:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e .
```

## First-user smoke flow

The smallest public CLI proof uses the offline `echo` provider and a temporary
config.

Important: global CLI flags such as `--config`, `--home-root`, and
`--data-root` belong before the subcommand.

From the package root:

```bash
tmpdir="$(mktemp -d)"
export OPENMINION_HOME="$tmpdir/home"
export OPENMINION_DATA_ROOT="$OPENMINION_HOME/.openminion"
mkdir -p "$OPENMINION_HOME"

python3.11 -m openminion --config "$tmpdir/config.json" config init --provider echo --force
python3.11 -m openminion --config "$tmpdir/config.json" verify smoke
python3.11 -m openminion --config "$tmpdir/config.json" doctor --json
python3.11 -m openminion --config "$tmpdir/config.json" agent --message "hello"
```

Expected outcomes:

1. `config init` writes a usable config
2. `verify smoke` reports `verify: OK`
3. `doctor --json` returns `"ok": true` in `summary`
4. `agent --message "hello"` returns a provider response without a traceback

## Package validation gates

Run from the package root:

```bash
.venv/bin/python3.11 -m ruff check .
make lint
```

## Focused regression tests

The public first-user path is protected by targeted CLI regression tests under
`tests/`.

Example focused run:

```bash
.venv/bin/python3.11 -m pytest -q \
  tests/test_verify_command.py \
  tests/test_config_command.py \
  tests/test_public_first_run_cli.py
```

## Broader runtime checks

For package-release or integration-owner validation, use:

1. `RELEASING.md` for the compact release checklist
2. `tests/e2e/runners/` for committed end-to-end runners
3. the maintainer workflow docs for broader integration and tracking flows

## Interactive CLI PTY smoke

The interactive CLI has a reusable PTY-based E2E harness under
`tests/e2e/cli/focus/`. It is intended for maintainer and contributor
validation of the interactive surface a person actually uses: launch, prompt
readiness, slash commands, live turns, tool turns, and opt-in complex workflows.

Run the deterministic local slice from the package root:

```bash
PYTHONDONTWRITEBYTECODE=1 \
.venv/bin/python3.11 tests/e2e/runners/run_cli_focus_e2e.py local
```

Before release, run the deterministic Tier A coding-harness journey gate:

```bash
PYTHONDONTWRITEBYTECODE=1 \
.venv/bin/python3.11 tests/e2e/runners/run_cli_focus_e2e.py tier-a
```

This gate covers PTY answer/interrupt/queue behavior, approval and session-grant
flows, and an isolated code edit/diff/validation/rollback journey. Live MiniMax
smoke remains separate evidence for provider behavior and must not replace this
deterministic gate.

Run live MiniMax interactive CLI smoke when a compatible config and credentials are
available:

```bash
OPENMINION_CLI_FOCUS_E2E_CONFIG=/path/to/config.json \
OPENMINION_CLI_FOCUS_E2E_AGENT=minimax-m2-7 \
.venv/bin/python3.11 tests/e2e/runners/run_cli_focus_e2e.py live
```

Run only the separately gated deep/complex research scenarios:

```bash
OPENMINION_CLI_FOCUS_E2E_CONFIG=/path/to/config.json \
OPENMINION_CLI_FOCUS_E2E_AGENT=minimax-m2-7 \
OPENMINION_LIVE_CLI_FOCUS_COMPLEX_E2E=1 \
.venv/bin/python3.11 tests/e2e/runners/run_cli_focus_e2e.py research
```

Run only the separately gated deep/complex/long coding scenarios:

```bash
OPENMINION_CLI_FOCUS_E2E_CONFIG=/path/to/config.json \
OPENMINION_CLI_FOCUS_E2E_AGENT=minimax-m2-7 \
OPENMINION_LIVE_CLI_FOCUS_COMPLEX_E2E=1 \
.venv/bin/python3.11 tests/e2e/runners/run_cli_focus_e2e.py coding
```

Run the full complex/deep matrix:

```bash
OPENMINION_CLI_FOCUS_E2E_CONFIG=/path/to/config.json \
OPENMINION_CLI_FOCUS_E2E_AGENT=minimax-m2-7 \
OPENMINION_LIVE_CLI_FOCUS_COMPLEX_E2E=1 \
.venv/bin/python3.11 tests/e2e/runners/run_cli_focus_e2e.py complex
```

The complex slice is intentionally not part of routine local validation. It can
consume provider quota and exercises longer tasks such as deep-research,
complex research synthesis, long-running research, deep coding, complex coding,
and long scratch-directory coding flows.
