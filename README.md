<p align="center">
  <img src="https://www.openminion.com/brand/openminion-logo.png" alt="OpenMinion logo" width="128" />
</p>

<h1 align="center">OpenMinion</h1>

<p align="center">
  <strong>Python-first, local-first runtime for tool-using AI agents.</strong>
</p>

<p align="center">
  <a href="https://github.com/OpenMinion/openminion">GitHub</a>
  · <a href="https://pypi.org/project/openminion/">PyPI</a>
  · <a href="https://www.openminion.com">Website</a>
  · <a href="https://www.openminion.com/docs/">Docs</a>
  · <a href="https://x.com/OpenMinion">X</a>
</p>

<p align="center">
  <a href="https://pypi.org/project/openminion/"><img alt="PyPI" src="https://img.shields.io/pypi/v/openminion?cacheSeconds=300"></a>
  <a href="https://pypi.org/project/openminion/"><img alt="Python" src="https://img.shields.io/pypi/pyversions/openminion?cacheSeconds=300"></a>
  <a href="LICENSE"><img alt="License" src="https://img.shields.io/badge/license-Apache--2.0-blue"></a>
  <img alt="Status" src="https://img.shields.io/badge/status-alpha-6B7280">
</p>

OpenMinion is the public preview of a runtime for running inspectable
agents on your own machine. One shared runtime spans CLI, Python API, HTTP API,
and daemon-backed workflows, with durable sessions, structured tools, and
inspectable run state built in. Long-running autonomy, live provider
resilience, and memory/context usefulness claims remain proof-gated during the
alpha period.

## Read This First

