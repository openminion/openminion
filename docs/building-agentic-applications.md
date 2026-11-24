# Build an agentic application

OpenMinion supplies the agent runtime. Your application owns its users,
business records, workflow, interface, and deployment.

The usual shape is:

```text
application UI or CLI
        |
application workflow and database
        |
Agent or APIRuntime
        |
OpenMinion sessions, tools, policy, artifacts, and telemetry
```

Keep the two state systems linked, not merged. Store your application request
ID beside the returned OpenMinion session, trace, result, run, or task IDs.
Do not use agent memory as the authoritative copy of a business record.

## Choose a runtime surface

| Need | Surface |
| --- | --- |
| One bounded Python call | `openminion.Agent` |
| Shared process runtime, streamed progress, cancellation, or approvals | `openminion.api.APIRuntime` |
| Another language or process | trusted local HTTP API |

`Agent` is the smallest starting point:

```python
from openminion import Agent, ProviderError, TurnTimeoutError


def answer(prompt: str, *, session_id: str) -> str:
    with Agent(session_id=session_id) as agent:
        try:
            return agent.run(prompt, timeout_seconds=120).text
        except (ProviderError, TurnTimeoutError):
            raise
```

The application creates and stores `session_id`. A later process may create a
new `Agent` with that ID and run a new turn. That is session continuity, not
resurrection of an active turn.

Use one application-owned `APIRuntime` when a process needs the full submitted
turn lifecycle:

```python
from openminion.api import APIRuntime

runtime = APIRuntime.from_config_path("agents.json")
try:
    handle = runtime.submit_turn(
        payload={
            "message": "Prepare the release notes.",
            "session_id": "app-session-42",
            "agent_id": "default",
            "idempotency_key": "request-42",
        }
    )
    for chunk in handle.stream():
        if chunk.kind == "status":
            render_status(chunk.data)
        elif chunk.kind == "approval":
            render_approval(chunk.data)
        elif chunk.kind == "final_text":
            render_text(str(chunk.data.get("text", "")))
        # Future event kinds are additive. Ignore kinds this app does not use.
    response = handle.result()
finally:
    runtime.close()
```

The supported submission fields for application use are:

| Field | Meaning |
| --- | --- |
| `message` | Required non-empty user input unless `image_refs` supplies input. |
| `session_id` | Optional runtime continuity ID. Generate and store it in the app. |
| `agent_id` | Optional configured agent profile. |
| `idempotency_key` | Optional deduplication key for one exact request. Conflicting reuse fails. |
| `timeout_seconds` | Optional turn deadline. |
| `deliver` | Whether the configured channel should receive the result. |
| `allowed_tools` | Optional turn tool allowlist. An empty list disables application tools. |
| `forced_tools` | Optional exact tool names requested for the turn. Policy still applies. |
| `permission_mode` | Optional documented runtime permission posture. |
| `image_refs` | Up to four session-owned `image://` references. |

Other profile and routing fields are operator/runtime configuration. Do not put
credentials, arbitrary business records, or authorization principals in
`inbound_metadata`. Fields prefixed with `subagent_` and `subagent_context` are
reserved and rejected on untrusted submission.

## Stream and terminal contract

`RuntimeTurnHandle.trace_id` identifies one submitted turn. Each `TurnChunk`
contains:

- `schema_version`, currently `openminion.turn-stream.v1`;
- `trace_id`;
- a monotonically increasing `sequence` within the turn;
- `event_id`, formed from the trace and sequence;
- `kind`, `data`, and timestamp.

The public kinds are:

| Kind | Minimum data |
| --- | --- |
| `status` | structured runtime phase/status facts, including terminal status on the last status event |
| `approval` | phase, approval ID, tool name, preview, and outcome |
| `final_text` | `text` |

Fields and kinds may be added without changing the stream schema. Consumers
must ignore unknown fields and kinds. Events from one handle are ordered by
`sequence`; event IDs are not application or business IDs.

`handle.resolve_approval(approval_id=..., decision=...)` accepts only
`allow_once` or `deny`. It returns false for a stale or unknown approval ID.
`handle.cancel()` requests cancellation. A synchronous external side effect
already in progress cannot be undone, so the application must read back its
domain state before deciding what to do next.

