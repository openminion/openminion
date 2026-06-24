# `services/gateway/`

Owner: services-layer
Pairs with: `services/agent/`, `services/channel/`
Canonical builder: `services/runtime/bootstrap.py:build_gateway_service`

## Purpose

Inbound-message gateway. Owns `GatewayService.handle_message(channel,
target, body, session_id)` — the single entry point through which
every channel (CLI, Telegram, HTTP) feeds a message into the agent
runtime. Handles authorization, routing, session resolution,
streaming, turn execution dispatch, and final response assembly.

## Public surface

Re-exported from `openminion.services.gateway`:

- `GatewayService` — entry point
- `GatewayProtocolSession` — protocol-shaped per-session record
- `GatewayStreamEvent` — streaming event record
- `_resolve_turn_timeout_seconds(...)` — turn timeout resolver

Internal modules of note:

- `service.py` — `GatewayService`
- `authz.py` — gateway-level authorization checks
- `routing.py` — channel → agent routing
- `protocol.py` — `GatewayProtocolSession`
- `streaming.py` — turn-level streaming events
- `turn/` — per-turn coordinator package
- `context.py` — gateway-side context resolution
- `memory.py` — memory turn recording plus memory error → fact bridge
- `response.py` — final response assembly (always returns full
  `Message` object — never a partial / stream-only)
- `turn_intent.py` — typed turn-intent and goal-resolution bridge
- `security.py` — cross-cuts to `services/security/`
- `types.py`, `config.py`, `constants.py`

## Owned objects

- `GatewayService` runtime instance (single per runtime).
- Per-message `GatewayProtocolSession` records.
- Streaming subscriber registrations for live channels.

## Non-goals

- Channel transport (Telegram, HTTP, CLI bind) — that lives in
  `controlplane/channels/*` and `controlplane/`.
- LLM call execution — owned by `services/agent/` +
  `modules/brain/` + `modules/llm/`.
- Session storage — owned by `modules/session/`.
- Security policy evaluation — delegated to `services/security/`.

## Dependencies

- `services/agent/` — turn execution.
- `services/security/` — authz / boundary checks.
- `services/channel/` — channel authenticity.
- `services/context/` — turn context resolution.
- `modules/session/` — session record + `SessionStore`.
- `modules/telemetry/` — event emission.

## How this differs from `modules/`

There is no `modules/gateway/`. The gateway is the runtime composition
point that stitches `modules/session/`, `modules/brain/`,
`modules/tool/`, and `modules/telemetry/` into a single inbound
message handler. All feature ownership lives in modules; gateway only
wires them.
