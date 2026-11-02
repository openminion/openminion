"""PostgreSQL schema per owner (#446), against a real server.

Runtime storage keeps the configured URL's schema (here `public` of a new
database per test); each module uses `public__<module id>`; a module whose
listed history is already in `public` stays there. Every refusal is checked
to write nothing: a snapshot of every schema's tables (columns, nullability,
row counts and an md5 of all rows), indexes, functions, triggers and sequence
values is the same before and after. Views, constraints other than those
indexes, column defaults and privileges are not part of the snapshot.

Legacy layouts are built the way earlier code wrote them: a module's Alembic
chain upgraded on a connection to `public`, the memory store on an engine for
`public`, and runtime storage's own `migrate_record_store` without the start's
check. Only public entry points are imported at module level, so the same file
runs on code from before #446.
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from openminion.cli.commands.storage import run_storage_migrate
from openminion.modules.storage.migrations.module_ids import get_module_application_id
from openminion.modules.storage.migrations.runner import MigrationRunner
from tests.storage.postgres_test_utils import schema_url

pytestmark = pytest.mark.postgres
sa = pytest.importorskip("sqlalchemy")

DOCS = Path(__file__).resolve().parents[2] / "docs" / "runtime-database-upgrades.md"
# Audit records older than the A2A store's retention are deleted when it writes.
NOW = datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ---------------------------------------------------------------------------- fixtures


def _server_url() -> str:
    url = str(os.getenv("OPENMINION_TEST_POSTGRES_URL", "")).strip()
    if not url:
        pytest.skip("OPENMINION_TEST_POSTGRES_URL is not set")
    return url


def _admin():
    return sa.create_engine(_server_url(), isolation_level="AUTOCOMMIT")


@pytest.fixture
def database() -> Iterator[str]:
    """A new database: `public` is the base schema, module schemas live next to it."""
    name = f"om446_{uuid.uuid4().hex[:12]}"
    admin = _admin()
    with admin.connect() as conn:
        conn.execute(sa.text(f'CREATE DATABASE "{name}"'))
    try:
        yield (
            sa.engine.make_url(_server_url())
            .set(database=name)
            .render_as_string(hide_password=False)
        )
    finally:
        with admin.connect() as conn:
            conn.execute(
                sa.text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :d AND pid <> pg_backend_pid()"
                ),
                {"d": name},
            )
            conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()


def _execute(url: str, *statements: str) -> None:
    engine = sa.create_engine(url)
    try:
        with engine.begin() as conn:
            for statement in statements:
                conn.execute(sa.text(statement))
    finally:
        engine.dispose()


def _scalars(url: str, sql: str) -> list:
    engine = sa.create_engine(url)
    try:
        with engine.connect() as conn:
            return list(conn.execute(sa.text(sql)).scalars())
    finally:
        engine.dispose()


def _snapshot(url: str) -> dict:
    """Every non-system schema: tables, columns, keys, rows, indexes, routines, sequences."""
    engine = sa.create_engine(url)
    snap: dict = {}
    try:
        with engine.connect() as conn:
            schemas = conn.execute(
                sa.text(
                    "SELECT nspname FROM pg_namespace WHERE nspname !~ '^pg_' "
                    "AND nspname <> 'information_schema' ORDER BY 1"
                )
            ).scalars()
            for schema in list(schemas):
                q = f'"{schema}"'
                tables: dict = {}
                names = conn.execute(
                    sa.text(
                        "SELECT tablename FROM pg_tables WHERE schemaname = :s ORDER BY 1"
                    ),
                    {"s": schema},
                ).scalars()
                for table in list(names):
                    columns = conn.execute(
                        sa.text(
                            "SELECT column_name, data_type, is_nullable FROM "
                            "information_schema.columns WHERE table_schema = :s AND "
                            "table_name = :t ORDER BY ordinal_position"
                        ),
                        {"s": schema, "t": table},
                    ).all()
                    count, md5 = conn.execute(
                        sa.text(
                            "SELECT count(*), md5(coalesce(string_agg(x::text, '|' "
                            f"ORDER BY x::text), '')) FROM {q}.\"{table}\" x"
                        )
                    ).one()
                    tables[table] = ([tuple(c) for c in columns], count, md5)
                snap[schema] = {
                    "tables": tables,
                    "indexes": conn.execute(
                        sa.text(
                            "SELECT indexname, indexdef FROM pg_indexes "
                            "WHERE schemaname = :s ORDER BY 1"
                        ),
                        {"s": schema},
                    ).all(),
                    "routines": conn.execute(
                        sa.text(
                            "SELECT p.proname FROM pg_proc p JOIN pg_namespace n "
                            "ON n.oid = p.pronamespace WHERE n.nspname = :s ORDER BY 1"
                        ),
                        {"s": schema},
                    ).all(),
                    "triggers": conn.execute(
                        sa.text(
                            "SELECT t.tgname, c.relname FROM pg_trigger t JOIN pg_class c "
                            "ON c.oid = t.tgrelid JOIN pg_namespace n ON n.oid = "
                            "c.relnamespace WHERE n.nspname = :s AND NOT t.tgisinternal "
                            "ORDER BY 1"
                        ),
                        {"s": schema},
                    ).all(),
                    "sequences": conn.execute(
                        sa.text(
                            "SELECT sequencename, last_value FROM pg_sequences "
                            "WHERE schemaname = :s ORDER BY 1"
                        ),
                        {"s": schema},
                    ).all(),
                }
    finally:
        engine.dispose()
    return snap


def _schemas(url: str) -> list[str]:
    return _scalars(
        url,
        "SELECT nspname FROM pg_namespace WHERE nspname !~ '^pg_' "
        "AND nspname <> 'information_schema' ORDER BY 1",
    )


def _tables(url: str, schema: str) -> set[str]:
    return set(
        _scalars(url, f"SELECT tablename FROM pg_tables WHERE schemaname = '{schema}'")
    )


def _ledger(url: str, schema: str) -> tuple[list[str], str | None]:
    q = f'"{schema}"'
    revisions = _scalars(url, f"SELECT version_num FROM {q}.alembic_version")
    module_id = _scalars(url, f"SELECT value FROM {q}.om_meta WHERE key = 'module_id'")
    return revisions, (module_id[0] if module_id else None)


# ---------------------------------------------------------------------------- owners


def _start_runtime(url: str, root: Path) -> int:
    from openminion.modules.storage.runtime.context import build_runtime_storage

    ctx = build_runtime_storage(
        root / "state.db",
        env={},
        record_backend="record.postgres",
        record_backend_options={"url": url},
    )
    try:
        return ctx.migration_result.current_version
    finally:
        ctx.close()


def _runtime_rows(url: str) -> None:
    _execute(
        url,
        "INSERT INTO public.sessions(id, session_key, channel, target) "
        "VALUES ('s-1', 'k-1', 'cli', 'probe')",
        "INSERT INTO public.idempotency_keys(method, idempotency_key, request_hash, "
        "response_json, status) VALUES ('m', 'k', 'h', '{}', 'done')",
    )


def _legacy_runtime(url: str) -> str:
    """Runtime storage's migrations as earlier code ran them: no schema check."""
    from openminion.modules.storage.backends.postgres import RecordStorePostgres
    from openminion.modules.storage.runtime.migrations import migrate_record_store

    store = RecordStorePostgres(url)
    try:
        migrate_record_store(store, backend_type="postgres")
    except Exception as exc:  # noqa: BLE001
        return str(exc)
    finally:
        store.close()
    return "ok"


