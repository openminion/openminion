# Token Usage Projection

Owner: `openminion-telemetry`

This package projects durable session facts into normalized run and session
token usage. It does not persist a second ledger, inspect prompt content, or
apply optimization policy.

## Boundaries

1. `service.py` reads canonical session stores and builds run/session views.
2. `token_usage.py` normalizes events into records and summaries.
3. `contracts.py` owns the versioned JSON-compatible export contract.
4. `coverage.py` classifies source-field availability and correlation presence.
5. `formatting.py` owns compact internal run/session presentation.
6. `openminion.services.stats` is compatibility-only and owns no behavior.

The additive v1 `coverage` block reports whether provider token dimensions were
reported, missing, or invalid, plus identity and correlation-field presence.
Completed and failed LLM terminal events participate when they contain a
structured `usage` mapping; failures without usage remain failure facts rather
than becoming zero-token calls. This keeps an explicit provider-reported zero
distinct from unavailable data without changing token totals or inventing
missing usage.

Each `llm_total` record identifies its `total_source`: `provider` means the
provider supplied the total, while `derived` means OpenMinion summed the input
and output dimensions. The export keeps those amounts separate as
`totals.provider_tokens` and `totals.derived_tokens`; cache dimensions remain
independent and are never added to either total.

Provider-reported and explicitly estimated cost are kept separate as
`provider_cost_usd` and `estimated_cost_usd`. Cost is attached once to the
`llm_total` record. Missing cost remains unavailable; the projection does not
derive pricing from model names or a local price table.

OpenMinion callers should import the supported Python surface from
`openminion.modules.telemetry.usage`. A future external optimization package
should consume the `openminion.token_usage.v1` envelope or the shared fixture,
not import OpenMinion storage, service, event-projection, or prompt internals.
The interoperability fixture lives at
`tests/telemetry/fixtures/token_usage/openminion_token_usage_v1.json`.

Additive fields may extend v1. Removing fields or changing accounting meaning
requires a new schema version.
