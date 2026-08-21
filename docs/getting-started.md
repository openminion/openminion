# OpenMinion Getting Started

Status: active
Last updated: 2026-08-16

Purpose: give contributors and automation authors a package-local bootstrap and
execution summary for work inside the `openminion` repo.

## Fast bootstrap

```bash
cd openminion
python3.11 -m venv .venv
source .venv/bin/activate
make dev-install
make hooks-install
```

If you are running the CLI locally, also set:

```bash
export OPENMINION_HOME=.
export OPENMINION_DATA_ROOT="$OPENMINION_HOME/.openminion"
```

## First run

For a public package install, prefer an isolated command-line installation:

```bash
pipx install openminion
# or: uv tool install openminion
```

Start with the bare command:

```bash
openminion
```

When the normal default config already exists, this opens the Focus terminal
directly. When the default config is missing and a terminal is available,
OpenMinion launches setup, guides you through hosted, local, or import setup,
writes the canonical config at
`<OPENMINION_HOME>/.openminion/agents.json`, runs `doctor`, and then enters
Focus. A useful first task is:

```text
Give me one safe read-only command to inspect the current directory.
```

The first screen goes directly to the model provider. OpenAI, Anthropic,
OpenRouter, Cortensor Portal, MiniMax, and local Ollama appear first; additional providers,
custom endpoints, and config import remain available from the same menu.

Demo mode is not part of normal onboarding. It remains available through the
explicit `openminion --demo` development/test path. A non-interactive demo can
also be created with `openminion config init --provider echo`. It verifies
local configuration, storage, session, and CLI plumbing only; it does not call
a model. `openminion status readiness` therefore reports `overall=demo` for
that configuration instead of claiming provider readiness.

Setup shows the three identities that matter during first run:

1. provider, such as OpenAI, Anthropic, OpenRouter, Ollama, MiniMax,
   Kimi, Z.ai, DeepSeek, Qwen/DashScope, Gemini, xAI, Mistral, Together,
   Cerebras, Groq, Cortensor Portal, Cortensor Router, or a custom endpoint;
2. model id, such as `gpt-4.1-mini` or `MiniMax-M2.7`; and
3. API adapter, such as OpenAI-compatible or Anthropic-compatible.

For built-in providers, OpenMinion selects the API adapter automatically. The
adapter describes compatibility, not provider identity. MiniMax remains the
provider when it uses the OpenAI-compatible adapter.

Environment credentials are preferred. For example, OpenAI setup reads
`OPENAI_API_KEY`; MiniMax setup reads `MINIMAX_API_KEY` while using the existing
OpenAI-compatible adapter. Compatibility does not imply a shared account or
credential; each provider still uses its own key. Interactive setup may
store a pasted key locally only after a hidden prompt, warning, and confirmation.
On POSIX systems, setup-owned config directories are tightened to owner-only
`0700`, and setup-created config files are owner-only `0600`.

Built-in hosted presets currently include:

| Preset | API format | Environment variable | Default base URL | Recommended model source |
| --- | --- | --- | --- | --- |
| `openai` | OpenAI-compatible | `OPENAI_API_KEY` | `https://api.openai.com/v1` | live-optional, otherwise recommended |
| `anthropic` | Anthropic Messages | `ANTHROPIC_API_KEY` | `https://api.anthropic.com/v1` | recommended |
| `openrouter` | OpenAI-compatible | `OPENROUTER_API_KEY` | `https://openrouter.ai/api/v1` | live-optional, otherwise recommended |
| `cortensor-portal` | OpenAI-compatible | `CORTENSOR_API_KEY` | `https://api.cortensor.app/v1` | recommended |
| `minimax` | OpenAI-compatible | `MINIMAX_API_KEY` | `https://api.minimax.io/v1` | live-optional, otherwise recommended |
| `kimi` | OpenAI-compatible | `MOONSHOT_API_KEY` | `https://api.moonshot.ai/v1` | recommended |
| `zai` | OpenAI-compatible | `ZAI_API_KEY` | `https://api.z.ai/api/paas/v4/` | recommended |
| `zai-coding` | OpenAI-compatible | `ZAI_API_KEY` | `https://api.z.ai/api/coding/paas/v4` | recommended |
| `deepseek` | OpenAI-compatible | `DEEPSEEK_API_KEY` | `https://api.deepseek.com` | recommended |
| `qwen-dashscope` | OpenAI-compatible | `DASHSCOPE_API_KEY` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | recommended |
| `gemini` | OpenAI-compatible | `GEMINI_API_KEY` | `https://generativelanguage.googleapis.com/v1beta/openai/` | live-optional, otherwise recommended |
| `xai` | OpenAI-compatible | `XAI_API_KEY` | `https://api.x.ai/v1` | recommended |
| `mistral` | OpenAI-compatible | `MISTRAL_API_KEY` | `https://api.mistral.ai/v1` | recommended |
| `together` | OpenAI-compatible | `TOGETHER_API_KEY` | `https://api.together.ai/v1` | recommended |

