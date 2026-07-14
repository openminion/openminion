# OpenMinion Terminal Surfaces

Status: active
Last updated: 2026-07-13

OpenMinion has one canonical interactive terminal product: Textual Focus.
Compatibility aliases remain only to give existing operators a bounded
migration path.

## Canonical routes

| Need | Route | Contract |
| --- | --- | --- |
| Interactive work | `openminion` or `openminion focus` | Launch Textual Focus on a TTY. |
| Piped prompt | `cat prompt.md \| openminion` | Run one request without mounting Textual. |
| Scripted request | `openminion run` | Use stable human or JSON one-shot output. |
| Resource operations | `openminion status`, `openminion cron`, and companion CLIs | Use bounded operator commands rather than dashboard widgets. |
| Embedded runtime | `openminion` and `openminion.api` imports | Use typed Python APIs without CLI or widget imports. |
| HTTP integration | `openminion api` and `openminion.api.APIRuntime` | Use the package-owned API runtime and schemas. |

`openminion chat` and `openminion tui` are hidden compatibility aliases.
Interactive use forwards to Textual Focus; piped chat input forwards to the
one-shot owner. Unsupported legacy flags fail with migration help instead of
silently changing behavior.

## Focus activity animation

Focus activity animation is presentation chrome, not runtime semantics.
OpenMinion always ships `openminion:braille` as the built-in default. Optional
providers can be selected only through the presentation-local animation
registry, and provider payloads are raw frames plus timing.

Useful commands:

```bash
openminion focus --animation-provider unicode --animation helix
```

Inside Focus:

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

## Dashboard migration

The dashboard is deprecated but retained until the release gate below is
satisfied. It is available through `openminion dashboard`, the deprecated
`openminion tui --dashboard` alias, and the Focus `/dashboard` side-trip.

| Dashboard area | Replacement |
| --- | --- |
| Chat | Bare `openminion`, `openminion run`, or the Python/API runtime. |
| Tasks | `openminion status` and typed task lifecycle APIs. |
| Cron | `openminion cron` and the cron runtime APIs. |
| Sessions | Focus session commands, `openminion sessions`, and `sessctl`. |
| System | `openminion status`, `openminion doctor`, and system-operation tools. |
| Policy | `policyctl` and policy APIs. |
| Memory | `memctl`, `openminion memory`, and memory APIs. |
| Monitor | Telemetry events plus `openminion status` and `openminion doctor`. |
| Agents | Focus `/agents`, `openminion agent`, and agent APIs. |
| Third Brain | Its optional provider/API integration; it is not a core terminal owner. |

Dashboard notices can be suppressed with
`OPENMINION_DASHBOARD_NO_DEPRECATION=1`. The `tui` alias notice has the separate
`OPENMINION_TUI_NO_DEPRECATION=1` switch. Suppression hides the message only; it
does not change routing or the retirement gate.

## Privacy-safe usage evidence

When a live telemetry service exists, OpenMinion records only a fixed surface
name and, for dashboard navigation, a fixed tab identifier. It never records
prompts, command arguments, content, paths, credentials, or resource payloads.

Runtime-backed Focus, interactive compatibility aliases, dashboard launches,
and dashboard tab activation can emit counters. Demo dashboard sessions and
piped one-shot compatibility paths do not own a live telemetry service, so
their usage is explicitly unavailable rather than inferred as zero. Operators
must consider that limitation during release review.

## Dashboard retirement gate

Dashboard source deletion is not authorized by a general refactor approval.
It requires all of the following:

1. this migration map and runtime notices have shipped,
2. capability parity and privacy-safe usage evidence have been reviewed,
3. at least one compatibility release has shipped unless the operator approves
   an earlier cutoff,
4. the operator separately and explicitly approves dashboard deletion.

Until then, dashboard code and tests remain. After approval, the command becomes
a bounded tombstone that names replacements without importing dashboard code.
