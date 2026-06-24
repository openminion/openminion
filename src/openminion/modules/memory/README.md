# Memory Module

Owner: `openminion-memory`
Shape: `template-aligned`
Runtime peer: standalone (no `services/` peer)

This module Owns memory records, contracts, promotion/scoring runtime logic, and memory service surfaces. Primary contracts: `interfaces.py`, `contracts/*`, `models.py`, `service.py`. Typed memory records live in `models.py` and `contracts/types.py`.

The `standalone` runtime-peer label means this module does not have a
same-shaped peer module under `services/`. Adjacent orchestration surfaces such
as `openminion.services.agent.memory` still exist and remain the owners for
agent-turn extraction, learning, retrieval pipeline assembly, and gateway
integration.

## sophiagraph sibling package

The reusable durable wisdom graph substrate lives in the sibling package at
`sophiagraph`.

Current KCE boundary rules:

- OpenMinion may consume `sophiagraph` through an editable install or sibling
  source root during local development and CI.
- `sophiagraph` must never import from `openminion`.
- `openminion.modules.memory` remains the orchestrator; the extraction moves
  reusable primitives first and leaves runtime/gateway policy here.

Standalone package release docs:

- package README
- package release runbook
- monorepo release reference

## Backend options

Under the MDCG-owned top-level `runtime.memory_provider=memory_v2` seam,
OpenMinion now supports lower durable-memory backend selection via
`memory.backend.provider`:

- `sophiagraph` — default built-in backend
- `none` — stable empty-read / disabled-write mode
- `external` — adapter slot with capability validation

OpenMinion keeps ownership of the orchestration service and gateway wiring even
when the lower reusable primitives live in `sophiagraph`.

### Layered config story

These are layered selectors, not competing knobs:

- `runtime.memory_provider` chooses the OpenMinion memory implementation family.
- `memory.backend.provider` chooses the durable backend used by the `memory_v2`
  family.

Current practical combinations:

```yaml
runtime:
  memory_provider: memory_v2

memory:
  backend:
    provider: sophiagraph # default durable path
```

```yaml
runtime:
  memory_provider: memory_v2

memory:
  backend:
    provider: none        # stable empty-read / disabled-write mode
```

```yaml
runtime:
  memory_provider: memory_v2

memory:
  backend:
    provider: external
    external_adapter: reference-sqlite
    options:
      db_path: /tmp/reference-sophiagraph.sqlite3
```

If `runtime.memory_provider` changes away from `memory_v2`, the lower
`memory.backend.*` settings are no longer the active owner surface.

### Implementing an external backend

External backends register underneath the lower KCE seam:

- contract: `openminion.modules.memory.backends.interfaces.KnowledgeBackend`
- registry: `openminion.modules.memory.backends.external.register_external_backend`
- capability report: `openminion.modules.memory.backends.external.ExternalBackendCapabilities`

The adapter must map into the canonical `sophiagraph` / OpenMinion record,
relation, portability, and tier-history contracts. It must not redefine those
models. Required capability checks run through the external registry before the
runtime accepts the adapter on the default bootstrap path.

Reference artifacts:

- capability matrix
- reference-sqlite current state
- default-path convergence discussion
- reference adapter: `openminion.modules.memory.backends.external.reference_sqlite`

## CLI portability

The memory module now supports selective record-level portability in addition to
whole-store backup and restore.

- `memctl export --bundle --out <path>` writes a versioned tar.gz bundle
  containing memory records plus optional companion sections.
- `memctl import --bundle <path>` imports that bundle through
  service-owned merge logic instead of CLI-owned row mutation.
- Direct import is the default because it preserves durable record IDs,
  relations, candidates, and tier-transition history for round-trip restore.
- Candidate mode is explicit opt-in (`--trust candidate`) and stages imported
  records as new candidates; bundle `candidates`, `relations`, and
  `tier_transitions` are skipped in that mode and reported back to the operator.

For the executable contract and rollout details, see:

- the memory export/import spec
- the memory export/import tracker

## Write-time poisoning defense (shipped)

Write-time defense against MINJA-class memory-injection attacks is implemented
at the `promote_candidate` seam through
`runtime/candidate_readiness.py:compute_promotion_readiness`. The design is
**LOSG-aligned by construction**: the writing path authors a typed `claim_key`
+ `polarity` and a closed-set `source_class`; the runtime transports and
counts exact-key matches with no embedding-similarity or LLM-judge comparison.

The lane closed with an integration smoke above the closeout threshold and the
contradiction-penalty follow-on now uses BTI temporal validity semantics.

For the design contract and execution plan, see:

- the memory poisoning defense spec
- the memory poisoning defense tracker

## Bi-temporal invalidation (shipped)

Bi-temporal invalidation adds an explicit truth window to durable records:

- `event_time`: when the fact became true in the world
- `valid_to`: when the fact stopped being true

This is intentionally different from operator soft-delete provenance (`is_deleted`, `deleted_at`, `deleted_reason`). Default retrieval remains current-only; audit callers can opt into invalidated rows.

For the contract and execution plan, see:

- the bi-temporal memory invalidation spec
- the bi-temporal memory invalidation tracker

## Current health gate

The current memory-area stewardship gate lives in:

- the current-state assessment
- the stabilization and gap-closure spec
- the stabilization and gap-closure tracker

That gate validates the standalone `sophiagraph` package, the focused
OpenMinion memory/Sophiagraph integration slice, documentation and tracker state, repo-wide
Ruff, and `make lint`.
