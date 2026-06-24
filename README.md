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
  <img alt="Package version" src="https://img.shields.io/badge/package-0.0.1-3775A9">
  <img alt="Python" src="https://img.shields.io/badge/python-3.11%2B-3775A9">
  <a href="LICENSE"><img alt="License" src="https://img.shields.io/badge/license-Apache--2.0-blue"></a>
  <img alt="Status" src="https://img.shields.io/badge/status-alpha-6B7280">
</p>

`openminion` is the `v0.0.1` initial public alpha release of a Python-first, local-first
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

- Current public package line: `v0.0.1` alpha
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
  is the current package-local proof snapshot for the `0.0.1` alpha line.
- [`docs/runtime-surfaces.md`](docs/runtime-surfaces.md) maps the supported
  CLI, runtime, and Python-library surfaces.
- [`API_COMPATIBILITY.md`](API_COMPATIBILITY.md) records the supported public
  import roots and compatibility posture.
- [`RELEASING.md`](RELEASING.md) records the package-local release checks and
  publish flow.
- [`docs/source-tree-owner-map.md`](docs/source-tree-owner-map.md) explains the
  source-tree layout for contributors who need to go deeper than the public
  facade.

## Focus shell

The default `openminion` invocation is the recommended interactive surface:

1. `openminion` (no subcommand) on a TTY launches the terminal-flow focus shell — output flows into your terminal's primary buffer (scrollback works like `git log`).
2. `cat prompt.md | openminion` runs a single one-shot turn against stdin and exits.
3. `openminion focus --rich` opens the Textual full-TUI shell (alt-screen, overlays, in-app dashboard side-trip) instead of terminal-flow.
4. Each agent turn renders with a `⏺` marker, a verb-rotating thinking spinner, colored `●` tool-call markers, and syntax-highlighted code blocks (monokai theme).
5. `--plain-spinner` (or `OPENMINION_FOCUS_PLAIN_SPINNER=1` / `NO_COLOR=1`) drops the verb rotation but keeps the elapsed counter and `esc to interrupt` hint.
6. Tool blocks longer than 6 lines are truncated to keep the scrollback readable; `/expand` re-prints the most recent one in full, `/expand 2` picks the second-most-recent, `/expand 0` lists all truncated blocks.
7. Tool-block verbosity has three levels: `--verbosity quiet` hides tool blocks (an end-of-turn `(N tool calls hidden …)` summary still shows the activity, with `M failed` if any non-zero exits); `--verbosity normal` is the default (6-line cap + `/expand`); `--verbosity verbose` shows full tool bodies up to a 200-line hard cap. Same effect via `OPENMINION_FOCUS_VERBOSITY=quiet|normal|verbose`. Live overrides via `/quiet`, `/verbose`, `/normal` slash commands. Failed tool calls show `✗ (exit N)` in red after the title. **Persistent preferences**: create `<DATA_ROOT>/focus_prefs.toml` (typically `~/.openminion/focus_prefs.toml`) with flat keys `verbosity = "quiet"` and/or `progress = "off"` to set per-user defaults without re-typing slashes or exporting env vars. Precedence: CLI flag → env → preferences file → default.
8. Edit and Write tool calls render with inline unified-diff coloring (`+` lines green, `-` lines red, `@@` hunk headers cyan, `---`/`+++` file headers bold). The same FTV verbosity ladder applies — `quiet` hides; `normal` truncates to 6 lines + `/expand`; `verbose` shows up to 200 lines; `/expand` always shows the full diff. Detection is conservative: when the body isn't recognizable as a unified diff, the generic tool-block render fires instead.
9. Live tool-execution narration: while a tool is running, a yellow `●` marker prints a `Running Bash(ls -la)` narration line; on completion, the final tool block (with output, exit code, and FDR diff coloring for Edit/Write) prints immediately below — no double-render across the live and post-turn paths. Quiet mode suppresses the narration but still counts the call toward the end-of-turn `(N tool calls hidden …)` summary.
10. Slash commands available in focus terminal-flow: `/clear`, `/compact`, `/cost`, `/dashboard`, `/exit`, `/expand`, `/help`, `/init`, `/mcp`, `/model`, `/new`, `/normal`, `/quiet`, `/quit`, `/readonly`, `/resume`, `/sessions`, `/status`, `/tools`, `/verbose`. Prefix `!` runs a shell escape. FPC v2 additions: `/init` bootstraps an `OPENMINION.md` project memory file (also detects `AGENTS.md` / `CLAUDE.md`); `/compact` summarizes older turns to reclaim context (real backend via `SessionContextService`); `/model <provider>` or `/model <provider>/<model>` switches the active model for the session (restart reverts); `/mcp` lists configured MCP servers + health + tool counts; `/readonly on|off|toggle` flags the session as read-only (write-tool blocking enforcement is FPC-11b). Pasting an image-file path into the composer auto-converts to `[image: <path>]`. Custom slash commands can be added as Markdown files in `.openminion/commands/*.md` (project) or `<DATA_ROOT>/commands/*.md` (user-global) with `$ARGUMENTS`/`$1..$N`/`@file`/`!` `cmd` `` interpolation.

