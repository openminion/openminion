# OpenMinion Terminal Surfaces

Status: active
Last updated: 2026-08-10

OpenMinion has one canonical interactive CLI: `openminion`. It uses the
terminal renderer by default. Textual remains available through explicit
`openminion --rich`, not as the default. Install that optional renderer with:

```bash
python -m pip install "openminion[textual]"
```

## Canonical routes

| Need | Route | Contract |
| --- | --- | --- |
| Interactive work | `openminion` | Launch the default terminal renderer on a TTY; add `--rich` only when the Textual renderer is desired. |
| Piped prompt | `cat prompt.md \| openminion` | Run one request without mounting an interactive renderer. |
| Scripted request | `openminion run` | Use stable human or JSON one-shot output. |
| Resource operations | `openminion status`, `openminion cron`, and companion CLIs | Use bounded operator commands rather than dashboard widgets. |
| Embedded runtime | `openminion` and `openminion.api` imports | Use typed Python APIs without CLI or widget imports. |
| HTTP integration | `openminion api` and `openminion.api.APIRuntime` | Use the package-owned API runtime and schemas. |

Legacy `openminion focus`, `openminion chat`, `openminion tui`, and
`openminion dashboard` commands are retired and rejected. There is no hidden
forwarding layer or dashboard tombstone. Use the canonical routes above.

Without `--session`, interactive startup creates a fresh session. Use
`--session <id>` when resuming or naming a session is intentional.

The default terminal leaves mouse-wheel input with the terminal for native
scrollback. It captures the mouse only while a clickable completion menu is
open, then returns control to the terminal when the menu closes.

## Terminal color

The default interactive terminal detects color support automatically. Use the
CLI flag when an inherited shell or test runner reports the wrong capability:

```bash
openminion --color auto
openminion --color always
openminion --color never
```

The CLI flag takes precedence over `NO_COLOR` and `OPENMINION_COLOR`. When the
flag is omitted, those environment variables remain supported for automation
and backward compatibility.

## Read-only operations overview

The optional Textual renderer provides a compact local overview:

```bash
openminion --rich
```

Then enter `/overview`. The overlay reads the active agent/model,
workspace/session, task summary, recent tool activity, telemetry diagnostics,
and host metrics from their existing owners. It performs no write action and
closes with Escape. The command is intentionally absent from the default
terminal renderer; use `/status`, `/tasks`, `/telemetry`, and the bounded
operator commands there.

## Interactive activity animation

Interactive activity animation is presentation chrome, not runtime semantics.
OpenMinion always ships `openminion:braille` as the built-in default. Optional
providers can be selected only through the presentation-local animation
registry, and provider payloads are raw frames plus timing.

Useful commands:

```bash
openminion --animation-provider unicode --animation helix
```

Inside the interactive CLI:

```text
/animation
/animation list
/animation use unicode:helix
/animation save unicode:helix
/animation reset
```

Install the optional Unicode catalog with:

```bash
python -m pip install "openminion[animations]"
```

Theme colors, backgrounds, labels, progress level, and reduced-motion behavior
remain owned by OpenMinion. `--progress minimal` and `--progress off` override
any selected provider. While a turn runs, the default terminal renderer shows
the active status, elapsed time, and one animation frame on the line above the
unchanged input prompt. Stable agent, model, and working-directory details stay
in the bottom toolbar. Minimal progress uses a static dot while keeping elapsed
time, and off hides the active row. With the optional Unicode catalog, the
default terminal animation follows structured brain phases: for example,
braillewave while analyzing, assemble while planning, dna while replanning,
gearspin while executing, orbitnodes while reviewing, scanline while verifying,
fillsweep while evaluating completion, and cascade while saving context. An
explicitly selected or saved animation remains fixed instead.

## Local plugins

Preview a local plugin before installing it, then check its runtime health:

```bash
openminion plugins preview ./my-plugin
openminion plugins install ./my-plugin --root ./src/openminion/extensions/custom
openminion plugins health example.plugin --root ./src/openminion/extensions/custom
```

`preview` reports declared dependencies, permissions, trust tier, and
provenance. `rollback` undoes the last install of that plugin, while `uninstall`
removes it and disables its manifest ID in the active config. Use the same
`--root` for install, health, rollback, and uninstall. The default is the first
path in `OPENMINION_PLUGIN_PATHS`, or the current local-extension root.

