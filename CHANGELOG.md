# OpenMinion Changelog

Status: active
Last updated: 2026-10-24

This file tracks package-facing release notes for `openminion`.

## Unreleased

- Resolve postponed (`from __future__ import annotations`) and quoted
  annotations on `@openminion.tool` functions in their own module, keeping
  `Annotated` field descriptions and constraints. Only argument annotations
  exposed in the args schema are evaluated (return, `self`/`cls` and
  `*args`/`**kwargs` annotations are left alone), and decoration fails with a
  clear `TypeError` when an exposed argument annotation cannot be resolved.
- Keep room participants that join with the same timestamp in join order, with
  a per-session join sequence that runtime migration 14 adds on SQLite and
  Postgres. `list_participants`, and the agent selected when the active agent
  leaves, no longer depend on random participant row ids. A participant added
  again while active keeps its place and join time; one that leaves and
  rejoins takes a new place after every earlier join. Explicit active-agent
  selection is unchanged. On upgrade, existing participants keep the order
  they listed in before, so participants that already share a timestamp keep
  their row id order (#364).
- Upgrade note for runtime migration 14 (#364): as for migration 13, upgrade
  every process that shares a runtime database together, keep a pre-upgrade
  backup, and do not run a binary from before this change against the
  upgraded database. An older writer stores no join place: the first
  participant it adds to a session after the upgrade lists before other joins
  with the same timestamp, adding a second one to that session fails, and a
  participant it rejoins keeps its earlier place.

## Current package line - 2026-10-24

- Preserve deterministic session-message and transcript-event ordering when
  timestamps tie, including stable compaction boundaries across storage
  upgrades.
- Reconstruct run status and lifecycle state beyond the first 2,000 events
  with bounded SQLite and PostgreSQL queries.
- Require declared risk metadata for temporary SDK and handoff tools, and
  prevent denied tools from starting delegated work.
- Fail fast when shared-runtime handoff tools are registered across threads,
  while releasing the registration lock after marker failures.
- Close runtime-owned memory stores and audit sinks exactly once without
  closing borrowed resources.
- Release SQLite connections immediately after migrations and OMX imports so
  Windows callers can rename or remove database files without waiting for
  garbage collection.
- Runtime migration 13 requires every process sharing a database to upgrade
  together. Stop all writers and retain a pre-upgrade backup; mixed-version
  writers and downgrade-in-place are not supported.

## Current package line - 2026-10-22

- Keep SDK follow-up turns on the active session, reject conflicting explicit
  agent selection, and scope identity and artifact stores to configured runtime
  roots.
- Preserve typed provider failures after tool execution and retain every Gemini
  system instruction when translating OpenAI-compatible requests.
- Require model-authored closure verdicts with rendered tool evidence so
  completion decisions remain inspectable and provider-owned.
- Ingest Markdown-first skills under their declared identity, expose pending
  versions, and resolve scheduled-task time zones on Windows.
- Add independent security-finding review and repair workflows with deterministic
  live harnesses and local closeout coverage.
- Order runtime session events by insertion sequence, validate malformed plan
  declarations, retire completed project checkpoints, and keep deleted cron
  history out of active task reconciliation.
- Close runtime-owned memory artifact stores exactly once while preserving
  borrowed-store ownership and scheduler shutdown behavior.

## Current package line - 2026-10-20

- Count canonical empty-scope memories in global introspection so global scope
  reports include every visible memory record.
- Add deterministic and live goal-only coding acceptance to the hosted E2E
  gate, including long-coding handoff and repair-progress coverage.

## Current package line - 2026-10-19

- Add complete scheduled-task operations, including update, run-now, pause,
  resume, delete, history, retry, incident controls, durable attempt fencing,
  daemon continuity, and execution telemetry.
- Support live MCP configuration reload, validated registry updates, rollback,
  lifecycle cleanup, and OAuth login without weakening the existing tool and
  approval contracts.
- Preserve learned-skill admission, save, reuse, denial, evidence, and
  PostgreSQL behavior across CLI, Focus, and durable runtime paths.
- Improve long-horizon memory retrieval, continuity, introspection, and
  ContextCtl fallback health while keeping memory facts and trust diagnostics
  truthful.
- Keep long-running project handoffs, provider failures, approval recovery,
  resumed work, and overlapping turns isolated to their owning session and
  project.
- Preserve canonical tool identity, call ID, status, and error code in recent
  tool activity and debug timelines.
- Stream tool-call lifecycle status through ACP without copying raw tool input
  or output into progress updates.
- Publish declared MCP output schemas and return successful runtime-tool results
  as both JSON text and validated `structuredContent`.
- Preserve canonical identity-verification results and complete rendering
  across terminal, Focus, and runtime inspection surfaces.
- Scope telemetry to the active session, keep token events ordered, and omit
  prompt and tool prose from default diagnostics.
- Tighten artifact reference ownership, deletion, and orphan cleanup while
  preserving lifecycle evidence across CLI and runtime adapters.
- Strengthen terminal confirmations and Focus PTY behavior for permission
  recovery, queued prompts, multiline paste, resize, and repeated approvals.
- Recover invalid project-entry controls, keep inactive-tool activation
  available during direct tool sequences, and accept provider-emitted JSON
  boolean strings without masking malformed tool requests.

## Current package line - 2026-10-15

- Preserve exact-action details in terminal and streamed approval previews,
  including transaction recipient, value, fees and preparation digest (#270).

- Honor explicit empty SDK tool lists while preserving default tools and explicit
  handoffs; delegated turns retain the narrower caller allowlist (#269).
- Deliver action-policy confirmations through SDK callbacks and submitted-turn
  approval events, retaining one-shot grants and cancellation checks (#270).
- Keep queued Focus prompts behind ordinary approval-denial tool results while
  preserving terminal denials for one-time actions.

- Keep MCP CLI configuration aligned with the SDK's project-scoped default,
  enable approval events for submitted SDK turns, and pin the public
  `TurnTimeoutError` introduction to 0.1.5.
- Report the resolved configuration path after MCP commands write changes.

## Current package line - 2026-10-14

- Expose decorated tools passed through `Agent(tools=...)` to the model while
  keeping temporary exposure profiles scoped to each run and collision-free.
- Preserve complete string and falsey tool results, and surface typed provider
  failures after retry exhaustion instead of reporting successful completion.
- Roll back partial tool-family registrations and revoke removed exposure
  profiles without disturbing pre-existing tools or permissions.
- Restore approval-replay tool history from the canonical session transcript
  so failed-command recovery keeps the original tool context.
- Propagate turn cancellation into worker calls and bound provider I/O by the
  remaining deadline; stop subsequent work and late assistant persistence (#258).
- Settle pending asynchronous work when a turn exits so cancelled or timed-out
  operations cannot leak into later runtime work.
- Preserve functional tool-decorator options and expose explicitly supplied
  handoff tools through the existing temporary profile lifecycle (#259, #257).
- Export the existing `TurnTimeoutError` and handle it in the SDK error example
  (#260). Invalid streamed approval decisions now raise `ValueError`; valid
  decisions remain `allow_once` and `deny` (#261).
- Reject MCP approval risk levels outside `low`, `medium`, and `high` (#262).

## Current package line - 2026-10-12

- Generalize approval-bound blockchain operations with atomic, session-safe
  preparations, durable lifecycle facts, and optional read-only child-agent
  delegation for research and resolution.
- Preserve exact agent and session delegation scope across CLI and Desktop
  requests while retaining unscoped CLI defaults.
- Fix child-workspace path handling, detect unavailable sandbox execution before
  approval, and preserve canonical terminal outcomes when later provider work
  fails.
- Classify bounded network diagnostics as read-only, turn command allowlist
  misses into exact approval requests, preserve denylist and install blocks,
  and stop cleanly after denial.
- Make file trash recoverable through Send2Trash; fail without deletion when
  recovery is unavailable, including the memory backend.
- Preserve original bytes on invalid UTF-8 writes and edits, and retain newline
  sequences outside edited spans.
- Return accurate large-file line ranges and search completeness, and honor
  search-root-relative globs. A range starting beyond EOF now fails explicitly.
- Preserve non-secret configuration settings during export while removing
  credential headers and nested channel secrets.
- Preflight patch hunks before mutation, handle grep patterns beginning with a
  dash and filenames containing colons, and enforce descendant read policies
  across code search, maps, indexes and symbol discovery.
- Request complete unsigned cross-platform runtime binaries after final PyPI
  releases and require public checksum readback while stable binary promotion
  remains protected by signing and qualification gates.

## Current package line - 2026-10-08

- Compose commerce order care through authored skills and the existing task and
  tool runtime while preserving exact approvals, persistence, migration history,
  replay safety, and uncertain-result recovery.
- Converge four permission postures across configuration, Focus, runtime policy,
  and status reporting, with automatic read-only approval and durable,
  revocable session grants.
- Generalize tool exposure and confirmation ownership without adding a parallel
  registry or weakening authorization for writes and external effects.
- Add discovery-only blockchain configuration plus researched-contract
  resolution and an approval-bound, durable transaction lifecycle with typed
  transport evidence and race-safe recovery.
- Harden browser, code, policy, confirmation replay, and Focus smoke paths found
  during provider-backed MiniMax validation.

## Current package line - 2026-10-07

- Type the existing submitted-turn stream and result lifecycle, including
  cancellation and approval resolution, without adding another SDK runtime.
- Expose existing timeout, approval, cancellation, result metadata, and usage
  facts through the public Agent facade and ship PEP 561 typing metadata.
- Fail clearly when decorated tools lack the canonical runtime registry or a
  runtime omits its canonical response body; preserve `0.1.x` support for one
  schema-valid JSON object wrapped in provider prose.
- Retain opt-in successful workflow observations in the existing Skill store
  and expose read-only authoring readiness without changing skill activation or
  model-owned selection.
- Preserve delegated artifact verification, complete structured tool evidence,
  durable blockchain preparations, exact approval identity, and reliable Focus
  prompt submission and completion behavior.
- Add a config-disabled commerce order lifecycle foundation with exact
  one-time approvals, durable submit-once recovery, tracking, cancellation,
  return and refund preparation, typed handoff, and deterministic evidence.
  Production commerce remains unavailable until a provider passes sandbox and
  release gates.

## Current package line - 2026-10-06

- Harden the public Agent SDK with decorated-tool support, isolated sessions,
  project and profile configuration, typed provider failures, deterministic
  cleanup, and reliable structured-output decoding.
- Unify web search and fetch provider defaults, fallback ordering, and runtime
  readiness reporting while preserving explicit provider requests.
- Keep scheduled-task creation and task inventory on the same canonical task
  manager so newly created schedules remain visible to CLI and Desktop users.

## 0.1 - 2026-10-05

- Establish the `0.1.x` compatibility contract for documented root Python
  exports, console scripts, and CLI command paths.
- Add credential-free web search fallbacks while keeping configured provider
  credentials out of model-visible request arguments.
- Improve evidence-backed itinerary research with explicit provider readiness,
  source preservation, same-session follow-ups, and truthful incomplete
  outcomes.
- Keep multiline paste in one Focus draft and make slash-command submission
  follow the same readiness and completion checks as ordinary prompts.
- Stabilize API shutdown ordering, optional runtime startup imports, and
  provider-free execution in isolated checkouts.
- Require clean wheel and source-distribution installation checks on Linux,
  macOS, and Windows before publication.

## Current package line - 2026-09-21

- Streamline provider onboarding and remote sandbox startup while preserving
  explicit provider configuration and SDK transport boundaries.
- Preserve collaborative task-plan continuity across Focus room restarts,
  checkpoints, transcript recovery, and resumed execution.
- Retain durable replay proof for learned-skill proposals, including exact
  evidence and inspection records across storage and delivery.
- Keep packaged Windows console handling active through lifecycle and PID-file
  cleanup so foreground daemon shutdown completes deterministically.

## Prior package line - 2026-09-21

- Complete graceful shutdown for the packaged Windows daemon through native
  console events, including deterministic PID-file cleanup.

## Prior package line - 2026-09-21

- Strengthen long-running coding projects with durable workspace-bound
  handoffs, plan and verifier continuity, and reliable recovery through Focus.
- Add collaborative Focus rooms with authorized task delegation, non-waking
  peer notes, typed handbacks, and restart-safe completion behavior.
- Preserve proof-bound learned skills and authored-tool drafts with deduplicated
  evidence, truthful trust diagnostics, and exact inspection records.
- Run remote sandbox work through the official Daytona SDK transport and
  support authored tools in those remote environments.
- Verify binary runtime publication independently and handle graceful OpenMinion
  daemon shutdown correctly on Windows.

## Prior package line - 2026-09-18

- Add approval-gated long-running coding projects from Focus with typed
  proposals before durable execution.
- Preserve success criteria through planning, checkpoints, verification,
  repair, delegated review, restart and reporting.
- Add project status, inspection, pause, resume and cancellation controls,
  with additive task API progress reporting.
- Activate explicitly requested tools before dispatch while keeping semantic
  tool selection model-owned, and preserve repeated write approval behavior.
- Refine Focus project and terminal presentation and strengthen independent
  Focus and model-neutral tool evidence gates.

## Prior package line - 2026-09-18

- Keep published runtime feed and immutable record JSON compatible with the
  single-version-owner guard, while continuing to reject duplicate live
  version literals in source, scripts and unrelated release files.
- Add regression coverage for the precise metadata path exception and retain
  protected publication, pinned-record hashes and merge-commit ancestry.

## Prior package line - 2026-09-17

- Raised shared execution defaults to 100 tool calls per run, 50 calls per
  tool, 200 weighted tool-cost units, 100 loop steps and a 300-second brain
  turn budget. JSON parsing and standalone policy now use the same defaults.
  Explicit saved limits remain unchanged; mode-specific and client timeouts
  still apply independently.

## Prior package line - 2026-09-13

- Improved ContextCtl memory continuity across session start, active work,
  prior-session artifacts, and selected-record attribution.
- Strengthened long-running project execution with durable plan, revision,
  recovery, verifier, and terminal-outcome lineage across resumed work.
- Added configurable per-turn usage reporting, recent token history,
  privacy-safe telemetry export, and trace diagnostics.
- Improved provider and model setup, contextual slash-command help, terminal
  status transitions, and bounded tool-result presentation.
- Added relative one-time schedules with aligned scheduler readiness and
  lifecycle behavior across interactive and background execution.
- Added typed delegated artifact review bound to immutable digests and
  verifier references.
- Hardened fresh-context handling, sidecar recovery, repository instruction
  boundaries, active-checkout execution, and telemetry shutdown.

## Prior package line - 2026-09-08

- Added explicit interactive session closure and aligned close, release, and
  resume lifecycle behavior across terminal, ACP, API, gateway, and cached
  agent-service paths.
- Improved memory and context evidence so active artifacts, final-use
  attribution, runtime traces, and resumed sessions retain the same authority.
- Enforced vault-scoped Obsidian graph navigation, preserved explicit graph
  source priority, and clarified copyable typed record identifiers.
- Hardened artifact access by rejecting inactive records, bounding rendered
  views, and keeping summary hashes out of artifact references.
- Improved local daemon setup while preserving the package ownership boundary
  between OpenMinion and separately distributed clients.
- Added manifest-backed critical regression gates and stabilized provider-free
  CLI, MCP, and standalone E2E proofs.

## Prior package line - 2026-09-07

- Added an Obsidian vault graph workflow that composes SophiaGraph document
  sources and PragmaGraph repository sources through typed, attributable graph
  query, neighborhood, refresh, and viewer operations.
- Added restart-safe remote session event inspection, task activity links, and
  typed approval resume while requiring authentication for non-loopback API
  binds.
- Added digest-pinned offline plugin installation with staged validation,
  collision checks, explicit config declarations, and exact rollback of failed
  installs.
- Completed scheduled-task lifecycle support across CLI, API, daemon, cron,
  durable coordination, health reporting, and resumed execution.
- Improved first-run continuity, provider setup, and Focus model connections
  while preserving configured model identity across setup and runtime.
- Restored context recall and final-use attribution without duplicating memory
  effects during replay, and aligned memory access across runtime services.
- Unified agent identity activation and storage paths so runtime, CLI, and
  service boundaries retain the same effective identity.
- Hardened artifact ingestion, aliases, retention, views, and lifecycle cleanup
  with durable ownership and safer context handoffs.
- Made SQLite snapshot backup names collision-safe, closed snapshot handles
  deterministically, and made daemon PID discovery and shutdown portable.
- Tightened tool, plugin, MCP, telemetry, path-policy, and provider-registration
  boundaries while preserving public runtime behavior.
- Preserved room agent identity through interactive startup and kept
  participant-attributed transcript styling consistent across streamed and
  stored room messages.

## Prior package line - 2026-09-05
- Added approval-bound repository delivery across Git remotes, pull request
  updates and merges, workflow dispatches, check inspection, and GitHub
  releases, including preflight validation and uncertain-effect reconciliation.
- Persisted project plans, repository lifecycle state, tool scopes, terminal
  outcomes, and scheduled check continuations across pauses and resumed work.
- Bound delegated review to artifact digests and durable child dispositions so
  corrected artifacts retain auditable review evidence.
- Added portable skill-bundle admission and authoring support plus a reviewed
  repository-delivery procedure with explicit stop rules.
- Consolidated interactive Focus onto the canonical terminal renderer, added
  root `--version` and repeatable `--add-dir`, and made workspace trust and
  transient grants explicit.
- Strengthened memory retention, final-use attribution, replay suppression,
  corrupt-shape rejection, and truthful recall paths.
- Hardened tool, plugin, extension, and MCP ownership boundaries while adding
  exact Python symbol ranges and clearer protocol and operator behavior.
- Added typed social-signal watches with bounded fetching, durable scheduled
  execution, and visible delivery outcomes.
- Added read-only security audit reports and completed bounded blockchain
  diagnostics and explicit approval flows while preserving the optional
  dependency boundary for bare package installs.
- Improved provider retry enforcement, pairing and session status, token and
  provider telemetry, CPU measurement, and bounded Focus history.

## Earlier package line - 2026-09-02

- Added durable human-agent Focus rooms with explicit participant roles,
  multi-agent routing, direct addressing, participant controls, attributed
  streaming, persisted transcripts, and interruption support.
- Added agent-scoped model connection catalogs and model selection commands
  while preserving configured defaults through setup and runtime handoffs.
- Reduced CLI startup and renderer weight through lazy presentation imports,
  optional Textual and figure dependencies, and packaged Textual styles.
- Hardened delegated and A2A execution with idempotent concurrent starts,
  cancellation safety, stale-work recovery, durable results, and isolated
  per-turn tool and scope state.
- Expanded correlated provider, HTTP, TUI, and request-response trace artifacts
  and strengthened long-running smoke and verification reliability.
- Restored formatter enforcement and aligned service typing and method-size
  quality ratchets with the formatted release candidate.

## Earlier package line - 2026-09-01

- Generalized authenticated active-turn status and streaming contracts with
  bounded history, redaction, terminal controls, and durable interruption
  evidence across API, daemon, Focus, and autonomy surfaces.
- Hardened deterministic deep-work execution with explicit evidence contracts,
  resumable checkpoints, read-only review composition, and reliable tool,
  verification, and complex-workflow closeout behavior.
- Strengthened memory capture and recall with atomic capture bundles, explicit
  retrieval eligibility, surfaced evidence, session continuity, and aligned
  SQLite and PostgreSQL persistence paths.
- Added scoped per-agent command grants, structured host inventory and
  execution contracts, and validated external skill bundles while preserving
  existing tool and skill ownership boundaries.
- Added a bounded blockchain inspection, preparation, and transaction lifecycle
  with explicit financial authorization, secret bridging, persistent
  confirmations, and local certification coverage.

## Earlier package line - 2026-08-29

- Preserved typed project-turn and provider failures across CLI, runtime, cron,
  checkpoint, and resume boundaries so verification cannot mask failed work.
- Carried failed verifier output into later project cycles and resumed processes
  so repair uses the durable failure evidence.
- Preserved active plan-step progress when a running plan is redeclared and
  aligned project-cycle claim windows with configured turn and verifier limits.
- Preserved provider, service, and model identity in canonical LLM lifecycle
  events, including native Ollama profiles.
- Strengthened provider-session and sustained-autonomy certification harnesses
  with effective identity, durable lineage, bounded redacted reports, and
  restart evidence without claiming unfinished long-duration certification.

## Earlier package line - 2026-08-27

- Hardened agent-loop recovery with durable failed-turn outcomes, bounded tool
  and finalization recovery, and clearer terminal evidence.
- Strengthened delegated and A2A execution with explicit child contracts,
  idempotent job ownership, normalized asynchronous lifecycle results, and
  accurate child budget accounting.
- Improved memory recall precision, weak-result abstention, capture assurance,
  context disclosure, usage attribution, and connection lifecycle handling.
- Added a shared public status-message catalog while preserving detailed
  technical output and responsive Focus delegation flows.
- Expanded resilience, autonomy, and package-quality certification across
  runtime continuity, control-plane polling, release gates, and regression
  coverage.

## Historical package line - 2026-07-12

- Added typed SophiaGraph namespace filters to existing `memctl` list/search
  commands and local memory-record HTTP routes.
- Preserved all eight namespace dimensions in the integrated SQLite store and
  retained permanent legacy scope compatibility.
- Documented the local-operator security boundary and deterministic namespace
  smoke coverage.

## 0.0.1 - 2026-06-23

### Initial public preview release

- Added package-local public docs and release-readiness references.
- Hardened first-run CLI behavior for `verify smoke` and default config output.
- Aligned package metadata, root exports, examples, and release-sensitive tests
  to the public package line `0.0.1`.
- Reconfirmed package-local release proof with targeted metadata/version tests,
  root import smoke, `ruff check .`, `make lint`, `python -m compileall
  examples`, and local wheel/sdist builds.

### Notes

- The project is still in public preview and under active development.
- This entry establishes the initial public `0.0.1` package line.
