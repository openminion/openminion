# `services/tool/`

Owner: services-layer
Pairs with: `modules/tool/` (tool catalog, executor, schemas)

## Purpose

Runtime peer for the tool subsystem. Owns the tool-selection service
(the shortlist + validation flow the brain calls before a turn), the
schema-exposure rules that determine which tools surface to which
caller, and the operator-tunable selection config. The actual tool
catalog, executor, and per-tool implementation live in `modules/tool/`.

## Public surface

Currently exported through direct submodule imports (no `__init__.py`
re-exports — consumers import by file). The intended public surface:

- `selection.ToolSelectionService` — turn-time selection service
- `selection.create_tool_selection_service(...)` — canonical builder
- `selection.SelectionMode` — selection-mode enum
- `selection.SchemaExposure` — schema-exposure enum
- `selection.ToolStub` — shortlist stub record
- `selection.ShortlistPlan` — shortlist plan record
- `selection.SelectionResult` — result record
- `selection.ValidationError`, `selection.create_validation_error(...)`
- `selection.ValidationRetryManager` — retry coordinator
- `selection.stub_to_provider_spec(...)`,
  `selection.selection_result_to_provider_specs(...)` — provider
  spec converters
- `exposure.py` — schema-exposure rules
- `schema.py` — schema helpers consumed by selection
- `config.py`, `constants.py` — operator-tunable selection knobs +
  fixed internal names

## Owned objects

- `ToolSelectionService` runtime instance.
- Per-turn `ShortlistPlan` and `SelectionResult` records.
- `ValidationRetryManager` for retry-with-validation flows.

## Non-goals

- Tool catalog — owned by `modules/tool/` (`ToolRegistry`).
- Tool execution / dispatch — owned by `modules/tool/executor.py`.
- Tool schemas — owned by `modules/tool/` per-tool modules.
- Tool authorization — owned by `services/security/`.
- Per-tool implementations — owned by `tools/*` and
  `modules/tool/builtins/`.

## Dependencies

- `modules/tool/` — `ToolRegistry`, executor, per-tool schemas.
- `modules/brain/` — selection results are consumed by the runner.
- `modules/llm/` — `ProviderToolSpec` shape.
- `services/security/` — selection results are gated through the
  security policy before execution.

## How this differs from `modules/`

`modules/tool/` owns the feature: tool catalog, executor, schemas,
envelope-v2 contract. `services/tool/` owns only the per-turn
runtime question "which tools should this turn expose to the LLM, and
how do we recover from validation failures" — the runtime shortlist
flow, not the catalog.