def _legacy_module(
    url: str, module_id: str, target: str = "head", *, downgrade: bool = False
) -> None:
    """A module's Alembic chain in `public`, as earlier code ran every module there."""
    from alembic import command
    from alembic.config import Config

    from openminion.modules.storage.migrations.alembic import (
        discover_module_alembic_paths,
    )

    ini, location = discover_module_alembic_paths(module_id)
    cfg = Config(str(ini)) if ini else Config()
    cfg.set_main_option("script_location", str(location))
    engine = sa.create_engine(url)
    try:
        with engine.begin() as conn:
            cfg.attributes["connection"] = conn
            (command.downgrade if downgrade else command.upgrade)(cfg, target)
    finally:
        engine.dispose()


def _memory_record(record_id: str):
    from openminion.modules.memory.models import MemoryRecord

    return MemoryRecord(
        id=record_id,
        scope="agent:a",
        type="fact",
        title=f"title {record_id}",
        content=f"content {record_id}",
        created_at=NOW,
        updated_at=NOW,
    )


def _legacy_memory(url: str, tmp_path: Path) -> None:
    """A dedicated memory database as earlier releases built it: all in `public`."""
    from openminion.modules.memory.storage.postgres.store import PostgresMemoryStore

    store = PostgresMemoryStore(
        sa.create_engine(url, future=True),
        database_path=tmp_path / "legacy-memory.db",
        owns_engine=True,
    )
    try:
        store.put(_memory_record("rec-1"))
    finally:
        store.close()


def _memory(url: str, tmp_path: Path):
    from openminion.modules.memory.storage.factory import resolve_memory_backend

    return resolve_memory_backend(
        config={"store": {"backend": "postgres", "postgres_url": url}},
        db_path=tmp_path / "memory.db",
    ).store


def _storage_config(url: str, root: Path):
    from openminion.modules.storage.engine import StorageEngineConfig

    root.mkdir(parents=True, exist_ok=True)
    return StorageEngineConfig(
        root_dir=root,
        sqlite_path=root / "state.db",
        fallback_root=root,
        record_backend="record.postgres",
        record_backend_options={"url": url},
    )


def _a2a_audit(url: str, root: Path):
    from openminion.modules.a2a.storage.factory import build_a2a_audit_store

    return build_a2a_audit_store(
        config=_storage_config(url, root), audit_root=root / "a2a-audit"
    )


def _audit_record(msg_id: str):
    from openminion.modules.a2a.models import AuditRecord

    return AuditRecord(
        ts=NOW,
        msg_id=msg_id,
        trace_id="t-1",
        from_agent="a",
        to_agent="b",
        to_capability=None,
        type="request",
        method="probe.method",
        status="ok",
    )


def _migrate(url: str, tmp_path: Path, capsys, module: str | None = None) -> dict:
    """`openminion storage migrate --backend postgres [--module M] --json`."""
    args = SimpleNamespace(
        backend="postgres",
        postgres_url=url,
        sqlite=str(tmp_path / "unused.db"),
        module=module,
        plan=False,
        verify=False,
        json=True,
    )
    code = 0
    try:
        run_storage_migrate(args)
    except SystemExit as exc:
        code = int(exc.code or 0)
    entries = json.loads(capsys.readouterr().out)["modules"]
    return {"code": code, **{entry["module_id"]: entry for entry in entries}}


# ---------------------------------------------------------------------------- new databases

WORKING_MODULES = (
    "secret",
    "session",
    "identity",
    "registry",
    "task",
    "skill",
    "policy",
    "compress",
    "retrieve",
    "artifact",
    "a2a",
    "memory",
    "controlplane",
    "telemetry",
)


