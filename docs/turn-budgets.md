# Understanding turn budgets and token cost

Start with the existing defaults and the turn's actual stopping reason. A
tool shortlist, an execution limit, a context-pack target and a provider's
output limit control different work. Increasing one does not fix exhaustion
of another.

## What the defaults control

| Setting or owner | Default | Meaning |
| --- | --- | --- |
| `runtime.tool_selection.max_tools_per_turn` | `6` | Maximum entries in the selection service's shortlist, not executed calls per turn. |
| `runtime.tool_selection.tool_prompt_token_budget` | `600` | Estimated prompt budget for that shortlist, not a complete model-request limit. |
| General adaptive loop | `24` iterations, `64` tool calls | Loop execution bounds; adaptive extensions and other strategies have their own policies. |
| `security.tool_policy.max_calls_per_run` | `160` | Runtime tool-call bound, separate from the adaptive loop's bound. |
| `runtime.session_context_token_budget` | `0` | Disable this optional additional session-context cap; purpose-specific pack and bucket budgets still apply. |
| `runtime.tool_schema_shortlisting_enabled` | Unset; brain default `false` | Controls the optional preliminary schema-selection call in eligible adaptive paths. It does not disable exact-name tool discovery. |

Agent/profile overrides can change the effective limits. Inspect the actual
configuration and stop reason before attributing an early stop to a default.
Do not remove approval requirements or automatically extend a turn to hide a
budget failure.

The decision-context target is `2,200` tokens for turns 1 through 5, `2,800`
for turns 6 through 10 and `3,200` after turn 10. The first two turns retain a
smaller recent-turn bucket because there is little history to include, but no
longer use a smaller overall target. Judge and validation requests retain their
separate `3,000`-token targets. These are context-pack targets, not provider
output limits or complete request sizes.

## Catalog size is not request size

The ordinary entry path offers control schemas and `tool.request`. The model
requests exact tool names, and the adaptive loop exposes their schemas. Other
available tools remain discoverable through the existing tool directory. A
configured catalog of many tools therefore does not imply that every model
request contains every full schema.

Where `tool.request` is disallowed or another execution path is selected,
exposure can differ. Inspect the outgoing request. Enabling preliminary
shortlisting can add a model call in paths where it applies; it need not change
an entry-selected path at all.

Context-pack token reports are estimates for the pack, not the provider's
complete request. Prompts, schemas and tool transcripts can add tokens later.
Pinned required context can leave a pack over its target; check `over_budget`,
bucket usage and the trimming decisions instead of interpreting a target as a
hard bound on the wire request. Local rebuilding time, repeated request bytes
and provider-billed uncached tokens are separate measurements.

## Choose a change from evidence

Use a bounded representative task and record:

1. Package/commit, provider/model, effective profile, prompt and expected result.
2. Every actual request's purpose, schema count/size, input/output tokens and
   cached tokens where the provider reports them. Keep unknown cache usage
   distinct from zero, and omit credentials and private prompt content.
3. Context-pack bucket usage, dropped segments and `over_budget`, plus local
   assembly time separately from provider latency.
4. Executed tool calls, loop iterations, exact stop reason, policy decisions
   and whether the task completed correctly.

Compare one setting at a time on equivalent isolated sessions. Keep tool access,
policy and success criteria unchanged. Check necessary-tool discovery and a
follow-up turn as well as cost. Repeat before claiming a speed or cost advantage;
provider caching, latency and model choices vary between samples.

Raise only the exhausted bound when a valid workload demonstrates that it is
too small. If a pack adjustment is applied twice, fix that owner rather than
inflating its default. If displayed totals disagree with the request trace,
resolve the accounting discrepancy before using them to tune defaults.

## Maintainer assessment for issue #497

An October 29, 2026 measurement on `bffd935d5` used the official MiniMax-M2.7
profile with minimal thinking, fresh local state and a find/read/summarize
fixture. The session-context cap was `0`; the paired runs differed in preliminary
shortlisting being disabled or enabled. Both completed correctly with two
executed file-tool calls and no tool errors.

| Observation | Disabled | Enabled |
| --- | --- | --- |
| Actual provider requests | 6 | 6 |
| Full schemas per request | 1, 8, 4, 5, 5, 1 | 1, 8, 4, 5, 5, 1 |
| Provider-reported input tokens | 30,541 | 30,882 |
| Provider-reported output tokens | 1,755 | 1,904 |
| Provider-reported cached input tokens | 6,215 | 6,318 |
| Measured turn time | 33.76 s | 42.04 s |
| Total measured context-pack assembly time | 0.0430 s | 0.0393 s |

These are two observations, not a comparative performance benchmark or a
DeepSeek qualification. Neither exhausted the execution budget. Both used the
entry-requested schema path, so this pair does not measure the benefit of the
optional preliminary selector in other paths. The response summary reported
only one LLM call and omitted some phase usage; the table uses captured provider
responses instead. No pricing or billed-cost claim follows from these totals.

The current recommendation is to retain the defaults above. A blanket increase,
a global shortlisting flip or a new context cache is not supported by this
evidence. Further tuning needs workload-specific proof and accurate whole-turn
accounting. Follow the investigation and its linked fixes in
[#497](https://github.com/openminion/openminion/issues/497); the independently
reported double recent-window adjustment remains separate in
[#482](https://github.com/openminion/openminion/issues/482).
