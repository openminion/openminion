# Runtime primitives

Owner: `openminion-runtime`
Shape: `small-primitive`
Runtime peer: standalone (no `services/` peer)

`openminion.modules.runtime` owns typed, provider-neutral contracts that are
shared by multiple runtime substrates. It does not own the composed runtime,
daemon, persistence, or user interface; those live under
`openminion.services.runtime` and the relevant feature modules.

## Members

- `audit.py`: append-only audit events, typed queries, retention policy, and
  compliance-oriented query templates
- `cost.py`: cost attribution, quota envelopes, and budget decisions
- `credentials.py`: credential references, scope checks, access records, and
  reload-on-auth-failure contracts
- `intervention.py`: live-agent projections and typed pause, resume, cancel,
  kill, and redirect actions
- `project_instructions.py`: project instruction discovery and bounded loading
- `replay.py`: deterministic replay policies, results, and divergence records
- `self_model.py`: structural runtime self-description records
- `sync.py`: compatibility bridge for synchronous callers that run coroutines
- `sandboxes/`: provider-neutral sandbox contracts plus optional E2B, Modal,
  and Pyodide adapters
- `contracts.py` and `constants.py`: package-wide runtime contract vocabulary

## Public facade

The package facade re-exports the shared audit, cost, credential, intervention,
and replay contracts. More specialized members remain available from their
own modules rather than expanding the package facade.

## Design rules

1. Runtime decisions use typed fields and closed vocabularies, not prose or
   response-text heuristics.
2. Projection and persistence remain separate steps so callers can test and
   reuse the pure contract functions.
3. Credential records never contain secret values.
4. Replay consumes recorded events and never re-invokes a model or tool.
5. Audit retention uses event kinds and explicit holds; redaction metadata does
   not silently change retention.
6. Intervention adapters are selected by typed action, not inferred intent.
7. Sandbox adapters stay optional and must implement the common contract.

## Package shape

This package intentionally uses focused flat modules instead of one service or
engine. The members have no shared lifecycle and are composed independently by
higher runtime owners. Adding a shared manager or wrapper would obscure those
boundaries without reducing duplication.

Focused tests live in the corresponding runtime test modules under `tests/`.
