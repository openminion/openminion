# OpenMinion Changelog

Status: active
Last updated: 2026-10-29

This file tracks package-facing release notes for `openminion`.

## Unreleased

- Reject duplicate Handoff resolved names when an agent is built or a delegate
  family is compiled, so two handoffs resolving to the same name fail with an
  actionable message instead of a `profile_id values must be unique` error at
  registration time (Refs #476).

- Keep native structured-output instructions limited to formatting so research
  synthesis can answer from supplied evidence. Preserve forced `submit_output`,
  schema validation, and caller-owned routing/judgment contracts (Refs #530).

- Give the first two decision turns the normal `2,200`-token context-pack
  target instead of the former `1,500`-token target. This leaves more room for
  the user's initial request, instructions and early clarification without
  changing later-turn scaling, completion budgets, provider output limits or
  saved agent profiles.

- Stage no `tool_outcome` memory candidate for an outcome classified as
  `policy_denied`. When the user denied a tool call, or the call was still
  waiting for the user's confirmation, the brain staged a `policy_denied`
  `tool_outcome` candidate in agent scope whose content and metadata held the
  call's arguments, also when the agent's memory policy allowed the write. No
  candidate is now staged for any `policy_denied` outcome: an ask-user command
  returned for a tool call, such as the user's denial, a pending confirmation
  or a policy denial, and a tool result with status `needs_user` or
  `blocked`, which includes a tool that ran and returned
  `requires_user_takeover`. Failure, timeout and allowlisted success
  candidates, the turn's `memory.write` answer and the policy and approval
  events are unchanged (Refs #524).

- Give each PostgreSQL module its own schema, and refuse a layout whose
  ownership is not proven before any table is written. Runtime storage and
  every module wrote into the one schema the configured URL selects, so they
  shared `alembic_version` and `om_meta`, and `CREATE TABLE IF NOT EXISTS`
  let one owner adopt another's `sessions`, `events`, `idempotency_keys`,
  `memory_records`, `migrations` or `agents`. Runtime storage keeps that
  schema; each module now uses `<base>__<module id>`, created inside its
  migration transaction (or, for a store without Alembic, in its own
  transaction before its first table), on a connection that keeps the URL's
  options and adds `-c search_path`. A module whose history is already in the
  base schema, such as a dedicated PostgreSQL memory database, stays there
  with no move or stamp. A table counts as an owner's only when that owner's
  ledger is recorded in the schema and the table exactly matches one listed
  for that ledger in `postgres_histories.json`, which was measured from every
  published release and this code. An existing `<base>__<module id>` is used
  only when it is empty or holds the module's proven history; one with tables
  but no ledger of the module, such as a first start stopped before
  `om_meta`, is refused. A shared or unknown layout, for example
  the A2A audit store on the runtime URL, or memory first and then runtime
  storage, is refused with a message that names its tables, for the owners
  that use them only. Upgrade: run `openminion storage migrate --backend
  postgres` before the module stores start, and connect the module stores
  directly or through a pooler that passes the `options` startup parameter.
  `docs/runtime-database-upgrades.md` gives the details and the operator's
  relocation steps (Refs #446).

- Document the upgrade for memory consolidation jobs from packages before
  0.1.15. Since 0.1.15 the daemon runs a job as memory consolidation only when
  its task record has the `memory_consolidation` kind that
  `task.consolidate_memory` stores. A job created by `task.consolidate_memory`
  of a package before 0.1.15, or any job with the reserved payload and no such
  record, fails its first run with the provenance message, without a turn or
  capture recovery, and is paused. `docs/runtime-database-upgrades.md` now
  describes how to inspect those jobs, cancel or pause each one before
  creating its replacement through `task.consolidate_memory`, and check that
  the old job is not enabled and the replacement is, for the same agent; that
  jobs created by 0.1.15 or later keep running next to the replacements and
  are not replaced on later upgrades; that the raw cron API rejects the
  reserved payload; and that pending capture intents are kept and recovered as
  before. New tests create the job through the tool's handler and run it
  through the daemon's scheduler, whose own task manager reads the stored kind
  (Refs #456).

- Let `Agent.run()` and `Agent.run_stream()` answer a waiting confirmation.
  A second `run()` after "Policy confirmation required." started a new thread,
  so the reply did not reach the tool call waiting on the first one. Both
  methods now accept an optional `thread_id` that sends the turn to that
  thread, and `AgentRunResult.thread_id` returns the turn's thread. Omitted or
  blank, it changes nothing (Refs #541).

## Current package line - 2026-10-29

- Ask the agent's `memory.write` authorization before a turn captures memory.
  A turn's auto fact extraction and its capture bundle write ran without a
  policy decision, so with an identity hard constraint such as `NEVER write
  memory records` an ordinary turn still wrote its candidate, receipt and
  `memory.capture.result`, while pending-capture recovery was refused for the
  same capture. The brain bridge now asks once per turn, with the check and
  internal actor that recovery uses and the turn's channel, session and root
  turn. When the write is not allowed, including when it needs approval, the
  turn makes no extraction request, writes nothing for its capture and asks no
  approval for it, and its reply is unchanged. Its intent stays pending with
  `CAPTURE_RECOVERY_UNAUTHORIZED`, and recovery commits it once the write is
  allowed. A diagnostic turn (`openminion agent-check` and the turn smoke
  check of `openminion doctor`), whose capture is disabled, no longer makes
  the extraction request whose result it never wrote (Refs #448).

- Close what a failed `APIRuntime` start built. When a start step raised,
  what the start had already built stayed open: the runtime storage (its
  SQLite connection, or the PostgreSQL connection pool and its pool-health
  emitter thread), the telemetry store, the module databases and the
  services. The caller got the original error, but those objects stayed open
  until a garbage collection freed them, and the PostgreSQL pool and emitter
  not even then. Each step of the start now closes what it built with the
  existing closes and re-raises the original error, also when one of those
  closes fails. Once the runtime object exists, a later failure closes it
  with `APIRuntime.close()`, which also stops a runtime manager that already
  started. A successful start hands the same objects to the runtime as
  before (Refs #447).
- Find the compress module's migrations in `openminion storage migrate`.
  Migration discovery looked for a module's storage package as
  `openminion.modules.<id>.storage`, but the compress package is
  `openminion.modules.context.compress.storage`, so `storage migrate --module
  compress` and compress's own `run_migrations` failed with `Alembic
  configuration missing` on SQLite and PostgreSQL and wrote nothing.
  Discovery's map of nested module packages now lists compress, as it lists
  `authoring` and `commerce`, and compress migrates to `0001_baseline` with
  its tables. The compress migration and discovery for other modules are
  unchanged (Refs #493).

- Skip a turn's decision record and tool outcome candidate when the agent's
  `memory.write` is not allowed. The brain wrote a `decision` record for every
  routed turn and staged a `tool_outcome` candidate for a refused tool call,
  with the call's arguments, without asking the memory policy, so with an
  identity hard constraint such as `NEVER write memory records` a refused
  `memory.write` call still left its content in the memory candidates. Both
  writes now use the turn's `memory.write` answer, which the brain bridge asks
  on every turn, including scheduled consolidation and diagnostic turns. When
  it is not `allow`, including when it needs approval, the decision record is
  skipped (`brain.decision_memory.skipped`, `memory_write_not_authorized`) and
  no candidate is staged. The working state keeps the decision metadata, and
  nothing asks for approval for these writes (Refs #496).
- Keep a room's selected active agent a participant when the selection runs
  at the same time as a leave of that agent on another connection.
  `set_active_agent` checked that the agent was an active participant and
  then wrote `active_agent_id` in a separate statement, outside the
  session-row lock that joins and leaves take, so a leave (`/kick`) could
  commit between the two and the selection (`/activate`) then stored an agent
  that had left. The selection now reads the agent's membership after taking
  that lock and writes the active agent in the same transaction. A leave
  during a selection waits for it to commit and then runs its fallback; a
  selection during a leave waits for it and is refused with the existing
  `Agent ... is not an active participant` error. The errors and the
  returned session are unchanged (Refs #495).
- Migrate the `controlplane` and `telemetry` modules on PostgreSQL. Their
  `0001_baseline` revisions applied SQLite's `INTEGER PRIMARY KEY
  AUTOINCREMENT` on every dialect, so `openminion storage migrate --backend
  postgres --module controlplane` and `--module telemetry` failed with a
  syntax error and wrote nothing. On PostgreSQL they now create the column
  types the modules' PostgreSQL stores already use: `SERIAL PRIMARY KEY` ids
  and a `DOUBLE PRECISION` telemetry timestamp. Tables the stores created
  before the migration are kept with their rows; an existing table whose id
  or timestamp has another type stops the upgrade with an error naming the
  column, and nothing is written. The controlplane store also opens a
  database the baseline created, where it failed with a duplicate
  `next_attempt_at` column, on SQLite too. The SQLite DDL and the revisions
  are unchanged (Refs #494).

## Current package line - 2026-10-28

- Start `APIRuntime` with PostgreSQL runtime storage (`storage.backend:
  postgres`). The start read the runtime record store's SQLite connection for
  every backend and failed with `runtime record_store does not expose sqlite
  connection`, so commands such as `openminion room create` exited with a
  startup error. `APIRuntime.storage_connection`, and the compatibility
  wrapper's `OpenMinionRuntime.storage`, are now the SQLite connection on
  SQLite, unchanged, and `None` for other backends; sessions and rooms go
  through `runtime.sessions` on either backend. A SQLite record store without
  a connection still fails the start with the same error. What is covered on
  PostgreSQL is listed in `docs/runtime-database-upgrades.md` (Refs #437).
- Report the migration error in `openminion storage migrate` entries. When a
  module's `MigrationReport` failed, its entry had status `error` but no
  message, and the text output printed only `FAIL <module>`. The entry now
  carries the report's `error`, and the text line prints it after the module
  id. The `applied` list is left out: it read a field that `MigrationReport`
  does not have, so it was always empty. Exit status is unchanged (Refs
  #446).
- Keep a room's active agent when a first agent join runs at the same time as
  another join, an explicit selection or a leave on another connection. The
  join read the room after its participant row had committed and, when the
  room had no active agent, wrote its agent as active in a separate
  statement. A second join could then replace the first activation, a join
  could overwrite a `set_active_agent` selection (`/activate`), and a join
  whose agent left meanwhile (`/kick`) stored that agent as active. The join
  now reads and writes the active agent in its participant transaction, under
  the session-row lock that joins and leaves already take, with the same
  empty or blank check. The first activation a join commits stays until a
  selection or a leave changes it, and a selection or leave made during a
  join waits for it to commit, then applies. The active agent is still not
  guaranteed to be the earliest join. Legacy owner records still activate no
  agent (Refs #393).

- Stop pending-capture recovery from calling the extraction model for an
  intent that the current memory store cannot commit. After the identity and
  authorization checks, recovery asks the memory owners whether the store can
  commit an atomic capture bundle (`supports_capture_bundles()`, the existing
  method check, now readable without a write). With a backend without
  `apply_capture_bundle`, such as `reference-sqlite`, each intent stays pending
  with its counts and `CAPTURE_RECOVERY_UNSUPPORTED` diagnostic, and no source
  message is read and no extraction request is made; before, every memory
  consolidation run extracted again for each pending intent up to its
  `batch_limit`. The same intents are recovered once the agent uses a store
  that can commit them. Under the audit wrapper, a store without the method,
  such as the in-memory store, is now reported the same way; before, recovery
  extracted and then raised `AttributeError`. A memory API without the new
  predicate keeps the previous order, and the bundle write and its errors are
  unchanged (Refs #449).

- Preserve scheduler-owned memory-consolidation context through runtime ingress
  and gateway prompts, reject raw cron creation of reserved consolidation
  payloads, and pause unverifiable legacy jobs with clear recreation guidance.
- Report the configured telemetry exporter posture consistently, including
  empty and error states, and connect invocation telemetry to current and recent
  token usage without exposing content by default.
- Harden MCP interoperability by refreshing tool definitions once after a
  protocol header mismatch, enforcing the 1 MiB SSE event boundary, and
  validating the official Python SDK 2.3.0 client/server paths.
- Close artifact lifecycle gaps by validating retention inputs, normalizing
  alias expiry, preserving historical cleanup, and carrying the existing
  retrieval controller through durable tool ingestion with per-item failure
  isolation.
- Complete task lifecycle controls across Focus, CLI, and API with canonical
  policy, run-now, scheduler status, bounded history, deterministic one-shot
  behavior, and trusted cron/watch metadata. Live MiniMax acceptance confirmed
  material-change watch delivery without runtime-owned semantic parsing.
- Add opt-in isolated source, image, AWS snapshot, and local web/API security
  assessments with bounded recurring evidence, SARIF export, exact finding
  comparisons, cancellation, expiry, revision limits, and canonical closure
  verdicts. Remote, cloud, and login assessment remain outside this release.

## Current package line - 2026-10-27

- Document what `openminion storage backup` covers in a SQLite runtime home.
  The command snapshots one file and refuses the module databases that carry
  their own `application_id` (artifact, authored tools, brain, identity,
  memory, policy, retrieve, skill and telemetry). The upgrade guide now lists
  the files of a default runtime home with the command's result for each,
  and backs up the refused ones with SQLite's backup API while every writer
  is stopped. For a rollback it checks each backup first, then moves each
  database aside together with its `-journal`, `-wal` and `-shm` before it
  puts the backups in place, so a companion left by a writer that did not
  finish is neither applied to a restored file nor deleted. It also states
  where the brain working state gets `turn_evidence` and
  `turn_evidence_trace_id`: the typed save adds them, while the turn-start
  reset rewrites the stored state with its existing keys, so the first row a
  runtime turn writes after the upgrade can lack both. The working state
  lives in the brain's session store, not in the runtime database
  (`docs/runtime-database-upgrades.md`, Refs #452, #419).

- Accept the canonical `time.now` tool's `utc` timestamp as exact-date
  evidence. Current-date answers that successfully called the time tool no
  longer fail the freshness verifier after receiving a grounded timestamp.
- Close what a runtime memory assembly build obtained when the build fails
  partway. The artifact store, and the memory store with its audit sink for
  the built-in backend, are closed once each through their current owner,
  and the build's own error reaches the caller even when a close fails. A
  PostgreSQL memory store disposes its engine instead of keeping a server
  connection. Successful builds hand both to the assembly as before
  (Refs #390).
- Pass validated Pydantic-model and dataclass arguments to `@tool` functions
  as instances instead of plain dicts. The decorator handler called
  `model_dump()` on the validated arguments, so a tool typed `span: Range`
  received a `dict` and failed with `AttributeError`. The handler now
  validates mappings with the tool's args model, accepting both declared
  aliases and canonical field names, and passes the validated field values;
  defaults and `Annotated` constraints apply and invalid mappings raise
  `ValidationError`. With Pydantic before 2.11, a nested model that declares
  aliases is accepted by alias only. This changes the decorator handler
  only: the shared built-in tool mapping contract is unchanged, so a normal
  SDK `Agent.run` call with an aliased argument, top-level or nested, is
  still rejected before the handler runs, as it is without this change. That
  runtime alias bug is tracked in #454 (Refs #435).
- Make `python -m openminion.modules.storage` exit with the status that
  storagectl's `main()` returns, as `python -m openminion.modules.storage.cli`
  already does. A command that prints `"ok": false` and returns 2, such as
  `pool-stats` without a PostgreSQL URL, now exits 2 instead of 0. The
  printed output is unchanged, and a command that raises still exits 1
  (Refs #450, #412).

## Current package line - 2026-10-26 update

- Raise the default tool budgets for longer real-world tasks to 160 calls per
  run, 80 calls per tool, 320 weighted tool-cost units, and 64 adaptive tool
  calls. Explicit saved limits and independent client timeouts remain
  unchanged (Refs #438).
- Check an entry step's text answer against the turn's typed freshness
  obligations before it ends the turn, as a judge's closing answer is
  checked. The respond route now finalizes the entry text with
  `final_close_message`: an answer whose evidence meets the obligations is
  kept unchanged, and one without the required live-data or exact-date
  evidence becomes the existing freshness-failure message, with the
  verifier's reasons in the freshness diagnostics. This covers a first entry
  step that answers in text and a continuation step after the judge or the
  closure freshness guard continues the turn. A continuation's evidence is
  the action result the turn's execution handed to it: the working state
  keeps it with the turn's trace id (`turn_evidence`,
  `turn_evidence_trace_id`), so a lookup that would satisfy a direct close
  also satisfies the continuation's text, and a result from another turn is
  not evidence. Every working state saved from this version on contains both
  fields, and a runtime from before it rejects such a state: upgrade every
  process that shares the runtime state together and roll back only with the
  matching pre-upgrade backup (`docs/runtime-database-upgrades.md`). A state
  saved before this version loads with neither field set. Clarify questions,
  other respond decisions and the tool/judge path are unchanged (Refs #419).
- Make `storagectl restore` leave the target equal to the snapshot. storagectl
  now dispatches `restore` before it builds its `StorageEngine`, as it already
  does for `pool-stats`, `dr-drill` and `a2a-archive-old`, so its SQLite record
  store no longer holds a connection open on the database file that the
  restore replaces. On native Windows the restore failed with
  `PermissionError`; on Linux a restore onto a new path reported `ok` but left
  a database without most of the snapshot's tables. The restore's validation,
  progress reporting, JSON output and errors are unchanged (Refs #412).
- Let memory consolidation cron runs recover pending terminal capture intents
  and then submit their scheduled turn. The cron executor passed the agent
  service's keyword-only capture candidate extractor to the recovery's
  positional callback, with the runtime session store as its session API, so
  every consolidation run with a pending intent failed with `TypeError` before
  its turn. A small adapter at the cron call site now calls the extractor by
  keyword with the brain session API that the agent's runner uses for turns.
  Recovery authorization, capture identity, the capture bundle write and the
  pending/unsupported accounting are unchanged (Refs #420).
- Let `memctl inspect` run without `--scope`, or with an empty one, as the
  command intends: it inspects with the default inspection identifiers and
  reports stats for every scope, instead of exiting with a traceback from the
  scope parser. A non-empty invalid scope is now reported as
  `Error: invalid scope: ...` on stderr with exit code 1, as `memctl search`
  and `memctl list` report one, before the memory service opens. JSON and
  human output report the same normalized scope (Refs #413).
- Let `memctl gc` purge soft-deleted records with a PostgreSQL memory store.
  memctl runs gc on the `AuditedMemoryStore` it builds, and
  `purge_soft_deleted` chose its SQL by the wrapper's type, so a PostgreSQL
  store took the SQLite branch and the command exited with
  `'PostgresMemoryStore' object has no attribute '_connect'` after the
  retention pass. The purge now runs on the store the wrapper holds, using
  the one-level wrapper resolution the retention helpers use since #392,
  which moves into the purge module. The purge SQL, auditing and errors are
  unchanged (Refs #401).

## Current package line - 2026-10-26

- Keep research incomplete when synthesis fails or returns a blank answer,
  even when sources were collected. Retain checkpoint evidence for resumption
  and project-owned incomplete status; record exception types without provider
  response bodies.
- Give tool calls that a provider returns without ids a full-UUID batch
  prefix (`call_<uuid>_<n>`) instead of a bare `call_<n>`. Tool transcript
  events are keyed by turn and call id, so two id-less response batches in the
  same turn (for example Ollama with the default `fallback` tool strategy
  after the closure gate sends the turn back through entry) both produced
  `call_1` and failed the turn with `session event idempotency conflict`.
  Provider-supplied ids and batch order are unchanged, and replaying an
  already-normalized call stays idempotent (#410).

- Let SDK turns complete with an external memory backend that does not
  implement atomic capture bundles, such as the bundled `reference-sqlite`
  adapter. The backend adapter now reports the missing optional
  `apply_capture_bundle` with the existing `CaptureRecoveryUnsupportedError`
  instead of raising `NotImplementedError` out of `Agent.run`, so the turn's
  capture intent stays pending with `CAPTURE_RECOVERY_UNSUPPORTED`, as it does
  for the local JSONL memory adapter. Backends that implement the method are
  called as before, and their errors still propagate (Refs #397).
- Select run events with a case-sensitive `run.` type prefix in
  `list_session_runs` and `list_session_run_events` on SQLite, as on
  PostgreSQL and as the thread lifecycle, the invocation terminal and every
  runtime writer already do. SQLite `LIKE` matched the prefix
  case-insensitively, so on SQLite an event stored with a `RUN.` or `Run.`
  type was counted in run listings, run event lists and the run status shown
  by the runs API, `openminion status` and owner diagnostics; it no longer
  is. No runtime writer emits such a type; it can come from the generic
  event append APIs or an import. The state is still read from the type
  suffix in any case (`run.COMPLETED` is completed), stored events are not
  changed, and the generic `list_events` and `count_events` prefix filters
  are unchanged (Refs #396).
- Close the memory store and audit database that a `memctl` command opens
  when the command ends, whether it succeeds or fails. The command's service
  now owns the store and audit sink it creates and closes them once, through
  the command's Click context; a service built by the factory outside a
  command is closed by its caller with `close()`. The audit database used to
  stay open until garbage collection, so an in-process caller on Windows
  (such as typer's `CliRunner`) could not rename or delete it (Refs #403).
- Keep a turn's freshness contract, obligations and diagnostics on an entry
  step that has no new user input and no valid typed `freshness` contract,
  such as the continuation step after the closure freshness guard turns a
  close into a continue. The step used to replace them with the classifier's
  default for empty input, which dropped the exact-date, live-data and
  sources obligations the guard was enforcing. A valid typed contract still
  replaces them, new user input is still classified, and a first step with
  neither keeps the default. The step emits `brain.freshness.entry_fallback`
  with reason `retained_turn_contract` (Refs #395).
- Report the deepest key history in `memctl inspect` (`history_summary` with
  `--json`, the `Deepest history` line otherwise) for the SQLite store that
  memctl wraps in its audited store; it was always missing there. A store
  without that summary, such as PostgreSQL, and a database without keyed
  history still report none (#402).
- Let PostgreSQL memory retention and purge remove records and candidates
  that carry evidence. The shared evidence decoder now keeps the list that
  PostgreSQL returns for the JSONB `evidence_json` column, instead of parsing
  its Python text form, so confidence decay, summary retention, scope
  capacity, stale insight eviction and the purge of soft-deleted records and
  promoted or rejected candidates no longer fail with `JSONDecodeError` on an
  item with evidence, and remove its artifact refs. SQLite JSON text, empty
  values and `None` decode as before (#400).
- Close the SQLite connections that twelve storage and API runtime tests open
  themselves, and the API runtime one of them builds, before the test removes
  its temporary directory or restores the database, so these tests pass on
  native Windows as they do on Linux. Test-only; no package behaviour changes
  (#408).
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
- Report an internal brain runtime failure (the state machine's `fatal_error`
  path, such as a tool returning a value that cannot be JSON-serialized) as
  `run_state="failed"` on `AgentRunResult` and in the persisted run, instead of
  `"completed"`. The existing structured `error_code`, `error_message` and
  `error_details` stay in the result metadata. Recoverable tool errors that the
  brain answers without a fatal transition still complete (Refs #384).
- Close the runtime memory assembly with the `external` memory backend
  provider: the memory service closes the external backend it owns once,
  through a new `BackendMemoryStoreAdapter.close()` that does nothing for a
  backend without `close()` or one it does not own, and the assembly then
  closes its artifact store. Before, the close raised `AttributeError` and left
  the artifact store open. With this provider the assembly also no longer
  creates the memory store and audit sink that only the built-in backend uses,
  so a PostgreSQL `memory.store` setting opens no engine and an unusable
  `memory.store` setting no longer fails the build (#388).
- Apply memory retention in the runtime session lifecycle with a PostgreSQL
  memory store. The lifecycle hands the retention helpers the runtime's
  `AuditedMemoryStore`, so confidence decay, summary compression and deletion,
  per-scope capacity and stale insight eviction took the SQLite path and failed
  with `'PostgresMemoryStore' object has no attribute '_connect'`: no retention
  was applied, every turn logged `memory.session_lifecycle failed`, and the
  first-turn summary compression failed silently. The helpers now run on the
  store behind the audit wrapper, as they already did with SQLite, and the
  lifecycle completes once per session (#392).
- Close the memory store, audit sink and artifact store that the bootstrap
  memory fallback creates. When `build_gateway_service` builds the agent memory
  itself, `GatewayService.close()` now closes it once, and the adapter that
  `build_agent_memory_service` returns gets a `close()` that closes the runtime
  memory assembly behind it once, keeping the scheduler boundary. Before, the
  assembly was dropped and nothing could close them. An `agent_memory` passed
  to the gateway service stays borrowed, and the SDK runtime's memory gateway
  still closes nothing itself (#389).
- Session metadata filters work on PostgreSQL.
  `list_sessions(metadata_filter=...)`, which ACP `session/list` and Focus
  directory sessions use, emitted SQLite's `json_extract` and failed there.
  Runtime migration 15 adds `session_metadata_strings`: one row for each
  top-level member of a session's stored metadata whose value is a string,
  written by the session store in the same transaction as every session
  insert, metadata update and delete, on SQLite and PostgreSQL. The migration
  backfills existing sessions in batches inside its transaction, reading each
  stored `metadata_json` with `json.loads`, so metadata PostgreSQL cannot parse
  (NaN, Infinity, an escaped NUL or a lone surrogate) is covered; stored
  metadata is not changed, and a session whose metadata is not JSON rolls the
  migration back and is named in the error. On PostgreSQL a filter selects a
  session whose stored metadata, as `SessionRecord.metadata` reads it, has a
  top-level member named exactly as the key whose value is a string equal to
  the filter value. Names and values are literal; numbers, booleans, null,
  objects and arrays never match; and at most `limit` sessions are read.
  `list_sessions` still sends a false filter value (`None`, `0`, `False`, an
  empty container) as `""`, so `{"surface": None}` matches an empty string as
  before; PostgreSQL raises `TypeError` for any other filter value that is not
  a string, where SQLite compares it as an SQL value. SQLite keeps its
  `json_extract` reader, so the backends still differ where it reads more
  than a literal top-level string: keys as JSON paths, objects and arrays by
  their minified text, version-dependent escaped keys and NaN documents, and
  the first of two members with one name. A `SessionStore` built directly on a
  `RecordStorePostgres` now uses the PostgreSQL reader: the SQL comes from the
  record store's type, with no SQLite default. See
  `docs/runtime-database-upgrades.md` (#369).
- Upgrade note for runtime migration 15 (#369): stop every process that writes
  the runtime database, keep a pre-upgrade backup, upgrade all of them
  together, and never run a binary from before this change against the
  migrated database. Rolling back requires restoring the matching pre-upgrade
  backup. An older writer does not maintain `session_metadata_strings`: a
  session it creates has no rows, so a PostgreSQL metadata filter misses it,
  and a session whose metadata it changes keeps the rows of its earlier
  metadata, so a filter can return it for a value it no longer has (a stale
  match) or miss it for its new value.
- Let a parsed memory config select the `remote` store backend.
  `resolve_memory_backend` now reads the `StoreConfig` fields that
  `load_config` fills (`remote_endpoint`, `remote_timeout_seconds`,
  `remote_max_retries`) from a `MemctlConfig` or its `store`, so `memctl` with
  a `store.backend: remote` config file no longer fails with
  `remote backend requires remote.endpoint`. The configured timeout and retries
  are kept, including `max_retries: 0`. A nested `remote` mapping in a
  dictionary config still takes precedence as a whole, with its defaults
  unchanged (#391).

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