## Legacy chat surface

`openminion chat` is in maintenance mode and soft-deprecated as of
`2026-05-10`:

`openminion chat` is the legacy interactive REPL surface. It predates the
focus shell and continues to work for users who depend on it, but **it is no
longer the recommended interactive surface**. Use `openminion` (focus
terminal-flow) for new work. The repository chat CLI charter records the full
maintenance-mode declaration, migration path, and removal criteria. A one-line
dim deprecation notice prints on chat launch; suppress it for scripted
invocations with `OPENMINION_CHAT_NO_DEPRECATION=1`.

Chat-only behavior preserved during the notice period:

1. `chat` shows a waiting spinner by default; disable with `chat --no-progress`.
2. Suppress INFO logs for cleaner chat UI: `chat --quiet`.
3. Interactive chat prompt and assistant lines are color-coded in TTY, with `[session|agent]` context.
4. Prompt format is `[session|agent] you>`.
5. `chat` automatically retries transient turn failures once before showing a final error.
6. Provider/API turn failures are non-fatal in `chat`; the REPL stays open and prints `[chat] turn failed: ...`.
7. Slash commands: `/`, `/help`, `/status`, `/clear`, `/agent`, `/session`, `/tools`, `/artifacts`, `/debug`, `/exit`.

## Cross-surface UX flags

These apply uniformly to focus, gateway, run, and agent surfaces:

1. `--verbosity {quiet,normal,verbose}` — tool-block fidelity. `quiet` hides tool blocks (focus shows an end-of-turn `(N tool calls hidden …)` summary; gateway/run/agent simply omit them); `normal` is the default (6-line cap + `/expand`); `verbose` shows full tool bodies up to a 200-line cap.
2. `--progress {full,minimal,off}` — in-flight chrome. `full` is the default on TTY (spinner + elapsed + interrupt hint); `minimal` keeps the elapsed counter but drops the verb rotation; `off` suppresses all chrome.
3. **Auto-detect**: when stdin OR stdout is piped (e.g. `cat prompt | openminion` or `openminion run "..." | tee out.txt`), `--progress` defaults to `off` automatically so captured output stays clean. Pass `--progress full` to override.
4. Env: `OPENMINION_VERBOSITY=quiet|normal|verbose` and `OPENMINION_PROGRESS=full|minimal|off` set defaults persistently.
5. Legacy aliases still work: `--no-progress` → `--progress off`; `--plain-spinner` → `--progress minimal`; `OPENMINION_FOCUS_VERBOSITY` and `OPENMINION_FOCUS_PLAIN_SPINNER` still resolve (with deprecation warnings).
6. `NO_COLOR=1` honors the universal convention by mapping to `--progress minimal`.
7. The Textual `--rich` shell does NOT yet honor the verbosity ladder (deferred to a follow-on round); use the default terminal-flow shell to get the full UX.
8. `openminion chat` is in maintenance mode and does NOT honor the unified
   `--verbosity` / `--progress` flags. It keeps its own existing
   `--no-progress` / `--quiet` flags during the soft-deprecation notice period.
   The repository chat CLI charter records the current policy.

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
4. `openminion chat` remains supported during the soft-deprecation notice period for users who depend on its specific affordances; new users should pick focus or gateway instead.

## Costs and warranty

OpenMinion is provided on an "as is" basis, without warranties or guarantees of
performance, reliability, availability, fitness for a particular purpose, or
cost outcomes. To the extent allowed by law, the project and contributors are
not liable for damages, losses, outages, billing costs, or other consequences
arising from use or malfunction of the software. See `LICENSE` for the full
legal terms and limitations.

OpenMinion can be configured to call third-party providers that may charge usage fees. You are solely responsible for any provider, API, cloud, infrastructure, or similar charges incurred through your configuration or use of the software.

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