## Dashboard replacements

The dashboard runtime has been retired. Its former areas are owned by the
canonical CLI, bounded resource commands, and typed APIs:

| Dashboard area | Replacement |
| --- | --- |
| Chat | Bare `openminion`, `openminion run`, or the Python/API runtime. |
| Tasks | `openminion status` and typed task lifecycle APIs. |
| Cron | `openminion cron` and the cron runtime APIs. |
| Sessions | Interactive session commands, `openminion sessions`, and `sessctl`. |
| System | `openminion status`, `openminion doctor`, and system-operation tools. |
| Policy | `policyctl` and policy APIs. |
| Memory | `memctl`, `openminion memory`, and memory APIs. |
| Monitor | Telemetry events plus `openminion status` and `openminion doctor`. |
| Agents | Interactive `/agents`, `openminion agent`, and agent APIs. |
| Third Brain | Its optional provider/API integration; it is not a core terminal owner. |

## Token Usage Visibility

Interactive sessions show a compact live token line when the active runtime has
usage facts. Inside the interactive CLI, `/cost` shows the current session,
last turn, context-window, and available provider or configured-rate cost
estimate. It says cost is unavailable when neither source can supply one.
`/tokens` renders the durable
token report for the active session in either interactive terminal.

For persisted session inspection, use the status surface:

```bash
openminion status tokens
openminion status tokens --session-id <session-id>
openminion status tokens --run-id <run-id>
openminion status tokens --session-id <session-id> --run-id <run-id>
openminion status tokens --recent 10
openminion status tokens --recent 10 --agent-id <agent-id>
openminion status tokens --recent 10 --only-warnings
openminion status tokens --recent 10 --json
openminion status tokens --session-id <session-id> --json
```

Without `--session-id`, `status tokens` inspects the newest session in the
configured data root. With `--run-id` and no session id, it resolves the owning
session from the run record. Text output is the human insight view: provider
and derived totals, provider-reported and explicitly estimated cost, cache
dimensions, context estimates, context buckets, metered and unmetered
completed/failed call coverage,
coverage/correlation warnings, outcome signals for run-scoped reports, advisory
recommendations, and next-step hints. Use `--recent <count>` for a read-only
rollup across the newest sessions before drilling into one session or run.
Add `--agent-id <agent-id>` to scope that recent-session view to one agent.
Token reports read at most 10,000 relevant events per session by default; use
`--event-limit` to choose a different positive bound. Limited reports are
marked incomplete.
Use `--recent <count> --only-warnings` when you only want sessions with token
telemetry gaps or optimization signals. Text recommendations include stable
advisory codes such as `[missing_provider_identity]`,
`[missing_call_correlation]`, `[derived_total_tokens]`,
`[context_dominates]`, and `[cache_write_without_read]` so follow-up tooling can
key off the same facts without parsing prose.
Recent rollups also show a provider/model coverage matrix so operators can see
which providers report native totals and which paths still rely on derived
totals.
They also include compact efficiency and session-trend rows: non-overlapping
LLM tokens, context estimates, LLM-token change from the prior session,
provider-vs-derived share, context share, cache
read/write ratio, separated cost totals, and the highest-signal warning codes
per recent session.
`--json` emits the raw `openminion.token_usage.v1` envelope for one session or
run, and a rollup envelope containing those raw session envelopes when
`--recent` is used.
In rollup JSON, use `efficiency.llm_tokens` and each trend's `llm_token_delta`
for non-overlapping comparisons. The older `total_visible_tokens` and
`visible_token_delta` fields remain additive-v1 compatibility fields that sum
LLM totals with context estimates; they are not billable-token totals.

Example recent rollup:

```text
status tokens: recent_sessions=10 with_usage=8 complete=yes
totals: provider=12,840 derived=920 context_estimated=6,400 cache_read=1,200 cache_write=2,100 provider_cost=$0.084 estimated_cost=$0.012
efficiency: llm=13,760 context_estimated=6,400 provider_total=93% derived_total=7% context_share=32% cache_read/write=57%
session trends: session-a=provider:6,300 derived:0 context:0 llm_delta:+2,080 cost:$0.042/-; session-b=provider:0 derived:920 context:3,300 llm_delta:-410 cost:-/$0.012 warnings:derived_total_tokens,context_dominates
top sessions: session-a=6,300, session-b=920
provider coverage: openai/gpt-4.1=records:8 provider:9,200 derived:0 cache_read:1,200; local/echo=records:2 provider:0 derived:920 cache_read:0
coverage health: llm_calls=19 metered=18/19 unmetered=1 provider=18/18 model=18/18 usage_events=22 run_id=21/22 trace_id=22/22 llm_call_id=19/22
recommendations: [missing_call_correlation] some usage events lack llm_call_id correlation; [context_dominates] context packing dominates recent usage; inspect bucket totals
drilldown: `openminion status tokens --session-id session-a` | `openminion status tokens --session-id session-b`
```

Example rollup JSON includes machine-readable insight fields next to the raw
session envelopes:

```json
{
  "schema_version": "openminion.token_usage_rollup.v1",
  "session_count": 1,
  "input_session_count": 10,
  "only_warnings": true,
  "agent_id": "agent.main",
  "totals": {
    "provider_tokens": 0,
    "derived_tokens": 920,
    "context_estimated_tokens": 6400,
    "cache_read_tokens": 0,
    "cache_write_tokens": 2100
  },
  "costs": {
    "provider_cost_usd": null,
    "estimated_cost_usd": 0.012
  },
  "provider_coverage": [
    {
      "provider": "local",
      "model": "echo",
      "llm_total_records": 2,
      "provider_total_records": 0,
      "derived_total_records": 2,
      "provider_tokens": 0,
      "derived_tokens": 920,
      "input_tokens": 600,
      "output_tokens": 320,
      "cache_read_tokens": 0,
      "cache_write_tokens": 0
    }
  ],
  "efficiency": {
    "llm_tokens": 920,
    "context_estimated_tokens": 6400,
    "total_visible_tokens": 7320,
    "provider_total_ratio_bps": 0,
    "derived_total_ratio_bps": 10000,
    "context_share_bps": 8743,
    "cache_read_to_write_bps": 0
  },
  "session_trends": [
    {
      "session_id": "session-a",
      "complete": true,
      "first_observed_at": "2026-08-08T10:00:00Z",
      "last_observed_at": "2026-08-08T10:02:00Z",
      "provider_tokens": 0,
      "derived_tokens": 920,
      "context_estimated_tokens": 6400,
      "cache_read_tokens": 0,
      "cache_write_tokens": 2100,
      "llm_tokens": 920,
      "total_visible_tokens": 7320,
      "provider_cost_usd": null,
      "estimated_cost_usd": 0.012,
      "provider_token_delta": -200,
      "llm_token_delta": -410,
      "visible_token_delta": 410,
      "advisory_codes": ["derived_total_tokens", "context_dominates"]
    }
  ],
  "advisories": [
    {
      "code": "context_dominates",
      "message": "context packing dominates recent usage; inspect bucket totals"
    }
  ],
  "summaries": [
    {
      "schema_version": "openminion.token_usage.v1",
      "session_id": "session-a"
    }
  ]
}
```

## Privacy-safe usage evidence

When a live telemetry service exists, OpenMinion records only the fixed
`interactive` surface name. It never records prompts, command arguments,
content, paths, credentials, or resource payloads.

Runtime-backed interactive sessions can emit that counter. Paths without a live
telemetry service report no event rather than inferring usage as zero.

## Telemetry invocation inspection

`telemetryctl doctor` reports whether local telemetry paths are ready and
whether external OpenTelemetry export is `disabled`, `ready`, or `incomplete`.
Local telemetry remains usable when external export is disabled.

`telemetryctl invocation list` lists locally persisted invocation identities
with filters for agent, status, and event type. Use
`telemetryctl invocation show <invocation-id>` for deterministic structural
events and timing, token, cache, cost, policy, and correlated-log summaries.
`telemetryctl invocation graph <invocation-id>` shows finite execution
segments plus orphan and propagation diagnostics. These commands do not
interpret hidden reasoning or print prompt, completion, tool argument, or tool
result content.

## Retirement status

The operator approved dashboard deletion on 2026-07-14 after the replacement
map was reviewed. The remaining command tombstone and interactive aliases were
removed on 2026-07-19; the root command is now the sole interactive entry.