Cortensor Portal is the normal hosted path, similar to other hosted
OpenAI-compatible endpoints: Portal owns routing, quota, capacity, and its
internal request lifecycle. OpenMinion defaults its existing provider timeout
to 480 seconds because responses can be slow; a larger configured
`timeout_seconds` value is preserved by the runtime. Current Portal support is
text, text streaming, native OpenAI tool calls, tool-result continuation, and
streamed tool-call deltas. OpenMinion preserves Portal tool-call IDs,
arguments, finish reasons, usage, and `X-Request-ID` correlation facts. Portal
requests are not automatically retried because resubmission is not yet
idempotent. Portal currently omits `X-Request-ID` from FastAPI `422` request
validation responses; OpenMinion cannot capture a header that is not present.

Run initial Portal acceptance serially. Two simultaneous requests assigned to
the same Cortensor session can currently surface
`router_v4_correlation_mismatch` as a `502 router_error`; OpenMinion reports
that failure without retrying it.

```bash
export CORTENSOR_API_KEY="..."
openminion setup --provider cortensor-portal --agent cortensor-portal --no-focus
```

The advanced direct Router path remains separate and keeps its Router session
semantics. The legacy setup id `cortensor` resolves only to this direct path.

```bash
openminion setup --provider cortensor-router --agent cortensor-router --no-focus
```

`/v1/models` is useful for diagnostics, but it does not prove inference
readiness. Add `--check-provider` only when a quota-consuming text request is
authorized.

Unless a setup run says `live`, a checked-in model is only a recommended
fallback. Fixture-backed provider support means OpenMinion has local request and
configuration coverage; it is not a live account, billing, quota, or model
availability guarantee.

List the static setup catalog without making a provider request:

```bash
openminion setup --list-providers
```

To move an existing setup, export it without embedded secrets and import it on
the new machine:

```bash
openminion config export --out openminion-config.yaml
openminion setup
```

Choose **Import an existing OpenMinion config**, enter the exported path, and
confirm the redacted source/target summary. Imported values replace matching
fields while unrelated settings in an existing target config are preserved.
The standalone equivalent is:

```bash
openminion config import openminion-config.yaml
```

Add `--force` only when the imported file should replace, rather than merge
with, an existing config. Credentials are not included in a normal export, so
set the referenced provider environment variables on the destination machine.

Automation can use the same setup path without prompts:

```bash
openminion setup \
  --provider minimax \
  --model MiniMax-M2.7 \
  --agent minimax-m27 \
  --no-focus
```

For another OpenAI-compatible provider, choose the provider preset and model:

```bash
openminion setup \
  --provider qwen-dashscope \
  --model qwen3.7-plus \
  --agent qwen3.7-plus \
  --no-focus
```

The same pattern works for `kimi`, `zai`, `zai-coding`, and `deepseek` with
their own environment variables and model ids.

Non-interactive setup reads credentials from the provider's environment
variable and does not send a remote provider request by default. Add
`--check-provider` only when the run is allowed to make one bounded provider
request that may consume quota.

Custom endpoints are explicit:

```bash
openminion setup \
  --provider custom-openai-compatible \
  --api-format openai-compatible \
  --base-url https://example.test/v1 \
  --model provider-model-id \
  --agent custom-openai \
  --no-focus
```

## Read first

Before substantial code changes, read:

1. [`engineering-patterns.md`](engineering-patterns.md)
2. [`code-quality-enforcement.md`](code-quality-enforcement.md)
3. [`source-tree-owner-map.md`](source-tree-owner-map.md)
4. [`runtime-surfaces.md`](runtime-surfaces.md)

## Normal execution loop

1. Pick one focused change.
2. Implement code and docs together when the public surface changes.
3. Add or update tests for the behavior you changed.
4. Run focused validation while iterating.
5. Run `make lint` before calling the work ready.
6. Record validation commands in the PR description.

## Pull request shape

Preferred PR shape:

1. short, GitHub-native title,
2. flat line-item bullets that summarize what changed,
3. plain `Validation` label followed by exact command bullets.

Example:

`Add package-local workspace sync helpers`

- add typed workspace sync planning
- add explicit apply/status helpers
- align public docs

Validation
- `make lint`
- `python -m pytest -q tests/<target>`

## Commit message shape

Use commit messages in the form:

1. `<type>(<scope>): <summary>`

Approved current types are:

1. `feat`
2. `fix`
3. `docs`
4. `refactor`
5. `test`
6. `chore`
7. `style`
8. `build`

Guidance:

1. include a scope by default in `openminion`,
2. choose a real owner scope such as `agent`, `api`, `cli`, `e2e`, `gateway`,
   `runtime`, `telemetry`, `tool`, or `tools`,
3. keep the summary specific to the landed change,
4. avoid vague subjects like `update`,
5. prefer the most specific truthful type; do not use `chore` when `docs`,
   `test`, `refactor`, or `build` is more accurate,
6. do not use local shorthand or planning labels as normal commit types.

## Boundary reminder

1. `README.md` is the package contract and install surface.
2. `API_COMPATIBILITY.md` is the public import/export promise.
3. `docs/` is the package-local public docs layer.
4. `tests/` and `scripts/` are important, but they are not public library API.
