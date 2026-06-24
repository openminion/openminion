# `modules/llm/`

Owner: `openminion-llm`
Shape: `template-aligned`
Runtime peer: standalone (no `services/` peer)

## Purpose

Provider-agnostic LLM client surface. Owns the canonical request/response
schema, the per-provider adapters (OpenAI, Anthropic, Ollama, OpenRouter,
LM Studio, MiniMax, etc.), tool-call normalization, transport, and the
public `LLMCTL` client used by brain/context/services.

## Scope

- Public client (`LLMCTL`, `LLMClient`)
- Request/response schemas (`schemas.py`, `contracts/`)
- Provider adapters (`providers/<name>/`) — each provider has its own
  request/response normalization
- Tool-call envelope contracts and normalization helpers
- Transport (HTTP, retries, trace integration)
- Telemetry events for LLM calls (`diagnostics/events.py`)

## Non-goals

- Higher-level orchestration (lives in `modules/brain/`)
- Context-pack assembly (lives in `modules/context/`)
- Tool execution (lives in `modules/tool/`)
- Provider-specific business logic beyond request/response normalization

## Public surface

Re-exported from `openminion.modules.llm`:

- Client: `LLMCTL`, `LLMClient`, `LLMCtlError`, `ResponseError`,
  `ErrorCode`
- Request/response: `LLMRequest`, `LLMResponse`, `Message`, `ToolCall`,
  `ToolChoice`, `ToolSpec`, `UsageInfo`

## Dependencies

- `modules/tool/` — for `ToolSpec` / tool-call envelope (cross-module
  contract, but the contract surface only)
- `base/` — config, channel, runtime, errors

## Canonical shape

Canonical with `interfaces.py`, `schemas.py`, `contracts/` subpackage,
`providers/` subpackage, `cli.py`. The provider subpackages each follow
a recurring shape (`adapter.py`, `normalization.py`,
`request_compat.py` where needed). The tool-call envelope contract
was hardened in the TCV2 lane (closed) — `envelope_v2.py` is the
canonical envelope owner.
