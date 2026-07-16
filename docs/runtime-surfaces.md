# OpenMinion Runtime Surfaces

Status: active
Last updated: 2026-07-14

Purpose: give developers one package-local map of the public `openminion`
surfaces and when to use each one.

## Primary surfaces

### 1. Interactive CLI

Use:

1. `openminion`
2. `python -m openminion`

Best for:

1. interactive local operator use,
2. tool-using conversational work,
3. terminal-first workflows.

Notes:

1. the default invocation launches the interactive CLI with the terminal renderer on a TTY,
2. `openminion --rich` explicitly selects the Textual renderer,
3. piped input executes one request without mounting an interactive renderer,
4. `focus`, `chat`, `tui`, and `dashboard` are hidden compatibility aliases,
5. no compatibility alias owns a separate interactive runtime,
6. startup creates a fresh session unless `--session <id>` is provided.

### 2. Python library API

Use:

1. `import openminion`
2. `from openminion import APIRuntime, Agent, OpenMinionConfig, tool`
3. `from openminion.api import APIRuntime, Agent, dispatch_request`

Best for:

1. embedding OpenMinion into Python applications,
2. direct runtime composition,
3. explicit turn execution from code,
4. typed tool/handoff integration.

Stable exported root symbols are recorded in `API_COMPATIBILITY.md`.

### 3. API runtime / server surface

Use:

1. `openminion.api.APIRuntime`
2. `openminion.api.dispatch_request`

Best for:

1. local HTTP-serving integration,
2. request/response adaptation,
3. runtime composition that should stay outside the interactive CLI.

### 4. Companion operator CLIs

Use when you need narrower subsystem control:

1. `openminiond`
2. `artifactctl`
3. `brainctl`
4. `memctl`
5. `sessctl`
6. `contextctl` / `ctxctl`
7. `policyctl`
8. `retrievectl`
9. `skillctl`
10. `a2actl`

Best for:

1. deterministic operator workflows,
2. subsystem inspection,
3. explicit local maintenance flows.

## Example surfaces

The package-owned `examples/` tree is part of the public teaching surface:

1. `examples/starter/provider.py`
2. `examples/starter/channel.py`
3. `examples/starter/plugin.py`
4. `examples/starter/tool.py`
5. `examples/starter/plugin.json`
6. `examples/starter/quickstart.py`
7. `examples/agents/hello/`
8. `examples/skills/hello/`
9. `examples/modules/sample/`

These examples are meant to show direct usage patterns, not internal framework
theater.

## Not blanket public

Importable does not automatically mean stable public API.

Treat these trees as internal/package-owned unless a narrower doc says
otherwise:

1. `openminion.modules.*`
2. `openminion.services.*`
3. `openminion.tools.*`
4. `tests/`
5. `scripts/`
