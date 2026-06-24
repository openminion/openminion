# OpenMinion Testing And Validation

Status: active
Last updated: 2026-06-18

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
