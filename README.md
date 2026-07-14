<p align="center">
  <img src="https://www.openminion.com/brand/openminion-logo.png" alt="OpenMinion logo" width="128" />
</p>

<h1 align="center">OpenMinion</h1>

<p align="center">
  <strong>Local-first runtime for tool-using AI agents.</strong>
</p>

<p align="center">
  <a href="https://github.com/openminion">GitHub</a>
  · <a href="https://www.openminion.com">Website</a>
  · <a href="https://www.openminion.com/docs">Docs</a>
  · <a href="#quick-start">Quick Start</a>
  · <a href="#focus-shell">Focus Shell</a>
  · <a href="https://x.com/OpenMinion">X</a>
</p>

<p align="center">
  <img alt="Package version" src="https://img.shields.io/pypi/v/openminion?label=package&color=3775A9">
  <img alt="Python" src="https://img.shields.io/badge/python-3.11%2B-3775A9">
  <a href="LICENSE"><img alt="License" src="https://img.shields.io/badge/license-Apache--2.0-blue"></a>
  <img alt="Status" src="https://img.shields.io/badge/status-alpha-6B7280">
</p>

`openminion` is a public alpha release of a Python-first, local-first
runtime for tool-using agents.

One shared runtime spans CLI work, Python embedding, HTTP API turns,
daemon-backed workflows, tools, sessions, and local integrations, so the
system stays inspectable instead of disappearing behind wrappers.

## Trust and Brand Safety

- Official GitHub: `https://github.com/openminion`
- Official website: `https://www.openminion.com`
- Official X account: `https://x.com/OpenMinion`

OpenMinion has no official token, coin, NFT, airdrop, staking program,
treasury product, or investment offering. Any claim otherwise is unauthorized
and should be treated as a scam.

## At a glance

- Current public package line: run `openminion version` or read
  `openminion.base.version.OPENMINION_VERSION`
- Best fit today: bounded local workflows, operator-driven runs, tool use on
  your own machine, and contributors who want a runtime they can inspect
- Main surfaces: CLI, Python API, HTTP API, daemon-backed workflows, tools,
  providers, sessions, and diagnostics
- Not the claim: OpenMinion is still under active development and should not
  yet be treated as a finished "give it a complex task and walk away" autonomy
  system

## What OpenMinion provides

`openminion` currently provides:

- Runtime core: one local runtime with explicit brain, memory, tool, channel,
  and service ownership boundaries
- User surfaces: focus shell, CLI commands, runtime admin, diagnostics, export,
  and session-aware operator workflows
- Extension surfaces: package-owned tool hosting, plugin loading, MCP
  integration, and skill loading
- Integration surfaces: Python/library imports, structured API/runtime
  entrypoints, configuration profiles, and external service adapters
- Contributor surfaces: examples, scripts, tests, compatibility policy, and
  repo-local docs for people who want to extend the stack

## What OpenMinion does not provide yet

OpenMinion is already useful for bounded local work, but it does **not** yet
claim to be:

- a finished "give it any complex task and walk away" autonomy system
- a hosted control plane or managed cloud service
- a black-box agent wrapper that hides runtime state from the operator
- a token, coin, NFT, airdrop, or investment product of any kind

## Quick start

If you only want one successful local run, start here:

```bash
export OPENMINION_HOME=.
export OPENMINION_DATA_ROOT="$OPENMINION_HOME/.openminion"
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
python -m openminion config init
python -m openminion run "hello"
python -m openminion tools list
python -m openminion doctor --check-turn --json
```

If you want the interactive surface next, launch the default focus shell:

```bash
python -m openminion
```

The legacy dashboard remains available only as a deprecated migration surface.
See [`docs/terminal-surfaces.md`](docs/terminal-surfaces.md) for its replacement
map and release gate.

## Contributor setup

1. Before making code changes, read [CONTRIBUTING.md](CONTRIBUTING.md) and [CODE_QUALITY.md](CODE_QUALITY.md).
2. Keep changes focused, include validation results, and avoid unrelated refactors in the same PR.
3. Use Python 3.11+ and a recent `pip` with PEP 660 editable-install support.

Local tooling baseline:

```bash
make dev-install
make hooks-install
make lint
```

Core commands:

1. `make fix`
2. `make format`
3. `make lint`
4. `make test`
5. `make check`
6. `make hooks-run`

Validation guidance:

1. use task-scoped pytest or integration commands plus repo-wide `ruff check .` for normal slice work,
2. use `make lint` before feature sign-off,
3. treat `make check` as an optional broad integration sweep, not the default per-task closeout command during multi-agent work,
4. see the package-local testing and validation docs for the public validator inventory and which guards are blocking vs advisory.

Temporary artifact rule:

1. broad cleanup file lists, ledgers, scan outputs, and scratch JSON/TSV/TXT artifacts belong in the repository scratch area rather than in package source or docs roots,
2. durable evidence belongs in maintained project documentation or tracked validation artifacts rather than in package source or package docs roots.

## Package layout

