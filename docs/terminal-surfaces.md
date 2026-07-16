# OpenMinion Terminal Surfaces

Status: active
Last updated: 2026-07-14

OpenMinion has one canonical interactive CLI: `openminion`. It uses the
terminal renderer by default. Textual remains available through explicit
`openminion --rich`, not as the default.
Compatibility aliases remain only to give existing operators a bounded
migration path.

## Canonical routes

| Need | Route | Contract |
| --- | --- | --- |
| Interactive work | `openminion` | Launch the default terminal renderer on a TTY; add `--rich` only when the Textual renderer is desired. |
| Piped prompt | `cat prompt.md \| openminion` | Run one request without mounting an interactive renderer. |
| Scripted request | `openminion run` | Use stable human or JSON one-shot output. |
| Resource operations | `openminion status`, `openminion cron`, and companion CLIs | Use bounded operator commands rather than dashboard widgets. |
| Embedded runtime | `openminion` and `openminion.api` imports | Use typed Python APIs without CLI or widget imports. |
| HTTP integration | `openminion api` and `openminion.api.APIRuntime` | Use the package-owned API runtime and schemas. |

`openminion focus`, `openminion chat`, and `openminion tui` are hidden
compatibility aliases that forward to the canonical owner. Piped chat input
forwards to the one-shot owner. `openminion dashboard` is a hidden bounded
tombstone that prints replacement commands without launching an interactive
runtime. Unsupported legacy flags fail with migration help instead of silently
changing behavior.

Without `--session`, interactive startup creates a fresh session. Use
`--session <id>` when resuming or naming a session is intentional.

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
any selected provider.

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

The `tui` alias notice can be suppressed with
`OPENMINION_TUI_NO_DEPRECATION=1`. Suppression hides that forwarding notice
only. The dashboard tombstone always prints its migration map.

## Privacy-safe usage evidence

When a live telemetry service exists, OpenMinion records only a fixed surface
name and, for dashboard navigation, a fixed tab identifier. It never records
prompts, command arguments, content, paths, credentials, or resource payloads.

Runtime-backed interactive sessions, compatibility aliases, dashboard launches,
and dashboard tab activation can emit counters. Demo dashboard sessions and
piped one-shot compatibility paths do not own a live telemetry service, so
their usage is explicitly unavailable rather than inferred as zero. Operators
must consider that limitation during release review.

## Retirement status

The operator approved dashboard deletion on 2026-07-14 after the replacement
map and compatibility routes were reviewed. The `dashboard` alias now forwards
to the canonical CLI without importing retired dashboard code.
