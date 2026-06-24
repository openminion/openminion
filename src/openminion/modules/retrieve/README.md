# `modules/retrieve/`

Owner: `openminion-retrieve`
Shape: `engine-owning`
Runtime peer: standalone (no `services/` peer)

## Purpose

Persistent retrieval substrate distinct from in-memory recall. Owns
typed document units, the RAPTOR-tree builder, ingestion paths,
candidate generation/selection (lexical + semantic), expansion
strategies (node/group/window/document), and recency-aware scoring.
Returns ranked `RetrievedItem`s that the context pack can render.

## Scope

- `RetrieveCtl` service + `RetrieveCtlInterface` Protocol
- Schemas: `RetrieveRequest`, `RetrievalFilters`, `RetrievedItem`,
  `DocUnit`, `IngestResult`, `RaptorBuildResult`,
  `GroupLongUnitsResult`
- Runtime subpackage (`runtime/`): `expansion`, `ingestion`,
  `retrieval`, `storage`, `time`, `unitization`
- Persistent storage backends (`storage/`)
- Diagnostics / telemetry events (`diagnostics/events.py`)

## Non-goals

- Conversation memory (lives in `modules/memory/`)
- Context-pack assembly (lives in `modules/context/`)
- Long-term semantic memory promotion (lives in `modules/memory/runtime/`)

## Public surface

Re-exported from `openminion.modules.retrieve`:

- Service: `RetrieveCtl`, `RetrieveCtlInterface`, `RetrieveCtlConfig`,
  `load_config`, `resolve_config_path`
- Records: `RetrieveRequest`, `RetrievalFilters`, `RetrievedItem`,
  `DocUnit`, `IngestResult`, `RaptorBuildResult`,
  `GroupLongUnitsResult`
- Versioning: `RETRIEVE_INTERFACE_VERSION`,
  `ensure_retrieve_compatibility`

## Dependencies

- `modules/storage/` — backend store substrate
- `modules/llm/` for embedding calls (semantic candidate generation)
- `base/` — config, runtime

## Canonical shape

Canonical with `interfaces.py`, `schemas.py`, `runtime/` subpackage,
`storage/` subpackage, `cli.py`, `diagnostics/`. RCBM (closed) extracted
2 telemetry helpers from `RetrieveCtl` into
`diagnostics/events.py`; the remaining 60 methods stay on the
class (the bulk are thin delegations to existing runtime/ siblings).