```
src/openminion/
  api/         # public API/runtime entrypoints
  base/        # foundational contracts and shared primitives
  cli/         # CLI entrypoints and interactive UX
  modules/     # feature and subsystem owners
  services/    # cross-owner runtime orchestration
  tools/       # tool runtime host + tool families
docs/
  README.md
  standalone-claim-alignment.md
  certification-readiness-matrix.md
  runtime-surfaces.md
examples/
scripts/
tests/
API_COMPATIBILITY.md
RELEASING.md
```

Tool package note:

1. `src/openminion/tools/` now contains both runtime host files and module-like per-tool folders.
2. Each per-tool folder maps to a previously standalone `openminion-tool-*` package.
3. Canonical tool imports are `openminion.tools.<tool_folder>`.

## Docs and release

- [`docs/README.md`](docs/README.md) is the package-local docs entrypoint.
- [`docs/certification-readiness-matrix.md`](docs/certification-readiness-matrix.md)
  is the current package-local proof snapshot for the active alpha line.
- [`docs/runtime-surfaces.md`](docs/runtime-surfaces.md) maps the supported
  CLI, runtime, and Python-library surfaces.
- [`docs/terminal-surfaces.md`](docs/terminal-surfaces.md) records the canonical
  terminal product, compatibility aliases, and dashboard migration gate.
- [`docs/long-horizon-project-worker.md`](docs/long-horizon-project-worker.md)
  records the alpha project-worker substrate, proof shape, and current claim
  boundary for longer objectives.
- [`API_COMPATIBILITY.md`](API_COMPATIBILITY.md) records the supported public
  import roots and compatibility posture.
- [`RELEASING.md`](RELEASING.md) records the package-local release checks and
  publish flow.
- [`docs/source-tree-owner-map.md`](docs/source-tree-owner-map.md) explains the
  source-tree layout for contributors who need to go deeper than the public
  facade.

## Focus shell

The default `openminion` invocation is the recommended interactive surface:

1. `openminion` launches Textual Focus on a TTY.
2. `cat prompt.md | openminion` runs one stdin-backed turn and exits without
   mounting Textual.
3. `openminion run` is the explicit one-shot command for scripts and JSON.
4. `openminion focus` is the named form of the same interactive product.
5. `openminion chat` and `openminion tui` are hidden compatibility aliases.
6. Each agent turn shows a `⏺` marker, verb-rotating thinking spinner,
   colored `●` tool-call markers, and syntax-highlighted code blocks.
7. `--progress minimal` drops moving frames while preserving bounded status
   text, and `--progress off` suppresses in-flight chrome. `--plain-spinner`,
   `OPENMINION_FOCUS_PLAIN_SPINNER=1`, and `NO_COLOR=1` remain compatibility
   paths for reduced motion.
8. Activity animation defaults to `openminion:braille`. Install the optional
   Unicode catalog with `python -m pip install "openminion[animations]"`, then
   run `openminion focus --animation-provider unicode --animation helix` or use
   `/animation list`, `/animation use unicode:helix`, and `/animation save
   unicode:helix` inside Focus.
9. Tool blocks longer than 6 lines are truncated to keep scrollback readable:
   `/expand` reprints the latest block, `/expand 2` selects the second latest,
   and `/expand 0` lists all truncated blocks.
10. Tool-block verbosity has three levels:
   - `--verbosity quiet` hides tool blocks but keeps an end-of-turn hidden-call
     summary.
   - `--verbosity normal` is the default: 6-line cap plus `/expand`.
   - `--verbosity verbose` shows full tool bodies up to a 200-line hard cap.

   You can set the same default with
   `OPENMINION_FOCUS_VERBOSITY=quiet|normal|verbose`, then override it live with
   `/quiet`, `/normal`, or `/verbose`. Failed tool calls show `✗ (exit N)` in
   red after the title.

   For persistent preferences, create `<DATA_ROOT>/focus_prefs.toml` (usually
   `~/.openminion/focus_prefs.toml`) with flat keys such as
   `verbosity = "quiet"`, `progress = "off"`, `animation_provider =
   "unicode"`, or `animation = "helix"`. Precedence is CLI flag → env →
   preferences file → default.
11. Edit and Write tool calls render inline unified diffs. The same verbosity
   ladder applies: quiet hides, normal truncates, verbose shows up to 200 lines,
   and `/expand` always shows the full diff.
12. Live tool-execution narration prints a yellow `● Running Bash(ls -la)` line
    while a tool is active, then renders the final tool block below it. Quiet
    mode suppresses narration but still counts the call in the end-of-turn
    summary.
13. Focus slash commands include `/animation`, `/clear`, `/compact`, `/cost`,
    `/dashboard`, `/exit`, `/expand`, `/help`, `/init`, `/mcp`, `/model`,
    `/new`, `/normal`, `/quiet`, `/quit`, `/readonly`, `/resume`, `/sessions`,
    `/status`, `/tools`, and `/verbose`.

    Other composer affordances:
    - prefix `!` to run a shell escape;
    - paste an image-file path to convert it to `[image: <path>]`;
    - add custom slash commands as Markdown files in `.openminion/commands/*.md`
      or `<DATA_ROOT>/commands/*.md`; command templates can use `$ARGUMENTS`,
      `$1..$N`, `@file`, and shell interpolation with `!` commands.

