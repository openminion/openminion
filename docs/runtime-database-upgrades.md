# Runtime Database Upgrades

Status: active

The runtime database (sessions, messages, events and their derived tables) is
migrated by the runtime itself: the first process that opens it with a newer
package applies the pending runtime migrations. Each migration, including its
data backfill, runs in one transaction with its ledger entry, so a failure
leaves the database at the previous version with no partial upgrade.

Runtime migrations 13, 14 and 15 add data that every writer must maintain
from then on. A process from before such a migration does not maintain it, so
these upgrades need every writer to move together.

## Coordinated upgrade

1. Stop every process that writes the runtime database: gateways, API
   servers, workers, CLI sessions and scheduled jobs.
2. Keep a pre-upgrade backup. For a PostgreSQL runtime database use
   `pg_dump` (`openminion storage backup --backend postgres` prints the
   guidance). For SQLite, `openminion storage backup --sqlite <path>` takes a
   snapshot of one database file; back up every database of the runtime home
   as in [SQLite runtime home backup](#sqlite-runtime-home-backup).
3. Upgrade every process to the same package version, then start them. The
   first one applies the migrations.
4. Never run a process from before the upgrade against the migrated database.

Rolling back means restoring the matching pre-upgrade backup with the matching
older package. Downgrading in place is not supported: an older process ignores
the derived data and leaves it wrong.

If a migration fails, it raises, and the database stays at the previous
version. Fix the cause it reports and start again.

## PostgreSQL schemas per owner

On PostgreSQL, runtime storage and the modules used to write into the one
schema the configured URL selects. They shared `alembic_version` and
`om_meta`, and their `CREATE TABLE IF NOT EXISTS` adopted each other's
tables: `sessions`, `events`, `idempotency_keys`, `memory_records`,
`migrations`, `agents`, `session_turn_leases` and `session_retention_holds`
each have more than one owner. Since #446:

- Runtime storage keeps that schema, the *base schema*: `current_schema()`
  of the configured URL, which follows its `options`, `PGOPTIONS` and the
  role's or database's `search_path`.
- Each module uses its own schema, `<base>__<module id>`, for example
  `public__memory`. The module's connection keeps every option of the
  configured URL and appends `-c search_path="<base>__<module id>"` (a URL
  without `options` starts from `PGOPTIONS`). `openminion storage migrate
  --backend postgres`, the memory store and the A2A audit store create the
  schema inside the migration transaction, so a failed migration leaves no
  schema. The other module stores, before their first table, create it with
  `CREATE SCHEMA IF NOT EXISTS` and check it again in one transaction, which
  is rolled back if the check refuses.
- A module whose supported history is already in the base schema keeps using
  it there. Nothing is moved or stamped. This is the case of a dedicated
  database, for example a PostgreSQL memory database or a database where
  `storage migrate --module <id>` ran alone.

This covers every PostgreSQL module writer: `openminion storage migrate`
(also with `--plan`), the startup check, the memory store, the A2A audit
and state stores, the storage module's `a2a-archive-old` command, the
session store, the controlplane store and the other module store factories.
SQLite is unchanged. Code that hands a selected schema to
`MigrationRunner(postgres_schema=...)` or to a store takes it from
`select_postgres_module_schema` or `select_module_schema`; it is used as
given, without another check.

### What is checked before any write

One `READ ONLY` transaction reads only the base schema and
`<base>__<module id>`: their tables (`pg_class` kinds `r` and `p`); for
tables with an OpenMinion name, the columns, types, `NOT NULL` and primary
key; the ledger rows (`migrations`, `alembic_version`, `om_meta`); the
schema owner and privileges. Views, sequences, indexes and functions are
not read, so the guarantee is about tables: a sequence or index with a name
that runtime storage creates still makes its migration fail later, after
the earlier versions committed, as before #446.

The ledger is the `migrations` rows v1..vk for runtime storage; for a
module, `om_meta.module_id` with its `alembic_version` row (none once its
Alembic chain is downgraded to base, a history with no table), or, for a
module store without Alembic, with `om_meta.schema_head`. A table belongs to an
owner when that owner's ledger is recorded in the schema and one of the
histories listed for that ledger in
`src/openminion/modules/storage/migrations/postgres_histories.json` has the
table with the same columns, types, `NOT NULL` and primary key. Each table
is attributed on its own. An owner's history there is proven when every
table of one listed history is present and belongs to that owner alone, and
the owner has no other table there. A table that only resembles a listed
one (a subset or a superset of its columns, other types) is not evidence,
and neither is a table without its owner's ledger.

- Runtime storage starts when the base schema has none of the tables it
  creates or reads, or when its history there is proven.
- A module stays in the base schema when its history there is proven.
- A module uses `<base>__<module id>` when no `om_meta` in the base schema
  names it and every base table with one of its names belongs to other
  owners only. It also keeps `<base>__<module id>` when that schema holds
  its proven history, the base schema records no ledger of it, and each
  base table in question has a name that another owner uses too; its
  connections use only `<base>__<module id>`, so it never reads or writes
  those base tables. A base table with a name only the module uses could be
  its own rows, so the module is then refused instead (two locations).
- An existing `<base>__<module id>` is used only when it is empty, or when
  it records the module's ledger and holds one of its listed histories,
  proven. The storage engine's tables there (`core_events`,
  `core_sidecar_rows`, `sidecar_ingest_log`, which a module store creates
  next to its own) must then have the exact columns this code creates, as
  listed in `postgres_histories.json`. A `<base>__<module id>` with tables
  but no ledger of the module is refused, whatever the tables are (see
  [A module schema without its ledger](#a-module-schema-without-its-ledger)).
  The role needs `USAGE` and `CREATE` on it.
- Everything else is refused before any write. The message gives the ledger
  recorded there and, for each table, the owner whose ledger records it, or
  that it matches no recorded ledger. Owners that use none of the refused
  table names are not affected.

| Layout of the base schema | Runtime storage | Modules |
| --- | --- | --- |
| Empty, or only other applications' tables with other names | starts | create `<base>__<id>` |
| Runtime storage history only | starts | create `<base>__<id>` |
| Runtime storage history, and the index the session store left on its `sessions` table when it was built on the runtime URL before #446 | starts | create `<base>__<id>` |
| One module's history only (dedicated database) | refused when it uses one of that module's table names | that module stays in place; the others create `<base>__<id>` |
| A module's history, then runtime storage, which committed v1..v3 and failed at v4 (memory first) | refused | memory refused (both ledgers record `migrations`); the others create `<base>__<id>` |
| A module's history and the empty `migrations` table of a runtime start that failed before v1 | refused | memory refused; the module stays in place |
| Runtime storage, then a module store on the same URL that adopted one of its tables (the A2A audit store and `idempotency_keys`) | starts | that module refused; the others, registry among them (`agents` is A2A's), create `<base>__<id>` |
| Several modules behind one `alembic_version` (`storage migrate` for several modules on one schema) | starts unless one of its tables changed | modules whose history is not proven refused |
| A table with an OpenMinion name that matches no listed history, or that misses or adds a column | refused when it is one of its tables | modules that use that name refused; the others create `<base>__<id>` |
| Runtime storage history with one of its tables missing | refused | create `<base>__<id>` |
| A module's proven history in `<base>__<id>`, and base tables with names it shares with another owner, but no ledger of it | as above | that module keeps `<base>__<id>` |
| A module's proven history in `<base>__<id>`, and base tables with names only it uses, without its ledger | as above | refused: two locations |
| `<base>__<id>` with tables but no ledger of the module: some of its store's tables, a storage engine table, or a first start stopped before `om_meta` | as above | that module refused |
| A module's proven history in `<base>__<id>` next to a storage engine table with other columns | as above | that module refused |
| The module's ledger in the base schema, and `<base>__<id>` exists | as above | refused: two locations |
| `search_path` names no existing schema | refused | refused |
| A base schema name with other characters than letters, digits and `_`, or a `<base>__<id>` longer than 63 bytes | as above | a module that needs `<base>__<id>` refused |
| No `CREATE` on the database for the role, and no `<base>__<id>` | as above | refused; the message gives the `CREATE SCHEMA ... AUTHORIZATION` statement |

### Listed histories

`postgres_histories.json` lists, per owner and ledger, the exact tables.
The entries measured as `current` are what this code builds: runtime storage
one version at a time, each module's Alembic chain one revision at a time
and then downgraded to base (not listed for memory, session and skill, whose
downgrade fails on PostgreSQL), and each module store alone, after its
module's Alembic head and before it.
The other entries were measured from every published package from 0.0.1 to
0.1.15, installed from PyPI; a layout that a published package leaves is
listed (`upgraded_from`) only when an installed upgrade from that package to
this code kept its rows and ended in one of this code's histories. Any other
layout, including the few that early 0.0.x packages left and this code
cannot upgrade, is refused as unknown. `engine_tables` lists the storage
engine's tables with the exact columns this code creates next to a module
store's tables in `<base>__<module id>`.

A new runtime migration, Alembic revision or store table changes what this
code builds. `tests/storage/test_postgres_schema.py` checks without a server
that the file lists this code's runtime versions and every revision of each
Alembic chain. `pytest -m postgres
tests/integration/test_postgres_module_schemas.py -k listed_histories`, with
`OPENMINION_TEST_POSTGRES_URL` set, builds every layout of this code; when
one is not listed, it fails with the lines to add to the file, measured as
`current`. The published packages' entries do not change.

### Connections, poolers and service files

A module connection is the configured URL with `-c
search_path="<base>__<module id>"` appended to its `options` startup
parameter. Before a module store writes, a connection with that URL must
start with that `search_path`; otherwise the store is refused, and nothing
is written. This is an upgrade impact for module stores behind a
transaction pooler. PgBouncer 1.22 in transaction pooling, for example,
rejects such a connection by default (`unsupported startup parameter in
options: search_path`); with `ignore_startup_parameters = options` or
`track_extra_parameters = search_path` it accepts it, but the server
connection keeps its default `search_path`, and the store is refused.
Point the module stores at the server directly, or through a pooler that
applies `options` to the server connection. Runtime storage uses the
configured URL unchanged, and `openminion storage migrate` needs no
`options`, because a migration sets `search_path` inside its own
transaction; it migrated through PgBouncer in each of those configurations.

Settings from a libpq service file (`service=` or `PGSERVICE`) apply only
to the parameters the URL does not give. A module URL always gives
`options`, so an `options` value from a service file does not reach module
connections; put it in the URL or in `PGOPTIONS`.

### Upgrading

1. Stop every process that writes the database, as in
   [Coordinated upgrade](#coordinated-upgrade), including module stores and
   `openminion storage migrate`.
2. Take a full `pg_dump` of the database, and check that it restores into a
   scratch database.
3. Upgrade every process to the same package. Run `openminion storage
   migrate --backend postgres` before the module stores start. A module
   store refuses a module history whose Alembic revision is behind this
   package's: its own `CREATE TABLE IF NOT EXISTS` and column upgrades would
   bring the tables to the head under the older `alembic_version`, a layout
   that `storage migrate` can then neither upgrade (Alembic would add the
   same columns again) nor prove. Then start the processes.
4. A refusal writes nothing. To go back, install the previous package again;
   the database is as it was.

### Relocating a refused layout

OpenMinion never moves, repairs or restamps tables itself. The steps below
are for the operator, with every OpenMinion process stopped and a verified
full `pg_dump`. Each block runs in one transaction. Indexes, constraints,
triggers and owned sequences move with their tables. The examples use the
base schema `public`.

**R1. A module's dedicated history, when runtime storage must use the base
schema.** Move every table of the module's listed history, with
`alembic_version` and `om_meta`, unchanged, to `<base>__<module id>`.
For a memory history at `0004_namespace` written by the memory store:

<!-- 446:R1 -->
```sql
BEGIN;
CREATE SCHEMA "public__memory";
ALTER TABLE public.memory_records SET SCHEMA "public__memory";
ALTER TABLE public.memory_candidates SET SCHEMA "public__memory";
ALTER TABLE public.memory_entities SET SCHEMA "public__memory";
ALTER TABLE public.memory_tier_transitions SET SCHEMA "public__memory";
ALTER TABLE public.memory_relations SET SCHEMA "public__memory";
ALTER TABLE public.memory_capture_bundle_receipts SET SCHEMA "public__memory";
ALTER TABLE public.migrations SET SCHEMA "public__memory";
ALTER TABLE public.alembic_version SET SCHEMA "public__memory";
ALTER TABLE public.om_meta SET SCHEMA "public__memory";
ALTER FUNCTION public.memory_records_search_trigger() SET SCHEMA "public__memory";
COMMIT;
```

The memory store then opens `public__memory` at its recorded revision, and
runtime storage starts in `public`. Before runtime storage has started in
`public`, this inverse puts the history back as it was:

<!-- 446:R1-inverse -->
```sql
BEGIN;
ALTER TABLE "public__memory".memory_records SET SCHEMA public;
ALTER TABLE "public__memory".memory_candidates SET SCHEMA public;
ALTER TABLE "public__memory".memory_entities SET SCHEMA public;
ALTER TABLE "public__memory".memory_tier_transitions SET SCHEMA public;
ALTER TABLE "public__memory".memory_relations SET SCHEMA public;
ALTER TABLE "public__memory".memory_capture_bundle_receipts SET SCHEMA public;
ALTER TABLE "public__memory".migrations SET SCHEMA public;
ALTER TABLE "public__memory".alembic_version SET SCHEMA public;
ALTER TABLE "public__memory".om_meta SET SCHEMA public;
ALTER FUNCTION "public__memory".memory_records_search_trigger() SET SCHEMA public;
DROP SCHEMA "public__memory";
COMMIT;
```

**R1b. Memory first, then runtime storage, which committed v1..v3.** The
same as R1, except that `migrations` stays in `public`: it now holds runtime
storage's rows. Runtime storage then resumes at v4.

<!-- 446:R1b -->
```sql
BEGIN;
CREATE SCHEMA "public__memory";
ALTER TABLE public.memory_records SET SCHEMA "public__memory";
ALTER TABLE public.memory_candidates SET SCHEMA "public__memory";
ALTER TABLE public.memory_entities SET SCHEMA "public__memory";
ALTER TABLE public.memory_tier_transitions SET SCHEMA "public__memory";
ALTER TABLE public.memory_relations SET SCHEMA "public__memory";
ALTER TABLE public.memory_capture_bundle_receipts SET SCHEMA "public__memory";
ALTER TABLE public.alembic_version SET SCHEMA "public__memory";
ALTER TABLE public.om_meta SET SCHEMA "public__memory";
ALTER FUNCTION public.memory_records_search_trigger() SET SCHEMA "public__memory";
CREATE TABLE "public__memory".migrations (LIKE public.migrations INCLUDING ALL);
COMMIT;
```

**R2. A module whose ledger claims another owner's table.** A ledger that
claimed another owner's table does not prove which revision the module's own
tables are at, so they are not moved into `<base>__<module id>`. Park the
module's own tables (the ones with its columns) with `alembic_version` and
`om_meta` in a holding schema that is not named `<base>__<module id>`; the
other owner's tables stay. For runtime storage, then the A2A audit store:

<!-- 446:R2-park -->
```sql
BEGIN;
CREATE SCHEMA a2a_hold;
ALTER TABLE public.audit_records SET SCHEMA a2a_hold;
ALTER TABLE public.a2a_audit_archive SET SCHEMA a2a_hold;
ALTER TABLE public.agents SET SCHEMA a2a_hold;
ALTER TABLE public.jobs SET SCHEMA a2a_hold;
ALTER TABLE public.alembic_version SET SCHEMA a2a_hold;
ALTER TABLE public.om_meta SET SCHEMA a2a_hold;
COMMIT;
```

Then run `openminion storage migrate --backend postgres --postgres-url <url>
--module a2a`, which creates `public__a2a` at its head revision, and copy the
rows with explicit column lists, setting the identity sequences past the
copied ids:

<!-- 446:R2-copy -->
```sql
BEGIN;
INSERT INTO "public__a2a".audit_records (id, record_date, ts, msg_id, trace_id, from_agent, to_agent, to_capability, type, method, status, task_id, error_code, error_message, envelope_json, data_json)
SELECT id, record_date, ts, msg_id, trace_id, from_agent, to_agent, to_capability, type, method, status, task_id, error_code, error_message, envelope_json, data_json FROM a2a_hold.audit_records;
INSERT INTO "public__a2a".a2a_audit_archive (id, record_date, source_file, ts, msg_id, trace_id, from_agent, to_agent, to_capability, type, method, status, task_id, error_code, error_message, envelope_json, data_json)
SELECT id, record_date, source_file, ts, msg_id, trace_id, from_agent, to_agent, to_capability, type, method, status, task_id, error_code, error_message, envelope_json, data_json FROM a2a_hold.a2a_audit_archive;
INSERT INTO "public__a2a".agents (agent_id, endpoint, capabilities_json, tags_json, status, updated_at)
SELECT agent_id, endpoint, capabilities_json, tags_json, status, updated_at FROM a2a_hold.agents;
INSERT INTO "public__a2a".jobs (task_id, trace_id, owner_agent_id, agent_id, method, idempotency_scope, idempotency_key, state, progress, current_step, result_inline_json, result_ref, error_json, created_at, updated_at, heartbeat_at)
SELECT task_id, trace_id, owner_agent_id, agent_id, method, idempotency_scope, idempotency_key, state, progress, current_step, result_inline_json, result_ref, error_json, created_at, updated_at, heartbeat_at FROM a2a_hold.jobs;
SELECT setval(pg_get_serial_sequence('"public__a2a".audit_records', 'id'), max(id)) FROM "public__a2a".audit_records HAVING max(id) IS NOT NULL;
SELECT setval(pg_get_serial_sequence('"public__a2a".a2a_audit_archive', 'id'), max(id)) FROM "public__a2a".a2a_audit_archive HAVING max(id) IS NOT NULL;
COMMIT;
```

Compare the row counts of each pair of tables, and keep `a2a_hold` until
the copy is verified. These statements are for the A2A tables at
`0005_job_idempotency_scope`; for an earlier revision the parked tables can
lack columns that the copy lists, and the copy stops. Then restore the dump
instead. With several modules behind one ledger, park them together and copy
each one.

**Going back after a relocation** means restoring the pre-upgrade dump with
the previous package. The only tested inverse is R1's above, and only before
runtime storage has started in the base schema. A layout where a module's
migration changed runtime storage's own tables (for example columns added to
its `memory_records` by `storage migrate --backend postgres` on the runtime
URL) has no relocation here: restore a dump from before that command.

### An empty `migrations` table

A runtime storage start that fails before v1 leaves an empty `migrations`
table; memory's baseline creates one too. An empty one proves neither, so
runtime storage and memory refuse it, and OpenMinion never adopts or drops
it. Before you remove it, find where it came from (the logs of the failed
start, or a memory history next to it) and take a full `pg_dump`.

### A module schema without its ledger

A `<base>__<module id>` that holds tables but no ledger of the module is
refused, whatever the tables are, also when they have exactly the columns
this code's store creates: some of a store's tables, a copy, and what a
store's first start leaves when it stops after its tables and before it
writes `om_meta` look the same. A second process that opens the same module
store while the first start is still creating its tables sees that layout
too and is refused; a later start succeeds once the first one has written
`om_meta`. OpenMinion never completes, adopts or drops such a schema.
Before you remove it, find where it came from (the logs of the first start)
and take a full `pg_dump`; a module store creates the schema again when it
does not exist.

### Permissions

Creating `<base>__<module id>` needs `CREATE` on the database. A database
administrator can instead create the empty schema for the role, which then
needs nothing more:

```sql
CREATE SCHEMA "public__memory" AUTHORIZATION openminion_app;
```

An existing schema needs `USAGE` and `CREATE` for the role:
`GRANT USAGE, CREATE ON SCHEMA "public__memory" TO openminion_app;`.

## SQLite runtime home backup

`openminion storage backup --sqlite <file> --output <backup>` snapshots one
SQLite file with SQLite's online backup API. It checks the file's
`application_id`: it accepts a file without one (`0`) or with the storage
module's (`0x4F4D0000`), and refuses any other id, which other modules set
on their own databases, with exit code 1 and `application_id mismatch for
module 'storage': expected 1330446336, found <id>`. It also keeps a copy of
the snapshot in a `<file>.bak/` directory next to the database. No single
command backs up the whole runtime home.

After its first turn, a runtime home with the default layout has these SQLite
files under its data root (by default `.openminion` under the home root).
Other configurations and features can add files.

| File | Owner (`application_id`) | `openminion storage backup` |
| --- | --- | --- |
| `state/openminion.db` (runtime database) | none | accepted |
| `state/brain/sessions.db` (brain working state) | none | accepted |
| `memory/memory.audit.db` | none | accepted |
| `ops/evidence.db`, `ops/jobs.db`, `ops/plans.db` | none | accepted |
| `task/task.db` | none | accepted |
| `artifact/index.db` | artifact | refused |
| `authored_tools/store.sqlite` | authoring | refused |
| `identity/identity.db` | identity | refused |
| `memory/memory.db`, `state/brain/memory.db` | memory | refused |
| `policy/policy.db` | policy | refused |
| `retrieve/retrieve.db` | retrieve | refused |
| `skill/skills.db` | skill | refused |
| `state/brain/brain.db` | brain | refused |
| `telemetry/telemetry.db` | telemetry | refused |

To back up the runtime home, stop every process that writes it, as in
[Coordinated upgrade](#coordinated-upgrade), then:

1. Back up each accepted file with
   `openminion storage backup --sqlite <file> --output <backup>`.
2. Back up every other SQLite file with SQLite's backup API, from the
   `sqlite3` shell:

   ```bash
   sqlite3 <file> ".backup '<backup>'"
   ```

   or from the Python environment that runs OpenMinion:

   ```bash
   python -c "import sqlite3, sys; src = sqlite3.connect(sys.argv[1]); dst = sqlite3.connect(sys.argv[2]); src.backup(dst); dst.close(); src.close()" <file> <backup>
   ```

3. Copy the other files under the data root as they are, except the
   `<file>-journal`, `<file>-wal` and `<file>-shm` companions of the SQLite
   files. Steps 1 and 2 open each database through SQLite, which rolls back a
   hot `-journal` and reads a `-wal`, so the backups already hold the
   committed content. A companion copied now and put back next to a restored
   file would be applied to it.
4. Record a checksum of each backup, for example with `sha256sum`.

To roll back:

1. Stop every process that uses the runtime home, readers included.
2. Check every backup before you replace anything: it matches the checksum
   you recorded, and `PRAGMA integrity_check` on it, for example
   `sqlite3 <backup> "PRAGMA integrity_check"`, prints `ok`.
3. Move each SQLite file of the runtime home, together with its
   `<file>-journal`, `<file>-wal` and `<file>-shm` if they exist, to a
   directory outside the data root, keeping their paths relative to the data
   root. Keep each set together and do not delete any of it: a writer that
   did not finish can leave a `-journal` or a `-wal` that is the only copy of
   that database's last committed content, and SQLite applies it to whatever
   file has the database's name. Left next to a restored file, a hot
   `-journal` would be rolled back into the backup and a `-wal` would add
   newer commits to it.
4. Put each backup in place of its file: restore an accepted file with
   `openminion storage restore --sqlite <file> --snapshot <backup> --yes`,
   and copy the backup to the file's path for the others. Then copy the other
   files back, without any `-journal`, `-wal` or `-shm`.
5. Check that no `<file>-journal`, `<file>-wal` or `<file>-shm` is next to a
   restored file before you start any process.

## Brain working state fields

The brain saves each session's working state as JSON: in the `working_state`
table of the brain's session store (`brain/sessions.db` next to the SQLite
runtime database, `state/brain/sessions.db` in the default layout), or in
`working_state.json` with the local session store. The working state rejects
fields it does not define.

Packages with `turn_evidence` and `turn_evidence_trace_id` add both fields,
as `null` when no same-turn continuation ran, when the brain saves its typed
working state (`save_state`). A writer that updates the stored JSON in place
keeps its keys. Before each runtime turn reaches the brain loop, the
turn-start reset rewrites the stored state that way, and so does the
injection of a gateway system context or of task, cron, project, watch,
consolidation or delegation hints when the turn carries them. So on a session
last saved before the fields, the first runtime turn after the upgrade can
write the reset's row without them, and the brain's next typed save in that
turn adds them. Once a typed save has added them, the rewrites keep them. A
row without the fields therefore does not mean that an older process wrote
it.

Packages with the fields read a working state without them, including those
rows, with both unset. A package from before the fields rejects any working
state saved with them: validation fails with `extra_forbidden` for both.

Upgrade the processes that share the runtime state as in
[Coordinated upgrade](#coordinated-upgrade): stop them, keep a pre-upgrade
backup (the databases of the runtime home as in
[SQLite runtime home backup](#sqlite-runtime-home-backup), and with the local
session store a copy of its `sessions` directory), upgrade them together, and
never run an older process against working state saved by the newer package.
Rolling back means restoring the matching pre-upgrade backup with the
matching older package.

## Memory consolidation jobs from earlier packages

From 0.1.15, the daemon runs a scheduled job as memory consolidation only when
its task record has the kind `memory_consolidation`, which
`task.consolidate_memory` stores when it creates the job. Packages before
0.1.15 did not store it, and the upgrade does not add it. So every
consolidation job created by `task.consolidate_memory` of a package before
0.1.15 stops, and so does any job whose payload has the reserved
`_openminion_memory_consolidation` key without such a task record. Jobs
created by `task.consolidate_memory` of 0.1.15 or later have the kind and keep
running after later upgrades: do not cancel or recreate them.

The first time the daemon runs such a job, the run fails with `memory
consolidation job is missing trusted task provenance; it was paused and must
be recreated with task.consolidate_memory`. It runs no turn and no capture
recovery, and the task is paused with its schedule disabled (a job without a
task record is only disabled). Resuming it does not help: its next run fails
and pauses it again.

To inspect the jobs:

```bash
openminion tasks list --json
openminion tasks show TASK_ID --json
openminion tools run task.list --json '{"limit": 50}'
```

A consolidation task has the instruction `Review the provided memory
candidates, decide which to promote, discard, or defer, ...`, and `task.list`
shows its `consolidation` settings. The task kind is not shown, so when you
upgrade from a package before 0.1.15, treat every consolidation task that
exists at that upgrade as a pre-upgrade job, whether it has run yet or not.
When you upgrade from 0.1.15 or later, do not treat the existing
consolidation tasks as pre-upgrade jobs: a task is one only when its run fails
with the message above (a job created before 0.1.15 and never replaced), and
only that task needs a replacement. A pre-upgrade job that has run is
`paused` with `enabled: false`, and its `last_run` failed with the message
above.

The `tasks` commands act on the default agent's tasks; add `--agent-id AGENT`
for another agent's. `openminion tools run` acts as the agent `openminion`,
whatever the default agent is. For another agent, set `OPENMINION_AGENT_ID`
and run the tool in this process:

```bash
OPENMINION_AGENT_ID=AGENT openminion tools run task.list \
  --runtime-source inproc --json '{"limit": 50}'
```

Replace each pre-upgrade job, and only those:

1. Note its `agent_id`, its interval (`schedule.every_ms` in `tasks show`,
   3600000 per hour) and its `consolidation.batch_limit` (`task.list`).
2. Stop it before you create the replacement:
   `openminion tasks cancel TASK_ID --json`, which also works on a job that
   its failed run paused. A cancelled job cannot be resumed next to its
   replacement. `openminion tasks pause TASK_ID --json` also stops a job that
   has not run yet, but leaves it resumable.
3. Create the replacement through `task.consolidate_memory`, with the same
   settings:

   ```bash
   openminion tools run task.consolidate_memory \
     --json '{"interval_hours": 12, "batch_limit": 8}'
   ```

   For another agent's job, add `OPENMINION_AGENT_ID=AGENT` and
   `--runtime-source inproc` as above.
4. Check with `openminion tasks list --json` that the old job is not enabled
   (`cancelled`, or `paused` with `enabled: false`), and that the replacement
   has `enabled: true` and the old job's `agent_id`. The enabled consolidation
   tasks are then the replacements plus any job that `task.consolidate_memory`
   of 0.1.15 or later created, which stays as it is. After an upgrade from a
   package before 0.1.15, every consolidation task present was a pre-upgrade
   job, so the replacements are the only enabled consolidation tasks.

Creating the replacement while the old job is still enabled is not refused;
both stay enabled until the old job's next run pauses it.

New jobs with the reserved payload cannot be created through the raw cron
API: `POST /v1/cron/jobs` answers `400` (`invalid_request`) with `memory
consolidation jobs must be created through task.consolidate_memory` and adds
no job.

Pending capture intents are kept: the failed run, `pause` and `cancel` leave
them as they are. Their recovery is separate from this procedure and works as
before: each consolidation run of the replacement recovers them first, on a
memory store that can commit a capture bundle; on one that cannot, they stay
pending.

## Runtime migration 15: session metadata string rows

Migration 15 adds `session_metadata_strings`, one row for each top-level member
of a session's stored metadata whose value is a string. The session store
writes these rows in the same transaction as every session insert, metadata
update and delete. The migration backfills existing sessions in batches of
1,000 inside its transaction. It reads each stored `metadata_json` with
`json.loads`, so documents PostgreSQL cannot parse (NaN, Infinity, an escaped
NUL or a lone surrogate) get their rows too, and it does not change any stored
metadata. A session whose `metadata_json` is not JSON stops the migration,
which rolls back and names that session.

As measured on 50,000 sessions with PostgreSQL 16, the backfill wrote 160,052
rows in 4.8 s, the table and its indexes took 33 MB next to 21 MB for
`sessions`, and a metadata write took 1.27 ms instead of 0.64 ms. The cost
grows with the number of string members per session.

A process from before migration 15 does not maintain the rows, which affects
PostgreSQL metadata filters (`list_sessions(metadata_filter=...)`, used by ACP
`session/list` and Focus directory sessions):

- **Missing match.** A session it creates has no rows, so no filter returns
  it.
- **Stale match.** A session whose metadata it changes keeps the rows of its
  earlier metadata, so a filter can return it for a value it no longer has,
  and miss it for its new value.

SQLite metadata filters do not read these rows.

## Metadata filter behavior by backend

PostgreSQL selects a session whose stored metadata, as
`SessionRecord.metadata` reads it after the JSON round trip, has a top-level
member named exactly as the filter key whose value is a string equal to the
filter value. Keys keep the existing `strip()`, and empty keys are skipped.
Every other character of a key or value is literal, including `.`, `[`, `"`,
non-ASCII text, NUL and lone surrogates. Numbers, booleans, null, objects,
arrays and missing members never match. The query returns at most `limit`
sessions in the usual `updated_at, id` order.

Before either backend reads a filter value, `list_sessions` keeps its existing
normalization: a false value (`None`, `0`, `False`, an empty container) is
sent as `""`, so `{"surface": None}` matches a session whose `surface` is the
empty string, on both backends. PostgreSQL then raises `TypeError` for any
other filter value that is not a string.

The rows store each name and value as its JSON string text (`json.dumps`,
ASCII without a NUL byte). PostgreSQL compares that text with `COLLATE "C"`,
which is a byte comparison, so the database's default collation does not
affect which sessions match. The indexed `entry_digest` only finds the
candidate rows; two different entries with one digest still compare their
full name and value.

SQLite keeps its existing `json_extract(metadata_json, '$.' || key) = value`
reader. It differs from the PostgreSQL behavior where it reads more than a
literal top-level string:

- it reads a key as a JSON path (`a.b`, `a[0]`, a leading `"`);
- it matches an object or array by its minified JSON text;
- it matches keys that `json.dumps` escapes (a quote, a backslash, control or
  non-ASCII characters), and values with a NUL, on SQLite 3.45.1, 3.46.1 and
  3.47.1 but not on 3.40.1 or 3.34.1;
- it raises `malformed JSON` on NaN and Infinity documents on SQLite 3.40.1
  and 3.34.1;
- for a member name stored twice (which the writer produces only for two
  distinct float NaN keys), it reads the first value, where PostgreSQL and
  `json.loads` read the last;
- it compares a filter value that is not a string (other than a false value,
  which becomes `""`) as an SQL value, so `1` matches a stored `1` or `true`,
  where PostgreSQL raises `TypeError`.

## PostgreSQL runtime storage

With `storage.backend: postgres` and `storage.postgres_url` (or
`OPENMINION_STORAGE_BACKEND=postgres` and `OPENMINION_STORAGE_POSTGRES_URL`),
`APIRuntime` keeps its runtime record store (sessions, messages, events and
room participants) in PostgreSQL. This section lists only the paths that
`tests/integration/test_runtime_storage_postgres.py` checks against a
PostgreSQL server; it is not a backend support matrix.

Checked on PostgreSQL:

- `APIRuntime.from_config_path` start and `close()`: no server connection is
  left after `close()`, and no runtime SQLite database is created at
  `storage.path`;
- a session and a message written through `runtime.sessions` are in
  PostgreSQL, and one echo-agent turn through `Agent(runtime=...)` completes
  and stores its messages there;
- `openminion room create`: the room and its owner participant row are in
  PostgreSQL;
- `OpenMinionRuntime.from_config_path` start and `close()`.

SQLite only:

- `APIRuntime.storage_connection` is the runtime record store's
  `sqlite3.Connection` on SQLite, and `None` for any other backend;
- the compatibility wrapper's `OpenMinionRuntime.storage` is the same value.

The paths above reach the runtime database through `runtime_storage` and
`sessions`, not through `storage_connection`.

Not covered:

- other runtime surfaces on PostgreSQL runtime storage;
- `openminion storage migrate --backend postgres` and module PostgreSQL
  migrations, except where each owner's tables go: see
  [PostgreSQL schemas per owner](#postgresql-schemas-per-owner);
- the module stores under the data root (for example `brain/sessions.db` and
  `memory/memory.db`): they are SQLite files on both backends, because
  `storage.backend` does not select them.
