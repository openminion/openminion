# OpenMinion Changelog

Status: active
Last updated: 2026-09-02

This file tracks package-facing release notes for `openminion`.

## Unreleased

- Added conventional root `--version`, predictable root-option parsing, and
  repeatable process-local `--add-dir` support for interactive workspaces.
- Narrowed default file roots to the active workspace, preserved configured
  absolute reads, and isolated transient workspace grants per turn and worker.
- Made implicit workspace trust and sandbox-unavailable host-command guidance
  explicit without enabling unsandboxed execution or persistent grants.

## Current package line - 2026-09-02

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

## Prior package line - 2026-09-01

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