## Compatibility aliases

`openminion chat` and `openminion tui` print a migration notice and forward to
the canonical owner. They do not retain separate interactive implementations.
Piped chat input forwards to one-shot execution; unsupported old flags fail
with migration help. Notice suppression is available through
`OPENMINION_CHAT_NO_DEPRECATION=1` and
`OPENMINION_TUI_NO_DEPRECATION=1`.

`openminion dashboard` remains a deprecated compatibility surface pending a
separate post-release deletion approval. Its migration map, privacy rules, and
release gate are documented in
[`docs/terminal-surfaces.md`](docs/terminal-surfaces.md).

## Cross-surface UX flags

These apply uniformly to focus, gateway, run, and agent surfaces:

1. `--verbosity {quiet,normal,verbose}` controls tool-block fidelity:
   - `quiet` hides tool blocks;
   - `normal` is the default 6-line cap plus `/expand`;
   - `verbose` shows full tool bodies up to a 200-line cap.
2. `--progress {full,minimal,off}` controls in-flight chrome:
   - `full` is the TTY default with spinner, elapsed time, and interrupt hint;
   - `minimal` keeps elapsed time and drops verb rotation;
   - `off` suppresses progress chrome.
3. **Auto-detect**: when stdin or stdout is piped, `--progress` defaults to
   `off` so captured output stays clean. Pass `--progress full` to override.
4. Env defaults: `OPENMINION_VERBOSITY=quiet|normal|verbose` and
   `OPENMINION_PROGRESS=full|minimal|off`.
5. Legacy aliases still work: `--no-progress` → `--progress off`,
   `--plain-spinner` → `--progress minimal`, and the older focus-specific env
   vars still resolve with deprecation warnings.
6. `NO_COLOR=1` follows the universal convention by mapping to
   `--progress minimal`.
7. Compatibility aliases do not own separate rendering or progress behavior;
   interactive aliases use the same Textual Focus implementation.

## Shared logging conventions

1. `gateway run --quiet` suppresses INFO logs for cleaner output.
2. Override log level without editing config: `OPENMINION_LOG_LEVEL=WARNING`.
3. Keep logs visually secondary in TTY (dim logs); force with `OPENMINION_LOG_COLOR=1`, disable with `OPENMINION_LOG_COLOR=0` or `NO_COLOR=1`.
4. Disable chat colors with `NO_COLOR=1` or `OPENMINION_COLOR=0`; force with `OPENMINION_COLOR=1`.

## Chat vs gateway loop

1. `gateway run` is a runtime/operator path with explicit channel/target/once/idempotency controls.
2. Use `openminion` (focus shell) for conversational UX; use `gateway run` for operational testing and deterministic runtime controls.
3. For any UI integration or plugin, use gateway ingress (`/v1/turn*` or
   `GatewayService.run_once`) rather than direct agent-service calls. The
   repository UI gateway contract documents that boundary.
4. Compatibility aliases remain only for migration and do not own a separate
   conversational loop.

## Costs and warranty

OpenMinion is provided on an "as is" basis, without warranties or guarantees of
performance, reliability, availability, fitness for a particular purpose, or
cost outcomes. To the extent allowed by law, the project and contributors are
not liable for damages, losses, outages, billing costs, or other consequences
arising from use or malfunction of the software. See `LICENSE` for the full
legal terms and limitations.

OpenMinion can be configured to call third-party providers that may charge
usage fees. You are solely responsible for provider, API, cloud,
infrastructure, or similar charges incurred through your configuration or use
of the software.

## Configuration and deeper docs

The package supports multiple runtime backends, profile-based configuration,
built-in tools, plugins, skills, storage, and HTTP/runtime integration. Those
details are intentionally kept out of the front page so this README stays a
generic public entrypoint.

Use these docs when you want to go deeper:

- [`docs/getting-started.md`](docs/getting-started.md) — package bootstrap and
  contributor flow
- [`docs/testing-and-validation.md`](docs/testing-and-validation.md) — smoke
  checks and release-facing validation commands
- [`docs/runtime-surfaces.md`](docs/runtime-surfaces.md) — CLI, runtime, and
  library surface map
- [`docs/long-horizon-project-worker.md`](docs/long-horizon-project-worker.md)
  — alpha project-worker proof shape and current claim boundary
- [`docs/source-tree-owner-map.md`](docs/source-tree-owner-map.md) — source
  layout guide for contributors
- [`examples/README.md`](examples/README.md) — runnable examples and starter
  templates
- [`API_COMPATIBILITY.md`](API_COMPATIBILITY.md) — public import and
  compatibility posture
- [`RELEASING.md`](RELEASING.md) — package release checklist

## License and brand-use boundary

- Source code license: `Apache-2.0`
- Brand/trademark grant: `none`

The software license grants rights to use, modify, and redistribute the code.
It does **not** grant rights to use the OpenMinion name, logos, branding,
website identity, or social identity except for truthful attribution. Forks,
clones, and derivative distributions must not present themselves as the
official OpenMinion project or imply affiliation, endorsement, or maintenance
by OpenMinion contributors unless that is actually true.
