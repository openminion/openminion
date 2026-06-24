# `modules/context/`

Owner: `openminion-context`
Shape: `engine-owning`
Runtime peer: paired with `openminion.services.context`

## Purpose

Builds the `ContextPack` (the assembled prompt context sent to the LLM)
from typed inputs: identity prefix, mission snapshot, summaries, recent
window, retrieval hits, evidence references, and turn input. Owns
segment assembly, trim ladder, render templates, summary rollover,
compaction, and the public `ContextCtlService` that orchestrates the
pipeline.

## Scope

- `ContextCtlService` build pipeline + segment assembly
  (`segment/__init__.py`, `service.py`, `builder.py`)
- Render helpers (`render/sections.py`, `render/renderers.py`)
- Budget/finalize logic (`pack/budgeting.py`, `pack/finalize.py`,
  `pack/semantics.py`, `pack/identity.py`)
- Pinned prefix construction (`prefix.py`)
- Active-state projection (`state/projection.py`)
- Compression subpackage (`compress/`) — summary rollup + delta state.
  See the LLMLingua-2 provider guide for the provider contract, install
  command for the optional `compress` extra, operator-enable flow, and A/B eval
  harness location.
- Knowledge-graph context-source contracts and adapters
  (`knowledge/`) for second-brain and third-brain graph providers
  feeding cited context into gateway assembly
- Prompt tool-schema selection inside `service.py`
- Telemetry surface (`telemetry.py`)

## Non-goals

- Memory record persistence (lives in `modules/memory/`)
- Retrieval ranking and candidate generation (lives in
  `modules/retrieve/`)
- LLM transport / provider semantics (lives in `modules/llm/`)
- The TUI presentation layer

## Public surface

Re-exported from `openminion.modules.context`:

- `ContextCtlService`, `ContextPackBuilder`, `PinnedPrefixBuilder`,
  `IdentityMissingError`
- `CONTEXT_CLIENT_INTERFACE_VERSION`,
  `ensure_context_client_compatibility`

The producer-side Protocol `ContextCtlInterface` was added in MCCS-02
(`interfaces.py`) so downstream callers can depend on a structural
interface instead of the concrete class. Consumer-side protocols
(`IdentityClient`, `MemoryClient`, `ArtifactClient`, `SkillClient`,
`CompressClient`, `RlmClient`, `VectorClient`, `LlmTelemetrySink`,
`ContextRetriever`) live in `contracts.py`.

## Dependencies

- `modules/identity/`, `modules/memory/`, `modules/artifact/`,
  `modules/skill/` (via consumer protocols in `contracts.py`)
- `modules/llm/` for token estimation
- `modules/retrieve/` (indirect via consumer protocol)
- `base/` — config, types, runtime

## Canonical shape

Canonical. Has `interfaces.py` (MCCS-02), `contracts.py` (consumer
protocols), `schemas.py`, `service.py`, `cli.py`, and the `compress/`
subpackage. CASBM (closed) thinned `service.py` and `segment/__init__.py`
without changing the public surface.
