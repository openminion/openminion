# TUI focus E2E harness

These tests drive the terminal focus shell through a real POSIX PTY. They cover
the surface a person uses: launch, prompt readiness, slash commands, live turns,
tool turns, and opt-in complex workflows.

Run the deterministic local smoke:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python3.11 -m pytest -q tests/e2e/tui/focus/test_local.py -ra
```

Run live MiniMax focus smoke:

```bash
OPENMINION_LIVE_TUI_FOCUS_E2E=1 \
PYTHONDONTWRITEBYTECODE=1 \
.venv/bin/python3.11 -m pytest -q tests/e2e/tui/focus/test_live_basic.py tests/e2e/tui/focus/test_live_tools.py -ra
```

Run only the deep/complex research scenarios:

```bash
OPENMINION_LIVE_TUI_FOCUS_E2E=1 \
OPENMINION_LIVE_TUI_FOCUS_COMPLEX_E2E=1 \
PYTHONDONTWRITEBYTECODE=1 \
.venv/bin/python3.11 tests/e2e/runners/run_tui_focus_e2e.py research
```

Run only the deep/complex/long coding scenarios:

```bash
OPENMINION_LIVE_TUI_FOCUS_E2E=1 \
OPENMINION_LIVE_TUI_FOCUS_COMPLEX_E2E=1 \
PYTHONDONTWRITEBYTECODE=1 \
.venv/bin/python3.11 tests/e2e/runners/run_tui_focus_e2e.py coding
```

Run the full complex/deep scenario matrix:

```bash
OPENMINION_LIVE_TUI_FOCUS_E2E=1 \
OPENMINION_LIVE_TUI_FOCUS_COMPLEX_E2E=1 \
PYTHONDONTWRITEBYTECODE=1 \
.venv/bin/python3.11 tests/e2e/runners/run_tui_focus_e2e.py complex
```

Useful environment variables:

- `OPENMINION_TUI_FOCUS_E2E_CONFIG`: config file path.
- `OPENMINION_TUI_FOCUS_E2E_AGENT`: agent id, default `minimax-m2-7`.
- `OPENMINION_TUI_FOCUS_E2E_ARTIFACT_ROOT`: transcript output directory.