`handle.result()` returns `TurnResponse`. Success has no `errors`; failure and
cancellation use structured `TurnError` entries. Do not decide success by
matching prose in `final_text`.

## Identity and idempotency

Keep an application table similar to:

```text
request_id | session_id | trace_id | result_id | status
```

The application owns `request_id` and all customer/project IDs. OpenMinion owns
the remaining runtime IDs. An idempotency key deduplicates an exact request in
the runtime store. Reusing it with a different agent, message, session, target,
request ID, delivery mode, history exclusions, forced tools, capability
category, input metadata, typed intent, or images is rejected. Completed
pre-agent-field legacy receipts replay across upgrades only when retained
session/run facts corroborate agent identity, request ID, delivery mode, and
default controls; uncorroborated or non-default legacy receipts fail closed as
conflicts rather than re-executing or discarding the receipt. It does not
replace an application uniqueness constraint or an integration-specific effect
key.

If a process stops, query persisted session/task records through their public
surfaces and start a later turn when appropriate. Active turn handles, queued
input, and SSE reattachment are process-local. Never automatically replay an
external write whose outcome is uncertain.

## Python and HTTP outcomes

The Python surfaces and HTTP surface carry the same meanings in different
shapes:

| Outcome | `Agent` | submitted Python turn | HTTP |
| --- | --- | --- | --- |
| success | `AgentRunResult` | `TurnResponse` without errors | `ok: true` with `turn` |
| provider failure | raises `ProviderError` | `TurnResponse.errors` | `ok: false` error envelope |
| deadline | raises `TurnTimeoutError` | timeout/error result | 504 `turn_timeout` |
| cancellation | cancellation exception/result | `cancelled` `TurnError` | cancel/status routes |
| invalid input | raises request error | failed submission/result | 400 `invalid_request` |
| approval | callback or waiting thread | `approval` chunk and `resolve_approval` | stream and approval route |

Use `POST /v1/turn` for a synchronous structured result and
`POST /v1/turn/stream` for the same result plus ordered chunks. The HTTP API is
a trusted operator surface. Put it behind an application-owned authenticated
tunnel or service boundary; OpenMinion does not claim customer tenancy, TLS,
or product RBAC.

## Configuration and application assets

The application owns feature flags, product prompts, business secrets, and
domain tools. OpenMinion configuration owns providers, agent profiles, runtime
limits, tool exposure, and policy. Select a config path and agent explicitly;
do not create a second merged configuration system.

Product-specific Python tools normally stay in the application with
`@openminion.tool`. A documented `SKILL.md` is a model-facing procedure, not
business state or a separate skill SDK. Publish a plugin only when the same
capability is useful across applications without hidden product state.

## Reusable plugins

The supported external plugin route is the local manifest plugin discovered
through `OPENMINION_PLUGIN_PATHS` and managed by `openminion plugins`. Plugin
code uses the narrow `openminion.tools` authoring surface and exports one
module-level `REGISTRAR` when it contributes tools. The adjacent manifest sets
`contract_version` to `1`; tools use the manifest ID namespace:

```text
model tool:      plugin.<manifest-id>.<operation>
runtime binding: runtime.plugin.<manifest-id>.<operation>
runtime tool:    plugin.<manifest-id>.<operation>
```

Plugins execute in the OpenMinion process and are trusted code. Preview and
verify a bundle before installing it. Manifest dependencies are reported but
not installed. Tool dependencies use `ToolDependencyDecl` and appear in
readiness reports. Enabling a plugin does not bypass tool exposure, policy,
scope, or approval.

The entry-point registrar loader in
`openminion.modules.tool.runtime.plugins` remains compatibility-only. The
family framework, registrar protocol, tool plugin API, method-plugin runtime,
and versioned tool-method contracts remain internal owners behind the public
facade; they are not separate public plugin systems.

See `examples/sdk/application_runtime.py` for the smallest application-owned
runtime and `API_COMPATIBILITY.md` for the complete supported import list.
