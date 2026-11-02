# Memory Module

Owner: `openminion-memory`
Shape: `template-aligned`
Runtime peer: standalone (no `services/` peer)

This module owns memory records, contracts, promotion/scoring runtime logic,
and memory service surfaces. Primary contracts live in `interfaces.py`,
`contracts/`, `models.py`, and `service/`.

The `standalone` runtime-peer label means this module does not have a
same-shaped peer module under `services/`. Adjacent orchestration surfaces such
as `openminion.services.agent.memory` still exist and remain the owners for
agent-turn extraction, learning, retrieval pipeline assembly, and gateway
integration.

## Sophiagraph dependency

The reusable durable wisdom graph substrate is provided by the `sophiagraph`
package dependency.

Current package boundary rules:

- OpenMinion consumes `sophiagraph>=0.0.10` through its declared package
  dependency. An editable install is also supported for local development.
- `sophiagraph` must never import from `openminion`.
- `openminion.modules.memory` remains the orchestrator; the extraction moves
  reusable primitives first and leaves runtime/gateway policy here.

### Delegated memory

OpenMinion remains the grant issuer and revocation authority for delegated
memory. `PolicyCtl` resolves one exact active grant per operation, then the
memory adapter projects it into Sophiagraph's package-neutral access contract.
Project permission and memory scope may narrow that projection; they never
widen it.

The v1 child posture is either `none` or `read_only_bounded`. Context assembly
enforces the smallest host, grant, and request token budget before model
delivery. Child-authored durable knowledge returns as a typed proposal; only
the parent submits it to the canonical candidate review and promotion flow.
Cancellation and revocation block subsequent operations but cannot retract
context already delivered to a running model.

## Turn capture authorization

A runtime turn's own memory capture, the auto fact extraction of the user
message and the capture bundle written when the turn ends, needs the agent's
`memory.write` authorization. The brain bridge asks once per turn, before the
brain runs, with `memory_capture_allowed` in
`openminion.modules.policy.adapters.memory`. It is the check that
pending-capture recovery makes with `memory_capture_recovery_allowed`: the
same internal actor, without the configured `internal_actor_scopes`, and the
same `memory.write` tool profile, with the turn's channel, the target
`memory-capture`, and the turn's runtime session and root turn, where recovery
uses the `cron` channel and the target `memory-capture-recovery`. Without a
security policy the capture is allowed; with a policy and no tool registry it
is not.

When the decision is not `allow`, including `require_approval` from an
identity hard constraint such as `NEVER write memory records`, the turn makes
no extraction request, writes no candidate, receipt or `memory.capture.result`,
and asks no approval for its capture. Its reply is unchanged, and its capture
intent stays pending with `"error_code": "CAPTURE_RECOVERY_UNAUTHORIZED"` in
`memory_capture_bundle_result`. Pending-capture recovery asks its own check
before it extracts, so it commits the intent once the write is allowed. A turn
whose capture is excluded or disabled makes no extraction request.

The bridge asks on every turn, including excluded (scheduled consolidation)
and disabled (diagnostic) turns, and keeps the answer on the runner. Two
optional writes the brain makes for itself during the turn need it too: the
`decision` record of the turn's routing decision (`write_decision_memory`) and
the `tool_outcome` candidate staged for a tool call's outcome
(`_stage_tool_outcome_candidate`). When the answer is not `allow`, the decision
record is skipped with `brain.decision_memory.skipped`
(`memory_write_not_authorized`), no candidate is staged, and nothing is kept
pending. The working state keeps the decision metadata. The turn's own capture
is authorized only when the answer is `allow` and its capture is neither
excluded nor disabled. A `BrainRunner` used without the service keeps both
writes. Whatever the answer, no `tool_outcome` candidate is staged for an
outcome classified as `policy_denied`. That category holds every ask-user
command returned for a tool call, such as the user's denial, a pending
confirmation or a policy denial, and every tool result with status
`needs_user` or `blocked`, which includes a tool that ran and returned
`requires_user_takeover`. Failure, timeout and allowlisted success outcomes
are staged as before.

## Backend options

Under the top-level `runtime.memory_provider=memory_v2` seam, OpenMinion
supports lower durable-memory backend selection through
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

External backends register through the memory backend seam:

- contract: `openminion.modules.memory.backends.interfaces.KnowledgeBackend`
- registry: `openminion.modules.memory.backends.external.register_external_backend`
- capability report: `openminion.modules.memory.backends.external.ExternalBackendCapabilities`

The runtime owns the backend that the registered factory returns. When the
runtime closes, the memory service calls the backend's `close()` once if it has
one; a backend that holds nothing to release needs none. With
`provider: external` the runtime does not open the `memory.store` store or its
audit sink, because only the built-in `sophiagraph` backend uses them.

The adapter must map into the canonical `sophiagraph` / OpenMinion record,
relation, portability, and tier-history contracts. It must not redefine those
models. Required capability checks run through the external registry before the
runtime accepts the adapter on the default bootstrap path.

The reference adapter is
`openminion.modules.memory.backends.external.reference_sqlite`; capability
validation remains owned by the external backend registry.

Atomic terminal capture, `apply_capture_bundle(bundle)`, is an optional backend
method outside the required `KnowledgeBackend` protocol. A backend that
implements it receives each turn's capture bundle and returns its receipt, as
the built-in store does, and an error it raises propagates unchanged. A backend
without it, such as the reference adapter, is reported with
`CaptureRecoveryUnsupportedError`: the turn completes, its capture intent stays
pending with `"error_code": "CAPTURE_RECOVERY_UNSUPPORTED"` in
`memory_capture_bundle_result`, and pending-capture recovery leaves it pending.
Recovery asks first, after the intent's identity and authorization checks: the
memory API, the memory service, the audited store and the backend adapter
answer `supports_capture_bundles()` with the same method check, without
writing. While the configured backend cannot commit a bundle, recovery does not
read the intent's source message or call the extraction model. A later
recovery with a backend that implements the method recovers the intent. The
backend itself needs no other method.

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

## Write-time poisoning defense (shipped)

Write-time defense against contradictory memory injection is implemented at
the `promote_candidate` seam through
`runtime/candidate_readiness.py:compute_promotion_readiness`. The writing path
authors a typed `claim_key`, `polarity`, and closed-set `source_class`; the
runtime transports and counts exact-key matches without embedding similarity
or an LLM judge.

The contradiction penalty uses the same temporal-validity semantics as durable
memory retrieval.

## Bi-temporal invalidation (shipped)

Bi-temporal invalidation adds an explicit truth window to durable records:

- `event_time`: when the fact became true in the world
- `valid_to`: when the fact stopped being true

This is intentionally different from operator soft-delete provenance (`is_deleted`, `deleted_at`, `deleted_reason`). Default retrieval remains current-only; audit callers can opt into invalidated rows.

## Current health gate

Focused memory tests live under `tests/memory/` and
`tests/services/agent/memory/`. Package closeout also runs repository Ruff and
`make lint`; provider-backed usefulness remains a separate live-evidence claim.
