# OpenMinion Terminal Surfaces

Status: active
Last updated: 2026-07-19

OpenMinion has one canonical interactive CLI: `openminion`. It uses the
terminal renderer by default. Textual remains available through explicit
`openminion --rich`, not as the default.

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

## Privacy-safe usage evidence

When a live telemetry service exists, OpenMinion records only the fixed
`interactive` surface name. It never records prompts, command arguments,
content, paths, credentials, or resource payloads.

Runtime-backed interactive sessions can emit that counter. Paths without a live
telemetry service report no event rather than inferring usage as zero.

## Retirement status

The operator approved dashboard deletion on 2026-07-14 after the replacement
map was reviewed. The remaining command tombstone and interactive aliases were
removed on 2026-07-19; the root command is now the sole interactive entry.