1. Read [At a Glance](#at-a-glance) to confirm the current product boundary.
2. Follow [Install](#install) and [Quick Start](#quick-start) for one local run.
3. Use [Runtime Surfaces](#runtime-surfaces) to choose CLI, Python, HTTP, or
   daemon-backed operation.
4. Read [Development](#development) before changing the runtime.
5. Use the [documentation site](https://www.openminion.com/docs/) for the full
   operator and contributor manual.

## Trust and Brand Safety

- Official GitHub: <https://github.com/OpenMinion/openminion>
- Official website and docs: <https://www.openminion.com>
- Official X account: <https://x.com/OpenMinion>

OpenMinion has no official token, coin, NFT, airdrop, staking program,
treasury product, or investment offering. Any claim otherwise is unauthorized
and should be treated as a scam.

## At a Glance

| | |
| --- | --- |
| Package | `openminion` |
| Current line | Public preview; `v0.1.0` is reserved for a later broadly usable milestone |
| Python | 3.11+ |
| Best fit | Bounded local workflows, tool use, integrations, and operator-driven agents |
| Main surfaces | CLI, Python API, local HTTP API, and daemon-backed execution |
| State model | Local sessions, conversations, threads, runs, artifacts, and memory records |
| Not the claim | Finished walk-away autonomy or a managed cloud control plane |

OpenMinion is usable for focused local work today, but it remains under active
development. Complex end-to-end prompts and long unsupervised tasks are still
improving. Current 2-hour autonomy certification support is validation-only;
full 8-hour and 24-hour real elapsed certification pilots still require an
approved run window, provider/model access, workspace, and budgets.

## Common Commands

```bash
openminion config init
openminion setup --list-providers
openminion run "hello"
openminion
openminion tools list
openminion doctor --check-turn --json
# Optional: install "openminion[acp]" before using a local ACP client.
openminion acp
```

Useful operator surfaces:

```bash
openminion agent ls
openminion agent status --agent-id default
openminion agent logs
openminion agent inspect --agent-id default --json
```

## Install

Install the current package as an isolated command-line app:

```bash
pipx install openminion
```

or, with `uv`:

```bash
uv tool install openminion
```

Installing into an existing Python environment is also supported:

```bash
python3.11 -m pip install openminion
```

For a source checkout:

```bash
git clone https://github.com/OpenMinion/openminion.git
cd openminion
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

For isolated local data during development:

```bash
export OPENMINION_HOME="$PWD"
export OPENMINION_DATA_ROOT="$OPENMINION_HOME/.openminion"
```

## Quick Start

For a real model-backed session, start with the bare command:

```bash
openminion
```

When setup finishes and Focus opens, ask: `Give me one safe read-only command
to inspect the current directory.`

For a credential-free product tour, create an explicit echo/demo config:

```bash
openminion config init --provider echo
openminion run "hello"
openminion status readiness
```

Demo mode exercises configuration, storage, sessions, and CLI plumbing, but it
does not call a model or prove provider-backed task quality. Readiness reports
label this state `demo`, not `ready`.

Open the interactive CLI:

```bash
openminion
```

Embed the same runtime from Python:

```python
from openminion import APIRuntime

runtime = APIRuntime.from_config_path(None)
try:
    result = runtime.run_turn(
        payload={"message": "Say hello in one short sentence."}
    )
    print(result)
finally:
    runtime.close()
```

See [`examples/starter/quickstart.py`](examples/starter/quickstart.py) for the
complete runnable example and
[`docs/getting-started.md`](docs/getting-started.md) for contributor bootstrap.

## What OpenMinion Provides

- one orchestration model across CLI, Python, HTTP, and daemon-backed paths
- provider abstraction for local and hosted model backends
- structured tools with model-facing contracts and policy-gated execution
- durable local session, run, artifact, memory-record, and conversation state
- interactive and one-shot local workflows
- diagnostics, status, logs, inspection, and OpenTelemetry-ready traces
- extension surfaces for tools, providers, skills, memory, and integrations

## What OpenMinion Does Not Provide

- finished “give it any complex task and walk away” autonomy
- a hosted control plane or managed cloud agent service
- provider-backed certification proof without configured provider credentials
  and an approved run window
- a black-box prompt wrapper that hides runtime state
- automatic permission to perform unsafe or privileged actions
- any cryptocurrency or investment product

## Runtime Surfaces

| Surface | Use it when |
| --- | --- |
| `openminion run` | You want one bounded local request |
| `openminion` | You want an interactive focus session |
| `APIRuntime` and `Agent` | You want to embed the runtime in Python |
| Local HTTP API | You want another process to submit turns or tool requests |
| `openminiond` | You want daemon-backed local execution |
| Operator CLIs | You need focused session, memory, policy, runtime, or artifact control |

The root package exports the supported Python facade: `APIRuntime`, `Agent`,
`AgentRunResult`, `Handoff`, `MemoryBundle`, `OpenMinionConfig`, `subagent`,
`tool`, and `__version__`. See
[`API_COMPATIBILITY.md`](API_COMPATIBILITY.md) before depending on deeper
package internals.

## Repository Map

```text
src/openminion/
  api/         Public Python and HTTP runtime entrypoints
  base/        Foundational contracts and shared primitives
  cli/         CLI entrypoints and terminal UX
  modules/     Feature and subsystem owners
  services/    Cross-owner runtime orchestration
  tools/       Tool host and tool families
docs/          Package-local public documentation
examples/      Runnable usage examples
scripts/       Validation and operator utilities
tests/         Package and integration proof
```

For owner-by-owner detail, read
[`docs/source-tree-owner-map.md`](docs/source-tree-owner-map.md).

## Development

Before contributing, read [CONTRIBUTING.md](CONTRIBUTING.md) and
[CODE_QUALITY.md](CODE_QUALITY.md).

```bash
make dev-install
make hooks-install
make lint
```

Use focused tests while iterating. Run `make lint` before feature sign-off;
reserve `make check` for an intentional broader integration sweep.

## Docs and Release

- [`docs/README.md`](docs/README.md): package documentation map
- [`docs/runtime-surfaces.md`](docs/runtime-surfaces.md): supported runtime
  and library surfaces
- [`docs/system-operations.md`](docs/system-operations.md): local operation and
  diagnostics
- [`docs/testing-and-validation.md`](docs/testing-and-validation.md): validation
  inventory
- [`docs/certification-readiness-matrix.md`](docs/certification-readiness-matrix.md):
  current proof coverage
- [`API_COMPATIBILITY.md`](API_COMPATIBILITY.md): public import and command
  compatibility
- [`RELEASING.md`](RELEASING.md): release checks and publish flow

Model-provider charges, hosted services, and third-party infrastructure are
separate from the Apache-2.0 licensed runtime. Review provider terms and costs
before enabling paid backends.

## License and Brand-use Boundary

- Source code license: Apache-2.0
- Brand/trademark grant: none

The license grants rights to use, modify, and redistribute the code. It does
not grant rights to present a fork, clone, token, website, or social account as
the official OpenMinion project or imply affiliation or endorsement.
