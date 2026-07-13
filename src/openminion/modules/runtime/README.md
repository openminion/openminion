# OpenMinion `modules/runtime/`

Owner: `openminion-runtime`
Shape: `small-primitive`
Runtime peer: standalone (no `services/` peer)

Cross-runtime owner package. Hosts typed seams that compose over multiple
runtime substrates (canonical events, phase-status, services/runtime, brain
execution) without owning their persistence or wiring. Each member is a
flat pure-seam file (typed schemas + Literals + pure functions, no
adapters, no service runtime); the package intentionally deviates from
the canonical `interfaces.py`/`schemas.py`/`adapters/`/`service.py`
template because there is no shared engine — only co-located primitives
named by the production-hardening downstream-execution lanes (CRES, COBA,
OBSI, AUCM, EVRP).

## Current members

- `sync.py` — generic compatibility bridge for synchronous callers that must
  run a coroutine, including callers already hosted inside an event loop.

- `credentials.py` — canonical credential-boundary owner. Lands
  the CRES audit (§5) anchors:
  - `CredentialRef` (frozen dataclass, 6 fields).
  - `CredentialAccessEvent` (frozen dataclass, 9 fields; never carries the
    secret value).
  - `CredentialRotationEvent` (frozen dataclass, 6 fields; never carries
    the secret value, old or new).
  - `CredentialScopeKind` Literal: `process`, `profile`, `agent`,
    `tool_family` (closed-set, exhaustive).
  - `CredentialSourceKind` Literal: `env`, `secret_ref`, `profile_override`
    (closed-set, exhaustive).
  - `CredentialRotationPolicy` Literal: `static`,
    `reload_on_auth_failure` (closed-set, exhaustive).
  - Pure functions: `resolve_credential_ref`, `assert_credential_scope`,
    `redacted_credential_ref`, `record_credential_access_event`,
    `reload_credential_after_auth_failure`.
  - Frozen per-source routing map keyed by `CredentialSourceKind`.

The seam is **pure** — it never reads the secret value. Per-source value
resolution is performed by the substrate named in the routing map
(`base/config/env.py`, `modules/secret/loader.py`, and
`base/config/runtime/profile.py`). Audit events flow through a structural
`CredentialAuditLog` protocol that the canonical-events stream owner (or
a test double) satisfies.

## Owner discipline (per CRES spec §5)

- No prose-derived sensitivity verdicts. Classification is by typed
  `CredentialSourceKind` and `CredentialScopeKind` only.
- No silent value rewriting. Redaction renders typed placeholders from the
  `CredentialRef` shape; the formatter never inspects (and is not given)
  the value.
- `assert_credential_scope` and `record_credential_access_event` are not
  fused. Each step is independently callable.
- `access_site` is caller-declared (a static label, not synthesized from
  stack frames or response bodies).
- Per-source routing map is `MappingProxyType`; runtime cannot re-key it.
- Only the typed `AUTH_INVALID` shaping triggers
  `reload_credential_after_auth_failure`. No response-body heuristics.

- `cost.py` — typed cost-attribution and budget-enforcement
  owner: `CostAttribution`, `QuotaEnvelope`,
  `BudgetEnforcementDecisionEvent`, plus pure
  projection/apply/load/evaluate functions.

- `intervention.py` — typed operator observability and
  intervention owner. Lands the OBSI audit (§5) anchors:
  - `LiveAgentState` (frozen dataclass, 9 fields).
  - `BudgetSnapshot` (frozen dataclass) and `PropagationStatus` Literal.
  - `InterventionAction` Literal: `pause`, `resume`, `cancel`, `kill`,
    `redirect` (closed-set, exhaustive).
  - `InterventionDecision` (frozen dataclass, 7 fields).
  - Pure functions: `project_live_agent_state`, `issue_intervention`,
    `propagate_intervention`, `record_intervention_event`.
  - Frozen adapter-selection mapping keyed by `InterventionAction`.

  Owner discipline (OBSI spec §5):

  - No prose-derived intervention verdicts; classification is by typed
    `InterventionAction` only.
  - No LLM-as-judge gating of operator actions.
  - `redirect` is structurally cancel-then-reissue. No in-place prompt
    rewriting.
  - Adapter selection is `MappingProxyType`; runtime cannot rekey it.
  - `issue_intervention` and `propagate_intervention` are not fused.

