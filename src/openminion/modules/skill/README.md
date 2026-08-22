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
`runtime/skill/` package separates ingest, catalog, and matching concerns;
`runtime/skill/__init__.py` keeps the public `Skill` composition surface.

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

Per-path runtime defaults:

1. `Skill.ingest_text`, `Skill.ingest_file`, `Skill.ingest_artifact`:
   `untrusted_local`
2. `Skill.ingest_url`: `untrusted_remote`

Model and runtime callers cannot override trust, status, reviewer identity, or
inspection. These facts are derived from `SkillIngestAuthority`. Local operator
surfaces may declare provenance at ingest, but every new version is staged as
`draft` and remains non-selectable until a separate admit operation succeeds.

Admission posture:

1. `skill ingest` stages an immutable package version and returns its version
   hash.
2. `skill admit` activates an exact staged version with an expected-active-hash
   compare-and-swap, required reason, target status, and derived local reviewer.
3. `skill rollback` activates a previously admitted version through the same
   compare-and-swap contract.
4. Implicit reads and selection resolve only the active admitted version;
   explicit version reads can inspect pending versions.
5. `skill.version_admitted` records IDs, hashes, authority class, reviewer,
   reason, and status without copying skill content.

Operator gate:

1. Runtime/model authority cannot activate any staged version, regardless of
   local or remote provenance.
2. API and controlplane mutation routes fail with
   `SKILL_OPERATOR_AUTH_REQUIRED` until their boundary supplies authenticated
   operator authority.
3. Metadata such as `allowed-tools` and `requires_tools` never grants tool or
   policy authority.

## Agent Skills resources

File ingest supports bounded `references/`, `assets/`, and `scripts/`
resources. Their manifests are stored on `SkillPackage.resources`; content is
loaded only when a caller explicitly requests one path. Bundled scripts always
carry `executable=false` and are never executed by the skill runtime. The full
field and directory posture is documented in
`docs/reference/skill-agent-skills-conformance-matrix.md`.