def test_new_database_gives_every_module_its_own_schema(
    database: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _start_runtime(database, tmp_path / "rt1") == 15
    _runtime_rows(database)
    runtime_before = _snapshot(database)["public"]

    result = _migrate(database, tmp_path, capsys)

    for module_id in WORKING_MODULES:
        assert result[module_id]["status"] == "ok", result[module_id]
        revisions, owner = _ledger(database, f"public__{module_id}")
        assert len(revisions) == 1 and owner == module_id
    # Runtime storage's schema, tables and rows are untouched, with no module ledger.
    assert _snapshot(database)["public"] == runtime_before
    assert not {"alembic_version", "om_meta"} & _tables(database, "public")

    memory = _memory(database, tmp_path)
    try:
        memory.put(_memory_record("rec-1"))
        assert memory.get("rec-1") is not None
    finally:
        memory.close()
    audit = _a2a_audit(database, tmp_path / "a2a")
    try:
        audit.append_audit(_audit_record("msg-1"))
        assert [r.msg_id for r in audit.query_audit()] == ["msg-1"]
    finally:
        audit.close()
    assert _snapshot(database)["public"] == runtime_before
    assert _start_runtime(database, tmp_path / "rt2") == 15


def test_modules_first_then_runtime_storage(
    database: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for module_id in ("memory", "a2a", "session", "registry"):
        assert _migrate(database, tmp_path, capsys, module_id)["code"] == 0

    assert _start_runtime(database, tmp_path / "rt") == 15
    columns = _scalars(
        database,
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = 'sessions'",
    )
    assert {"session_key", "channel", "target"} <= set(columns)


def test_module_stores_on_the_runtime_url_use_their_own_schemas(
    database: str, tmp_path: Path
) -> None:
    """The session and A2A stores that shared runtime storage's schema now work."""
    from openminion.modules.session.runtime.factory import build_module_session_store

    _start_runtime(database, tmp_path / "rt")
    _runtime_rows(database)
    before = _snapshot(database)["public"]

    sessions = build_module_session_store(
        config=_storage_config(database, tmp_path / "s"),
        database_path=tmp_path / "s" / "sessions.db",
        env={},
    )
    try:
        session_id = sessions.create_session(
            initial_agent_id="a", profile_version="v1", title="t", tags=[]
        )
        assert sessions.get_session(session_id) is not None
    finally:
        sessions.close()
    audit = _a2a_audit(database, tmp_path / "a2a")
    audit.close()

    assert _snapshot(database)["public"] == before
    assert {"public__session", "public__a2a"} <= set(_schemas(database))


def test_runtime_storage_after_the_session_store_tried_its_url(
    database: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """On dev the session store, built on the runtime URL as api/queries/sessions.py
    builds it, fails and leaves one index on runtime storage's sessions table. That
    database upgrades: runtime storage proceeds and every module gets its own schema."""
    from openminion.modules.session.runtime.factory import build_module_session_store
    from openminion.modules.session.storage.store import PostgresSessionStore
    from openminion.modules.storage.engine import StorageEngine

    _start_runtime(database, tmp_path / "rt")
    _runtime_rows(database)
    # dev's factory: the storage engine for the configured URL, then the store on it.
    engine = StorageEngine.from_config(config=_storage_config(database, tmp_path / "d"))
    try:
        with pytest.raises(Exception, match="session_id"):
            PostgresSessionStore(
                tmp_path / "d" / "sessions.db",
                record_store=engine.record_store,
                hybrid_store=engine.hybrid_store,
                env={},
            )
    finally:
        engine.close()
    assert "idx_sessions_status_updated" in _scalars(
        database, "SELECT indexname FROM pg_indexes WHERE schemaname = 'public'"
    )

    assert _start_runtime(database, tmp_path / "rt2") == 15
    before = _snapshot(database)["public"]
    result = _migrate(database, tmp_path, capsys)
    assert {m: result[m]["status"] for m in WORKING_MODULES} == {
        m: "ok" for m in WORKING_MODULES
    }
    sessions = build_module_session_store(
        config=_storage_config(database, tmp_path / "s"),
        database_path=tmp_path / "s" / "sessions.db",
        env={},
    )
    try:
        assert sessions.get_session(
            sessions.create_session(
                initial_agent_id="a", profile_version="v1", title="t", tags=[]
            )
        )
    finally:
        sessions.close()
    assert _snapshot(database)["public"] == before


def test_a_module_url_that_does_not_land_in_its_schema_is_refused(
    database: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A server or proxy that drops the URL's options: refused before any write."""
    from openminion.modules.storage.migrations import postgres_schema

    _start_runtime(database, tmp_path / "rt")
    before = _snapshot(database)
    # As if the options parameter never reached the server.
    monkeypatch.setattr(postgres_schema, "module_url", lambda url, schema: url)
    registry = _factory(
        "openminion.modules.registry.storage", "build_registry_store", "r.db"
    )

    for build in (
        lambda: _memory(database, tmp_path),
        lambda: _a2a_audit(database, tmp_path / "a2a"),
        lambda: registry(database, tmp_path / "r"),
    ):
        with pytest.raises(Exception) as refused:
            build()
        assert "did not apply the URL's options parameter" in str(refused.value)
        assert "Nothing was written" in str(refused.value)
    assert _snapshot(database) == before


# ---------------------------------------------------------------------------- in place


def test_dedicated_memory_history_stays_in_place(
    database: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _legacy_memory(database, tmp_path)
    ledger = _ledger(database, "public")

    memory = _memory(database, tmp_path)
    try:
        assert memory.get("rec-1") is not None
        memory.put(_memory_record("rec-2"))
    finally:
        memory.close()
    assert _migrate(database, tmp_path, capsys, "memory")["memory"]["status"] == "ok"

    assert _ledger(database, "public") == ledger == (["0004_namespace"], "memory")
    assert _schemas(database) == ["public"]
    assert len(_scalars(database, "SELECT id FROM public.memory_records")) == 2


def test_listed_historical_session_history_stays_in_place_and_upgrades(
    database: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Session at 0004 as the published 0.0.11..0.1.7 wrote it (no cron_scheduler_control)."""
    from openminion.modules.storage.migrations import postgres_schema

    _legacy_module(database, "session", target="0004_task_plan_step_start")
    _execute(database, "DROP TABLE public.cron_scheduler_control")
    history = postgres_schema.complete_history("session", _read(database, "public"))
    assert history is not None and "0.1.7" in history.measured

    assert _migrate(database, tmp_path, capsys, "session")["session"]["status"] == "ok"

    assert _ledger(database, "public")[0] == ["0005_cron_scheduler_control"]
    assert _schemas(database) == ["public"]
    current = postgres_schema.complete_history("session", _read(database, "public"))
    assert current is not None and "current" in current.measured


# ---------------------------------------------------------------------------- refusals


def _memory_then_failed_runtime(url: str, tmp_path: Path) -> None:
    _legacy_memory(url, tmp_path)
    assert "v4" in _legacy_runtime(url)


def _a2a_then_failed_runtime(url: str, tmp_path: Path) -> None:
    _legacy_module(url, "a2a")
    assert "v1" in _legacy_runtime(url)


def _runtime_then_a2a(url: str, tmp_path: Path) -> None:
    _start_runtime(url, tmp_path / "rt")
    _runtime_rows(url)
    _legacy_module(url, "a2a")
    _execute(
        url,
        "INSERT INTO public.audit_records(record_date, ts, msg_id, trace_id, "
        f"from_agent, type, method, status) VALUES (current_date, '{NOW}', 'msg-0', "
        "'t', 'a', 'request', 'm', 'ok')",
    )


def _artifact_then_registry(url: str, tmp_path: Path) -> None:
    _legacy_module(url, "artifact")
    _legacy_module(url, "registry")


def _altered_runtime_table(url: str, tmp_path: Path) -> None:
    _start_runtime(url, tmp_path / "rt")
    _execute(url, "ALTER TABLE public.sessions ADD COLUMN added_by_hand TEXT")


def _missing_runtime_table(url: str, tmp_path: Path) -> None:
    _start_runtime(url, tmp_path / "rt")
    _execute(url, "DROP TABLE public.room_participants CASCADE")


def _foreign_tables(url: str, tmp_path: Path) -> None:
    _execute(
        url,
        "CREATE TABLE public.sessions (id integer PRIMARY KEY, owner text)",
        "CREATE TABLE public.customers (id integer PRIMARY KEY)",
    )


def _skill_behind_head(url: str, tmp_path: Path) -> None:
    _legacy_module(url, "skill", target="0003_audit")


def _two_locations(url: str, tmp_path: Path) -> None:
    _legacy_memory(url, tmp_path)
    _execute(url, 'CREATE SCHEMA "public__memory"')


def _foreign_migrations_table(url: str, tmp_path: Path) -> None:
    _execute(
        url,
        "CREATE TABLE public.migrations (version text PRIMARY KEY, name text)",
        "INSERT INTO public.migrations VALUES ('2024_01_init', 'another tool')",
    )


LAYOUTS = {
    # layout: (builder, refused owners, message fragments, owners that still proceed)
    "memory-then-failed-runtime": (
        _memory_then_failed_runtime,
        ["runtime", "memory", "memory-store"],
        [
            "migrations is recorded by runtime storage v3 and memory alembic "
            "0004_namespace"
        ],
        ["secret", "registry"],
    ),
    "a2a-then-failed-runtime": (
        _a2a_then_failed_runtime,
        ["runtime", "memory"],
        ["no runtime storage ledger", "migrations matches no recorded ledger"],
        ["artifact"],
    ),
    "runtime-then-a2a-audit-store": (
        _runtime_then_a2a,
        ["a2a", "a2a-audit-store"],
        ["idempotency_keys is recorded by runtime storage v15"],
        ["registry", "memory", "runtime"],
    ),
    "artifact-then-registry": (
        _artifact_then_registry,
        ["artifact", "registry"],
        ["agents is missing", "no artifact ledger"],
        ["secret", "runtime"],
    ),
    "altered-runtime-table": (
        _altered_runtime_table,
        ["runtime", "session"],
        [
            "sessions has other columns than runtime storage v15 lists",
            "sessions matches no recorded ledger",
        ],
        ["memory", "a2a"],
    ),
    "missing-runtime-table": (
        _missing_runtime_table,
        ["runtime"],
        ["room_participants is missing"],
        ["session", "memory", "a2a"],
    ),
    "foreign-tables": (
        _foreign_tables,
        ["runtime", "session"],
        ["sessions matches no recorded ledger (its columns match no listed history)"],
        ["memory"],
    ),
    "foreign-migrations-table": (
        _foreign_migrations_table,
        ["runtime", "memory"],
        ["migrations matches no recorded ledger (its columns match no listed history)"],
        ["secret"],
    ),
    "two-locations": (
        _two_locations,
        ["memory", "memory-store"],
        ["neither is selected"],
        ["secret"],
    ),
    # A module store would add the head's tables under an older alembic_version.
    "store-on-a-history-behind-head": (
        _skill_behind_head,
        ["skill-store"],
        ["records alembic 0003_audit, behind this code's alembic 0007_workflow"],
        [],
    ),
}


def _skill_store(url: str, root: Path):
    return _factory("openminion.modules.skill.storage", "build_skill_store", "s.db")(
        url, root
    )


def _registry_store(url: str, root: Path):
    return _factory(
        "openminion.modules.registry.storage", "build_registry_store", "r.db"
    )(url, root)


def _attempt(owner: str, url: str, tmp_path: Path, capsys) -> str:
    """Run one owner the way an installation does; return its refusal message."""
    if owner == "runtime":
        with pytest.raises(Exception) as refused:
            _start_runtime(url, tmp_path / "rt-again")
        return str(refused.value)
    if owner == "memory-store":
        with pytest.raises(Exception) as refused:
            _memory(url, tmp_path)
        return str(refused.value)
    if owner == "a2a-audit-store":
        with pytest.raises(Exception) as refused:
            _a2a_audit(url, tmp_path / "a2a-again")
        return str(refused.value)
    if owner == "skill-store":
        with pytest.raises(Exception) as refused:
            _skill_store(url, tmp_path / "skill-again")
        return str(refused.value)
    if owner == "registry-store":
        with pytest.raises(Exception) as refused:
            _registry_store(url, tmp_path / "registry-again")
        return str(refused.value)
    entry = _migrate(url, tmp_path, capsys, owner)
    assert entry["code"] == 1 and entry[owner]["status"] == "error", entry
    return str(entry[owner]["error"])


@pytest.mark.parametrize("layout", sorted(LAYOUTS))
def test_unproven_layouts_are_refused_without_writing(
    layout: str, database: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    build, refused, fragments, unaffected = LAYOUTS[layout]
    build(database, tmp_path)
    capsys.readouterr()
    before = _snapshot(database)

    messages = [_attempt(owner, database, tmp_path, capsys) for owner in refused]

    assert _snapshot(database) == before
    joined = "\n".join(messages)
    for fragment in fragments:
        assert fragment in joined
    assert all("Nothing was written" in message for message in messages)

    # Owners that use none of the refused table names still proceed: modules in
    # their own schema, then runtime storage in `public`.
    for owner in (o for o in unaffected if o != "runtime"):
        assert _migrate(database, tmp_path, capsys, owner)[owner]["status"] == "ok"
        assert _ledger(database, f"public__{owner}")[1] == owner
    assert _snapshot(database)["public"] == before["public"]
    if "runtime" in unaffected:
        assert _start_runtime(database, tmp_path / "rt-unaffected") == 15


def test_a_store_behind_head_opens_after_storage_migrate(
    database: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The module store waits for `storage migrate`; opened first, it would strand skill."""
    from openminion.modules.storage.migrations import postgres_schema

    _skill_behind_head(database, tmp_path)
    with pytest.raises(Exception, match="behind this code's alembic 0007"):
        _skill_store(database, tmp_path / "s1")

    assert _migrate(database, tmp_path, capsys, "skill")["skill"]["status"] == "ok"
    store = _skill_store(database, tmp_path / "s2")
    getattr(store, "close", lambda: None)()
    assert _ledger(database, "public") == (["0007_workflow_observations"], "skill")
    history = postgres_schema.complete_history("skill", _read(database, "public"))
    assert history is not None and "current" in history.measured

    # Without the guard (as on dev), the store adds the head's tables under 0003, a
    # layout that is no listed history: from then on `storage migrate` refuses skill.
    _execute(database, "CREATE SCHEMA stuck")
    stuck = schema_url(database, "stuck")
    _legacy_module(stuck, "skill", target="0003_audit")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(postgres_schema, "alembic_head", lambda _: "alembic 0003_audit")
        try:
            getattr(_skill_store(stuck, tmp_path / "s3"), "close", lambda: None)()
        except Exception:  # noqa: BLE001 - a partial bootstrap strands it too
            pass
    entry = _migrate(stuck, tmp_path, capsys, "skill")["skill"]
    assert entry["status"] == "error" and "not provably its own" in entry["error"]


def test_a_failed_module_migration_leaves_no_schema(
    database: str, tmp_path: Path
) -> None:
    location = tmp_path / "failing" / "migrations"
    (location / "versions").mkdir(parents=True)
    (location / "env.py").write_text(
        "from openminion.modules.storage.migrations.alembic import run_module_env\n"
        "run_module_env(module_id='secret', module_application_id=0x4F4D0006)\n",
        encoding="utf-8",
    )
    (location / "versions" / "0001_fails.py").write_text(
        "from alembic import op\n"
        "revision = '0001_fails'\ndown_revision = None\n"
        "branch_labels = None\ndepends_on = None\n\n"
        "def upgrade():\n"
        "    op.execute('CREATE TABLE secrets (id text PRIMARY KEY)')\n"
        "    op.execute('SELECT 1 / 0')\n",
        encoding="utf-8",
    )
    before = _snapshot(database)
    engine = sa.create_engine(database)
    try:
        report = MigrationRunner(
            module_id="secret",
            db_path=tmp_path / "secret.db",
            module_application_id=get_module_application_id("secret"),
            alembic_script_location=location,
            backend_type="postgres",
            engine=engine,
            select_postgres_schema=True,
        ).migrate()
    finally:
        engine.dispose()

    assert report.success is False and "division by zero" in str(report.error)
    assert _snapshot(database) == before
    assert _schemas(database) == ["public"]


# ---------------------------------------------------------------------------- module schemas


def _one_store_table(url: str, tmp_path: Path) -> None:
    """`public__registry` with only `agent_methods`, as the registry store creates it."""
    _execute(
        url,
        'CREATE SCHEMA "public__registry"',
        'CREATE TABLE "public__registry".agent_methods (agent_id TEXT NOT NULL, '
        "method TEXT NOT NULL, quality_tier TEXT, cost_tier TEXT, "
        "latency_hint_ms INTEGER, PRIMARY KEY (agent_id, method))",
    )


def _foreign_engine_table(url: str, tmp_path: Path) -> None:
    _execute(
        url,
        'CREATE SCHEMA "public__registry"',
        'CREATE TABLE "public__registry".core_events (foreign_payload text)',
    )


def _first_start_stopped_before_om_meta(url: str, tmp_path: Path) -> None:
    """The registry store's first start, stopped after its tables, before om_meta."""
    from openminion.modules.storage.runtime import module_store

    def stop(*args, **kwargs):
        raise RuntimeError("stopped before om_meta")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(module_store, "ensure_module_metadata_via_store", stop)
        with pytest.raises(RuntimeError, match="stopped before om_meta"):
            _registry_store(url, tmp_path / "first")


REGISTRY_FIRST_START = {
    "agent_methods",
    "agent_status",
    "agents",
    "core_events",
    "core_sidecar_rows",
    "sidecar_ingest_log",
}
NO_LEDGER = {
    # layout: (builder, the tables of `public__registry`, all exact)
    "one-store-table": (_one_store_table, {"agent_methods"}, True),
    "foreign-engine-table": (_foreign_engine_table, {"core_events"}, False),
    "first-start-stopped-before-om-meta": (
        _first_start_stopped_before_om_meta,
        REGISTRY_FIRST_START,
        True,
    ),
}


@pytest.mark.parametrize("layout", sorted(NO_LEDGER))
def test_a_module_schema_without_its_ledger_is_refused_without_writing(
    layout: str, database: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Tables in `<base>__<module id>` without the module's ledger prove nothing, also
    when they have exactly the columns this code creates; the base schema is empty."""
    build, tables, exact = NO_LEDGER[layout]
    # What a complete first start of this code's registry store creates, elsewhere.
    _execute(database, "CREATE SCHEMA ref")
    _registry_store(schema_url(database, "ref"), tmp_path / "ref").close()
    reference = _all_signatures(database, "ref__registry")
    build(database, tmp_path)
    found = _all_signatures(database, "public__registry")
    assert set(found) == tables and not _tables(database, "public")
    assert exact == all(reference.get(t) == sig for t, sig in found.items())
    capsys.readouterr()
    before = _snapshot(database)

    messages = [
        _attempt(owner, database, tmp_path, capsys)
        for owner in ("registry-store", "registry")
    ]

    # No new schema, table, ledger row or row, and no changed column or index.
    assert _snapshot(database) == before
    for message in messages:
        assert (
            f'"public__registry" holds tables ({", ".join(sorted(tables))}) but '
            "records no registry ledger"
        ) in message
        assert "Nothing was written" in message and "take a full pg_dump" in message


def test_a_fresh_module_schema_is_initialized_and_reopened(
    database: str, tmp_path: Path
) -> None:
    """Controls: an empty `<base>__<module id>` (here one a database administrator
    created for registry; the others do not exist yet) is initialized, and each
    store's first start leaves its ledger, so the next start is accepted."""
    from openminion.modules.storage.migrations import postgres_schema

    _execute(database, 'CREATE SCHEMA "public__registry"')
    for name, (module_id, build) in _STORES.items():
        for start in ("first", "next"):
            close = getattr(
                build(database, tmp_path / f"{name}-{start}"), "close", None
            )
            if callable(close):
                close()
        layout = _read(database, f"public__{module_id}")
        history = postgres_schema.complete_history(module_id, layout)
        assert history is not None and "current" in history.measured, name
    assert _schemas(database) == sorted(
        ["public", *{f"public__{m}" for m, _ in _STORES.values()}]
    )
    assert not _tables(database, "public")


def test_a_proven_module_schema_never_touches_unproven_base_tables(
    database: str, tmp_path: Path
) -> None:
    """Control: after runtime storage's `sessions` is altered, runtime storage is
    refused, and the session store keeps its proven `public__session` without
    reading or writing the base schema."""
    from openminion.modules.session.runtime.factory import build_module_session_store

    def sessions(root: Path):
        return build_module_session_store(
            config=_storage_config(database, root),
            database_path=root / "sessions.db",
            env={},
        )

    _start_runtime(database, tmp_path / "rt")
    _runtime_rows(database)
    sessions(tmp_path / "s1").close()
    _execute(database, "ALTER TABLE public.sessions ADD COLUMN added_by_hand TEXT")
    with pytest.raises(Exception, match="not provably its own"):
        _start_runtime(database, tmp_path / "rt2")
    before = _snapshot(database)["public"]

    store = sessions(tmp_path / "s2")
    try:
        session_id = store.create_session(
            initial_agent_id="a", profile_version="v1", title="t", tags=[]
        )
        assert store.get_session(session_id) is not None
    finally:
        store.close()

    assert _snapshot(database)["public"] == before


# ---------------------------------------------------------------------------- schemas, roles


def test_an_empty_schema_a_dba_created_serves_a_role_without_create(
    database: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    role = f"om446_app_{uuid.uuid4().hex[:6]}"
    admin = _admin()
    with admin.connect() as conn:
        conn.execute(sa.text(f"CREATE ROLE {role} LOGIN PASSWORD 'om446'"))
    try:
        name = sa.engine.make_url(database).database
        _execute(
            database,
            f'GRANT CONNECT ON DATABASE "{name}" TO {role}',
            f'CREATE SCHEMA "public__memory" AUTHORIZATION {role}',
            'CREATE SCHEMA "public__secret"',
            # Unreadable without USAGE: the refusal must still name the GRANT.
            'CREATE TABLE "public__secret".alembic_version '
            "(version_num varchar(32) PRIMARY KEY)",
        )
        role_url = (
            sa.engine.make_url(database)
            .set(username=role, password="om446")
            .render_as_string(hide_password=False)
        )
        before = _snapshot(database)

        memory = _memory(role_url, tmp_path)
        try:
            memory.put(_memory_record("rec-1"))
        finally:
            memory.close()
        assert _ledger(database, "public__memory") == (["0004_namespace"], "memory")

        no_grant = _migrate(role_url, tmp_path, capsys, "secret")["secret"]["error"]
        assert f'GRANT USAGE, CREATE ON SCHEMA "public__secret" TO "{role}"' in no_grant
        no_create = _migrate(role_url, tmp_path, capsys, "registry")["registry"][
            "error"
        ]
        assert f'CREATE SCHEMA "public__registry" AUTHORIZATION "{role}"' in no_create
        after = _snapshot(database)
        assert after["public__secret"] == before["public__secret"]
        assert "public__registry" not in after
    finally:
        _execute(database, f"DROP OWNED BY {role}")
        with admin.connect() as conn:
            conn.execute(sa.text(f"DROP ROLE IF EXISTS {role}"))
        admin.dispose()


def _with_options(url: str, options: str, **query: str) -> str:
    parsed = sa.engine.make_url(url)
    return parsed.update_query_dict({"options": options, **query}).render_as_string(
        hide_password=False
    )


def test_base_schema_follows_the_url_and_names_are_checked(
    database: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _execute(database, 'CREATE SCHEMA "AppBase"', f'CREATE SCHEMA "{"b" * 60}"')

    mixed = _with_options(database, '-c search_path="AppBase"')
    assert _migrate(mixed, tmp_path, capsys, "secret")["secret"]["status"] == "ok"
    assert _ledger(database, "AppBase__secret") == (["0001_baseline"], "secret")

    long_base = _with_options(database, f'-c search_path="{"b" * 60}"')
    error = _migrate(long_base, tmp_path, capsys, "secret")["secret"]["error"]
    assert "longer than 63 bytes" in error

    missing = _with_options(database, "-c search_path=missing_schema")
    error = _migrate(missing, tmp_path, capsys, "secret")["secret"]["error"]
    assert "search_path names no existing schema" in error
    with pytest.raises(Exception, match="search_path names no existing schema"):
        _start_runtime(missing, tmp_path / "rt")


def test_module_connections_keep_the_url_options(
    database: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = _with_options(
        database, "-c statement_timeout=12345", application_name="om446_options"
    )
    memory = _memory(url, tmp_path)
    try:
        with memory._engine.connect() as conn:  # noqa: SLF001
            values = conn.execute(
                sa.text(
                    "SELECT current_schema(), current_setting('statement_timeout'), "
                    "current_setting('application_name')"
                )
            ).one()
    finally:
        memory.close()
    assert tuple(values) == ("public__memory", "12345ms", "om446_options")

    from openminion.modules.storage.migrations import postgres_schema

    monkeypatch.setenv("PGOPTIONS", "-c lock_timeout=4321")
    plain = postgres_schema.module_url(database, "public__memory")
    engine = sa.create_engine(plain)
    try:
        with engine.connect() as conn:
            assert conn.execute(sa.text("SHOW lock_timeout")).scalar() == "4321ms"
    finally:
        engine.dispose()


def test_startup_check_reads_each_module_in_its_schema(
    database: str, tmp_path: Path, capsys, caplog: pytest.LogCaptureFixture
) -> None:
    from openminion.modules.storage.runtime.validation import validate_postgres_config

    assert _migrate(database, tmp_path, capsys, "secret")["code"] == 0
    with caplog.at_level(logging.WARNING):
        validate_postgres_config(database)
    pending = {
        m for m in re.findall(r"Postgres module '(\w+)' has pending", caplog.text)
    }
    assert "secret" not in pending and "memory" in pending

    caplog.clear()
    _execute(database, "CREATE TABLE public.memory_records (id integer PRIMARY KEY)")
    with caplog.at_level(logging.WARNING):
        validate_postgres_config(database)
    assert "Postgres module 'memory': Module 'memory' will not use" in caplog.text


# ---------------------------------------------------------------------------- listed histories


def _read(url: str, schema: str):
    from openminion.modules.storage.migrations import postgres_schema

    engine = sa.create_engine(url)
    try:
        with engine.connect() as conn:
            return postgres_schema.read_schema(conn, schema)
    finally:
        engine.dispose()


def _factory(module: str, name: str, filename: str):
    def build(url: str, root: Path):
        import importlib

        kwargs = {
            "config": _storage_config(url, root),
            "database_path": root / filename,
        }
        if name == "build_module_session_store":
            kwargs["env"] = {}
        return getattr(importlib.import_module(module), name)(**kwargs)

    return build


_STORES = {
    "memory_store": ("memory", lambda url, root: _memory(url, root)),
    "a2a_audit_store": ("a2a", _a2a_audit),
    "a2a_state_store": (
        "a2a",
        _factory(
            "openminion.modules.a2a.storage.factory", "build_a2a_state_store", "a2a.db"
        ),
    ),
    "session_store": (
        "session",
        _factory(
            "openminion.modules.session.runtime.factory",
            "build_module_session_store",
            "sessions.db",
        ),
    ),
    "controlplane_store": (
        "controlplane",
        _factory(
            "openminion.modules.controlplane.storage",
            "build_controlplane_store",
            "cp.db",
        ),
    ),
    "telemetry_store": (
        "telemetry",
        _factory(
            "openminion.modules.telemetry.storage", "build_telemetry_store", "t.db"
        ),
    ),
    **{
        f"{module_id}_store": (
            module_id,
            _factory(
                f"openminion.modules.{path}.storage", f"build_{fn}", f"{module_id}.db"
            ),
        )
        for module_id, path, fn in (
            ("registry", "registry", "registry_store"),
            ("artifact", "artifact", "artifact_index"),
            ("identity", "identity", "identity_store"),
            ("policy", "policy", "policy_store"),
            ("retrieve", "retrieve", "retrieve_store"),
            ("skill", "skill", "skill_store"),
            ("task", "task", "task_store"),
            ("compress", "context.compress", "compress_telemetry_store"),
        )
    },
}


def _migrate_in(url: str, module_id: str, tmp_path: Path):
    engine = sa.create_engine(url)
    try:
        report = MigrationRunner(
            module_id=module_id,
            db_path=tmp_path / f"{module_id}.db",
            module_application_id=get_module_application_id(module_id),
            backend_type="postgres",
            engine=engine,
            select_postgres_schema=True,
        ).migrate()
    finally:
        engine.dispose()
    return report


def _all_signatures(url: str, schema: str) -> dict[str, str]:
    """Every table's signature in ``schema``, also for names no listed history has."""
    from openminion.modules.storage.migrations import postgres_schema

    engine = sa.create_engine(url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                sa.text(
                    "SELECT c.relname, a.attname, format_type(a.atttypid, a.atttypmod), "
                    "a.attnotnull, coalesce(a.attnum = ANY(i.indkey), false) "
                    "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "JOIN pg_attribute a ON a.attrelid = c.oid "
                    "LEFT JOIN pg_index i ON i.indrelid = c.oid AND i.indisprimary "
                    "WHERE n.nspname = :s AND c.relkind IN ('r', 'p') "
                    "AND a.attnum > 0 AND NOT a.attisdropped"
                ),
                {"s": schema},
            ).all()
    finally:
        engine.dispose()
    columns: dict[str, list] = {}
    keys: dict[str, list[str]] = {}
    for table, name, typ, notnull, key in rows:
        columns.setdefault(table, []).append((name, typ, bool(notnull)))
        if key:
            keys.setdefault(table, []).append(name)
    return {
        table: postgres_schema.signature(cols, keys.get(table, []))
        for table, cols in columns.items()
    }


def _paste_ready(unlisted: list[tuple[str, str, dict[str, str]]]) -> str:
    """Lines to add to postgres_histories.json for layouts this code builds."""
    from openminion.modules.storage.migrations import postgres_schema

    data = json.loads(postgres_schema._HISTORIES_FILE.read_text(encoding="utf-8"))  # noqa: SLF001
    refs = {
        (ref.rsplit("/", 1)[0], sig): ref for ref, sig in data["signatures"].items()
    }
    last: dict[str, int] = {}
    for ref in data["signatures"]:
        table, number = ref.rsplit("/", 1)
        last[table] = max(last.get(table, 0), int(number))
    signatures: dict[str, str] = {}
    histories: list[str] = []
    for owner, ledger, tables in unlisted:
        names = []
        for table, sig in sorted(tables.items()):
            if (table, sig) not in refs:
                last[table] = last.get(table, 0) + 1
                refs[(table, sig)] = f"{table}/{last[table]}"
                signatures[refs[(table, sig)]] = sig
            names.append(refs[(table, sig)])
        entry = {"owner": owner, "ledger": ledger, "tables": names}
        histories.append(json.dumps({**entry, "measured": ["current"]}))
    lines = ['Add to "signatures":']
    lines += [f"{json.dumps(k)}: {json.dumps(v)}," for k, v in signatures.items()]
    lines += ['Add to "histories" (or add "current" to an equal entry):']
    lines += [f"{history}," for history in histories]
    return "\n".join(lines)


def test_this_code_builds_only_listed_histories(database: str, tmp_path: Path) -> None:
    """Every layout this code builds, one revision at a time, is listed as current.

    On a failure the message holds the postgres_histories.json lines to add (see
    "PostgreSQL schemas per owner" in docs/runtime-database-upgrades.md).
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from openminion.modules.storage.backends.postgres import RecordStorePostgres
    from openminion.modules.storage.migrations.alembic import (
        discover_module_alembic_paths,
    )
    from openminion.modules.storage.migrations import postgres_schema
    from openminion.modules.storage.migrations.postgres_schema import ModuleSchema
    from openminion.modules.storage.runtime.migrations import (
        DEFAULT_MIGRATIONS,
        migrate_record_store,
    )

    built: set[tuple[str, str]] = set()
    unlisted: list[tuple[str, str, dict[str, str]]] = []
    catalog = postgres_schema._catalog()  # noqa: SLF001
    skip = set(catalog.engine_tables) | {"alembic_version", "om_meta"}
    engine_built: dict[str, set[str]] = {}

    def check(owner: str, schema: str) -> None:
        layout = _read(database, schema)
        for table in catalog.engine_tables:
            if layout.tables.get(table) is not None:
                engine_built.setdefault(table, set()).add(str(layout.tables[table]))
        history = postgres_schema.complete_history(owner, layout)
        owned = {t for t in layout.tables if t in catalog.names[owner]}
        if (
            history is not None
            and "current" in history.measured
            and owned == set(history.tables)
        ):
            built.add((owner, history.ledger))
            return
        if owner == postgres_schema.RUNTIME:
            ledger = f"v{len(layout.migrations or ())}"
        elif layout.alembic is not None:
            ledger = f"alembic {layout.alembic[0] if layout.alembic else 'base'}"
        else:
            ledger = f"store {(layout.om_meta or {}).get('schema_head')}"
        tables = _all_signatures(database, schema)
        unlisted.append(
            (owner, ledger, {t: v for t, v in tables.items() if t not in skip})
        )

    _execute(database, "CREATE SCHEMA rt")
    store = RecordStorePostgres(schema_url(database, "rt"))
    try:
        ordered = sorted(DEFAULT_MIGRATIONS, key=lambda m: m.version)
        for k in range(1, len(ordered) + 1):
            migrate_record_store(store, backend_type="postgres", migrations=ordered[:k])
            check("runtime", "rt")
    finally:
        store.close()

    for module_id in WORKING_MODULES:
        _, location = discover_module_alembic_paths(module_id)
        cfg = Config()
        cfg.set_main_option("script_location", str(location))
        chain = [
            s.revision
            for s in reversed(list(ScriptDirectory.from_config(cfg).walk_revisions()))
        ]
        schema = f"m_{module_id}"
        _execute(database, f'CREATE SCHEMA "{schema}"')
        engine = sa.create_engine(database)
        try:
            for revision in chain:
                report = MigrationRunner(
                    module_id=module_id,
                    db_path=tmp_path / f"{module_id}.db",
                    module_application_id=get_module_application_id(module_id),
                    backend_type="postgres",
                    engine=engine,
                    postgres_schema=ModuleSchema(module_id, schema, schema),
                ).migrate(target=revision)
                assert report.success, report.error
                check(module_id, schema)
        finally:
            engine.dispose()
        # And the chain downgraded to base with Alembic, which leaves only the ledger
        # tables. The memory, session and skill downgrades fail on PostgreSQL, so their
        # base is not listed.
        if module_id not in ("memory", "session", "skill"):
            _legacy_module(
                schema_url(database, schema), module_id, "base", downgrade=True
            )
            check(module_id, schema)

    # Each module store alone, after its module's Alembic head, and followed by it.
    for index, (name, (module_id, build)) in enumerate(_STORES.items()):
        for order in ("s", "a", "b"):
            if order != "s" and module_id not in WORKING_MODULES:
                continue
            base = f"b{index}{order}_{module_id}"
            _execute(database, f'CREATE SCHEMA "{base}"')
            url = schema_url(database, base)
            if order == "a":
                assert _migrate_in(url, module_id, tmp_path).success
            close = getattr(build(url, tmp_path / f"{base}-{name}"), "close", None)
            if callable(close):
                close()
            if order == "b":
                # A failed upgrade rolls back and leaves the store's own layout.
                _migrate_in(url, module_id, tmp_path)
            check(module_id, f"{base}__{module_id}")

    # The secret store has no PostgreSQL factory: it is built on a record store
    # its caller passes, in that record store's schema.
    from openminion.modules.secret.storage.store import PostgresSecretStore

    _execute(database, 'CREATE SCHEMA "b_secret"')
    secret = PostgresSecretStore(
        record_store=RecordStorePostgres(schema_url(database, "b_secret"))
    )
    secret.close()
    check("secret", "b_secret")

    listed = {
        (owner, ledger)
        for owner, ledgers in postgres_schema._catalog().histories.items()  # noqa: SLF001
        for ledger, items in ledgers.items()
        if any("current" in h.measured for h in items)
    }
    assert not unlisted, _paste_ready(unlisted)
    assert (sorted(listed - built), sorted(built - listed)) == ([], [])
    # The storage engine's tables next to each store's tables, exactly as listed.
    assert {t: set(s) for t, s in catalog.engine_tables.items()} == engine_built, (
        '"engine_tables": '
        + json.dumps({t: sorted(s) for t, s in sorted(engine_built.items())}, indent=1)
    )


# ---------------------------------------------------------------------------- relocation


def _documented_sql(name: str) -> list[str]:
    """The statements of the block marked `<!-- 446:<name> -->` in the upgrade docs."""
    text = DOCS.read_text(encoding="utf-8")
    match = re.search(rf"<!-- 446:{re.escape(name)} -->\s*```sql\n(.*?)```", text, re.S)
    assert match, name
    statements = [s.strip() for s in match.group(1).split(";\n")]
    return [s for s in statements if s and s not in ("BEGIN", "COMMIT")]


def test_relocation_r1_and_its_inverse(database: str, tmp_path: Path) -> None:
    _legacy_memory(database, tmp_path)
    original = _snapshot(database)
    with pytest.raises(Exception, match="not provably its own"):
        _start_runtime(database, tmp_path / "rt0")

    _execute(database, *_documented_sql("R1"))
    _execute(database, *_documented_sql("R1-inverse"))
    assert _snapshot(database) == original

    _execute(database, *_documented_sql("R1"))
    memory = _memory(database, tmp_path)
    try:
        assert memory.get("rec-1") is not None
        memory.put(_memory_record("rec-2"))
    finally:
        memory.close()
    assert _start_runtime(database, tmp_path / "rt1") == 15
    assert _ledger(database, "public__memory") == (["0004_namespace"], "memory")
    assert _schemas(database) == ["public", "public__memory"]


def test_relocation_r1b_lets_runtime_resume(database: str, tmp_path: Path) -> None:
    _memory_then_failed_runtime(database, tmp_path)

    _execute(database, *_documented_sql("R1b"))

    assert _start_runtime(database, tmp_path / "rt") == 15
    memory = _memory(database, tmp_path)
    try:
        assert memory.get("rec-1") is not None
    finally:
        memory.close()


def test_relocation_r2_parks_and_copies_the_module_rows(
    database: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _runtime_then_a2a(database, tmp_path)
    runtime_keys = _snapshot(database)["public"]["tables"]["idempotency_keys"]

    _execute(database, *_documented_sql("R2-park"))
    assert _migrate(database, tmp_path, capsys, "a2a")["a2a"]["status"] == "ok"
    _execute(database, *_documented_sql("R2-copy"))

    audit = _a2a_audit(database, tmp_path / "a2a")
    try:
        audit.append_audit(_audit_record("msg-1"))
        assert sorted(r.msg_id for r in audit.query_audit({"limit": 10})) == [
            "msg-0",
            "msg-1",
        ]
    finally:
        audit.close()
    assert _snapshot(database)["public"]["tables"]["idempotency_keys"] == runtime_keys
    assert _start_runtime(database, tmp_path / "rt2") == 15