- `audit.py` — typed compliance audit-trail owner. Lands the AUCM
  audit (§5) anchors:
  - `AuditEventKind` Literal: `tool_invoked`, `memory_read`,
    `memory_mutated`, `credential_access`, `policy_decision`,
    `intervention_issued`, `user_data_exported`, `user_data_erased`
    (closed-set, exhaustive over the 8 audit-named kinds).
  - `AuditEvent` (frozen dataclass, 10 fields: `kind`, `actor_ref`,
    `target_ref`, `timestamp`, `trace_id`, `session_id`, `policy_ref`,
    `artifact_refs`, `redaction_mode`, `immutable`).
  - `AuditRetentionPolicy` (frozen dataclass: per-kind retention
    durations, hold set, erasure-eligible set; module-load
    `DEFAULT_AUDIT_RETENTION_POLICY` baseline).
  - `AuditQueryRequest` / `AuditQueryResult` (typed filter + cursor;
    deterministic ordering: timestamp ASC, then `trace_id`
    lexicographic).
  - `AuditRuntimeSource` Literal: 8 in-scope runtime sources named by
    AUCM §5, mapped one-to-one onto `AuditEventKind` via a frozen
    routing map.
  - Pure functions: `project_runtime_event_to_audit_event`,
    `record_audit_event`, `query_audit_events`,
    `apply_audit_retention_policy`.
  - SOC2 / GDPR / HIPAA scenario adapter functions
    (`soc2_change_decision_query`, `gdpr_erasure_access_query`,
    `hipaa_sensitive_access_query`) returning typed
    `AuditQueryRequest` templates.
  - `InMemoryAuditLog` reference implementation enforcing append-only
    via `AuditAppendOnlyViolation`.

  Owner discipline (AUCM spec §5):

  - No prose-derived compliance verdicts; classification is by typed
    `AuditEventKind` only.
  - No LLM-as-judge auditability of what should be logged.
  - `project_runtime_event_to_audit_event` and `record_audit_event` are
    not fused; each step is independently testable.
  - Append-only is enforced at the `record_audit_event` boundary —
    `InMemoryAuditLog.delete` raises `AuditAppendOnlyViolation` unless
    invoked from `apply_audit_retention_policy`.
  - Retention is keyed by `AuditEventKind` only; `redaction_mode` is
    metadata, not a retention routing key.
  - Source→kind projection map is `MappingProxyType`; runtime cannot
    re-key it.

- `replay.py` — typed deterministic event-log replay owner. Lands
  the EVRP audit (§5) anchors:
  - `ReplayUseCase` Literal: `debug`, `regression_test`,
    `state_recovery`, `audit_replay` (closed-set, exhaustive over the
    4 audit-named use cases).
  - `DivergenceKind` Literal: `llm_payload_mismatch`,
    `tool_payload_mismatch`, `state_mismatch`, `event_order_mismatch`,
    `missing_event` (closed-set, exhaustive over the 5 audit-named
    shapes).
  - `ReplayBundle` (frozen dataclass; 6 audit-named fields plus optional
    `expected_state` / `expected_event_payloads` baselines).
  - `ReplayPolicy` (frozen dataclass, 6 fields: `use_case`,
    `stop_on_divergence`, `compare_llm_payloads`,
    `compare_tool_payloads`, `deterministic_time`,
    `deterministic_random`).
  - `ReplayResult` (frozen dataclass, 6 fields: `bundle_id`,
    `final_state`, `divergences`, `events_replayed`, `events_skipped`,
    `completed_at`).
  - `ReplayDivergence` (frozen dataclass, 6 fields: `event_id`,
    `seam_id`, `expected_payload`, `actual_payload`, `divergence_kind`,
    `recorded_at`).
  - Pure functions: `replay_from_events(bundle) -> ReplayResult` and
    `record_replay_divergence(divergence, *, divergence_log)`.
  - Frozen per-use-case policy map (`MappingProxyType`) keyed by
    `ReplayUseCase`; `default_policy_for(use_case)` returns the canonical
    baseline.
  - Canonical-events event-type identifier
    `REPLAY_DIVERGENCE_EVENT_TYPE = "runtime.replay_divergence"`.

  Owner discipline (EVRP spec §5):

  - No prose-derived replay verdicts; divergence is typed only.
  - No LLM-as-judge classification of mismatches.
  - `replay_from_events` is pure and deterministic; same bundle →
    identical result.
  - No re-invocation of model or tool at replay time; missing recorded
    payloads emit `divergence_kind='missing_event'`.
  - Policy selection is keyed by `ReplayUseCase` only; no content
    inspection.
  - Policy map is `MappingProxyType`; runtime cannot re-key it.
  - `compare_llm_payloads` / `compare_tool_payloads` only suppress that
    kind of divergence; `state_mismatch`, `event_order_mismatch`, and
    `missing_event` always emit.

## Canonical shape (MCCS-04: documented deviation)

This module **intentionally deviates** from the standard module-shape
rule (`interfaces.py` / `schemas.py` / `contracts.py` / `service.py` /
`runtime/`). The deviation is explicit and load-bearing:

1. **No `service.py` / `runtime/` subdir** — there is no single engine.
   Each member file (`credentials.py`, `cost.py`,
   `audit.py`, `replay.py`, `intervention.py`) is
   its own typed-seam owner; they are not co-instantiated, they have no
   shared lifecycle, and they intentionally do not share a parent class.
2. **No `interfaces.py`** — the Protocols a downstream caller would
   import live INSIDE each member file alongside that member's schemas
   and pure functions. Splitting them across files would create a
   second indirection layer with no behavior benefit.
3. **No `schemas.py`** — each member's schemas live next to its
   functions for the same reason. Cross-file imports between members
   are minimized by design.
4. **No `cli.py`** — these are runtime primitives, not user-facing
   subsystems. Their CLI surfaces (if any) live in `cli/commands/` and
   call the seams directly.

The deviation is justified because **each member is a production-
hardening anchor** named by an external audit lane (CRES, COBA, OBSI,
AUCM, EVRP) and the audit specs explicitly require flat, co-located
primitives — not abstracted-over-a-shared-engine. Forcing the canonical
shape here would create ceremony with no architectural benefit.

This module is governed under the same Engineering Patterns rules as
canonical modules (single-owner, no scattered env reads, etc.) — only
the file layout differs.
