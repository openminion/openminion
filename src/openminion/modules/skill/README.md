# `modules/skill/`

Owner: `openminion-skill`
Shape: `template-aligned`
Runtime peer: standalone (no `services/` peer)

## Purpose

Skill / workflow / recipe substrate: typed skill packages, workflow
DAGs, recipe definitions, and the runtime that matches user intent to
applicable skills. Owns ingestion (markdown / front-matter / authored
formats), the linter that validates skill content, and the JIT client
that hydrates skill snippets into context packs.

## Scope

- `Skill`, `SkillPackage`, `SkillConfig`, `SkillError`
- Workflow types: `Workflow`, `WorkflowStep`, `WorkflowCatalog`,
  `WorkflowCatalogEntry`
- Tool recipe (`ToolRecipe`)
- Matching: `SkillMatch` plus the LLM-selector runtime
- Linting: `LintIssue`
- JIT client: `SkillJITClient`, `ContextCtlSkillAdapter`

## Non-goals

- Skill execution / tool dispatch (lives in `modules/tool/`)
- Long-term memory of skill outcomes (lives in `modules/memory/`)
- Cross-agent skill exchange (out of scope for this module)

## Public surface

Re-exported from `openminion.modules.skill`:

- Core types: `Skill`, `SkillConfig`, `SkillError`, `SkillPackage`,
  `ToolRecipe`
- Workflows: `Workflow`, `WorkflowStep`, `WorkflowCatalog`,
  `WorkflowCatalogEntry`
- Matching: `SkillMatch`, `SkillJITClient`, `ContextCtlSkillAdapter`
- Linting: `LintIssue`
- Config: `load_config`

## Dependencies

- `modules/context/` (consumer-side, for skill-snippet rendering)
- `modules/storage/` — SQLite substrate
- `base/` — config, paths

## Canonical shape

Canonical with `interfaces.py`, `contracts.py`, `models.py`, `runtime/`
subpackage, `storage/` subpackage, `cli.py`, `diagnostics/`. The
`runtime/skill.py` (1,375 LOC, 27 methods on `Skill`) is currently
deferred from the maintainability decomposition lane per MFLR-06 —
re-evaluate after the scorer-deletion follow-on closes, since the scorer
removal materially changes the file shape.

Selection narrowing and the promotion-cadence orchestration over the
shipped proposal / review / emergence pipeline are described in
the skill-library v2 promotion-cadence spec (SLV2 lane).

## URL ingest threat model (SIPS-03/04)

The `tools/skill/url_ingest.py` module fetches markdown from public URLs
for the `skill.ingest_url` surface. The threat model that the module
defends against:

1. **Pre-fetch host blocklist** — `is_blocked_skill_host` rejects
   localhost, loopback, private IP ranges, link-local addresses, and
   common internal TLDs (`.local`, `.internal`, `.corp`, `.home`, `.lan`)
   before any HTTP request is issued.
2. **Redirect-aware host re-validation (SIPS-03)** — `urllib`'s
   automatic redirect following is disabled via
   `_NoFollowRedirectHandler`. Each redirect target is re-checked
   against the blocklist before the next request. A public host
   cannot 302 to an internal host without the redirect being refused
   with the existing `BLOCKED_HOST` error code.
3. **Redirect chain cap (SIPS-03)** — `SKILL_URL_MAX_REDIRECTS = 3`
   limits chain depth. Exceeding the cap fails with
   `URL_INGEST_REDIRECT_LIMIT`.
4. **DNS rebinding guard (SIPS-04)** — the host is resolved once at
   the initial check and the IP set is pinned as a baseline. Before
   the first fetch, the host is resolved again; if the resolved set
   differs from the baseline, the fetch fails with
   `URL_INGEST_DNS_REBINDING_GUARD`. This blocks the
   resolve-twice-with-rebind attack where a hostile DNS server returns
   a public IP to the check and a private IP to the fetch.

Explicitly out of scope for this lane:

- Per-host rate limiting / global URL ingest budget
- Request signing / origin authentication
- Content-Type policy enforcement beyond the `.md` extension check
- TLS certificate pinning

Out-of-scope concerns must be opened in a separate URL-ingest hardening
tracker, not retrofitted here.

## Skill ingest trust posture (STIP)

The skill runtime carries a structural trust taxonomy in
`bundle_metadata.trust`. This is provenance metadata, not a content
classifier.

Canonical trust values:

1. `trusted_local`
2. `trusted_remote`
3. `untrusted_local`
4. `untrusted_remote`

Per-path defaults when the caller does not pass an explicit trust value:

1. `Skill.ingest_text`, `Skill.ingest_file`, `Skill.ingest_artifact`:
   `untrusted_local`
2. `Skill.ingest_url`: `untrusted_remote`

Operator surfaces can override the default with `--trust=<level>` (CLI)
or the runtime `trust=` parameter. Invalid values fail closed.

Audit posture:

1. `skill.untrusted_source_promotion` fires when an `untrusted_*`
   skill transitions to a catalog-visible status (`draft -> verified`,
   `verified -> blessed`, or `draft -> blessed`).
2. The event payload records `skill_id`, `version_hash`, `trust`,
   `previous_status`, `new_status`, and `promotion_path`.

Operator gate:

1. `untrusted_remote -> catalog` transitions are blocked on runtime-owned
   paths.
2. Runtime/cadence paths must not promote `untrusted_remote` skills without
   operator authorization.
3. `untrusted_local` is still allowed on runtime-owned paths; the trust
   distinction is provenance, not a local-content classifier.
