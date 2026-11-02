"""PostgreSQL `0001_baseline` of `controlplane` and `telemetry`, with their stores (#494).

Each test starts from a new schema of its own. `storage migrate` puts a module in
its own schema under it, `<schema>__<module id>` (#446), where the tests read it.
"""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from types import SimpleNamespace
from typing import Any, Iterator
import uuid

import pytest

from openminion.cli.commands.storage import run_storage_migrate
from openminion.modules.controlplane.storage.schema import MIGRATIONS as CP_MIGRATIONS
from openminion.modules.controlplane.storage.store import PostgresControlPlaneStore
from openminion.modules.storage.backends.postgres import RecordStorePostgres
from openminion.modules.storage.migrations.alembic import (
    discover_module_alembic_paths,
)
from openminion.modules.storage.migrations.module_ids import get_module_application_id
from openminion.modules.storage.migrations.runner import MigrationRunner
from openminion.modules.telemetry.schemas import (
    TelemetryEvent,
    normalize_telemetry_event,
)
from openminion.modules.telemetry.storage.store import PostgresTelemetryStore
from tests.storage.postgres_test_utils import drop_schemas, schema_url

pytestmark = pytest.mark.postgres

MODULES = ("controlplane", "telemetry")
HEADS = {"controlplane": "0002_index_audit", "telemetry": "0003_debug_query_indexes"}
SERIAL_ID_TABLES = {
    "controlplane": ("cp_inbound_messages", "cp_outbound_messages", "cp_pair_attempts"),
    "telemetry": ("events",),
}
TIMESTAMPS = (1761650000.123456, 1761650001.654321)


@pytest.fixture
def schema() -> Iterator[str]:
    sa = pytest.importorskip("sqlalchemy")
    postgres_url = str(os.getenv("OPENMINION_TEST_POSTGRES_URL", "")).strip()
    if not postgres_url:
        pytest.skip("OPENMINION_TEST_POSTGRES_URL is not set")
    schema_name = f"baseline_{uuid.uuid4().hex}"
    admin_engine = sa.create_engine(postgres_url, future=True)
    with admin_engine.begin() as conn:
        conn.execute(sa.text(f'CREATE SCHEMA "{schema_name}"'))
    try:
        yield schema_url(postgres_url, schema_name)
    finally:
        drop_schemas(admin_engine, schema_name)
        admin_engine.dispose()


def _module_url(url: str, module_id: str) -> str:
    """The module's own schema under the test schema, `<schema>__<module id>` (#446)."""
    return schema_url(_server_url(), f"{_schema_name(url)}__{module_id}")


def _schema_name(url: str) -> str:
    sa = pytest.importorskip("sqlalchemy")
    options = str(sa.engine.make_url(url).query["options"])
    return options.split("search_path=", 1)[1]


def _server_url() -> str:
    return str(os.getenv("OPENMINION_TEST_POSTGRES_URL", "")).strip()


def _migrate(url: str, module_id: str, tmp_path, capsys) -> dict[str, Any]:
    args = SimpleNamespace(
        backend="postgres",
        postgres_url=url,
        sqlite=str(tmp_path / "openminion.db"),
        module=module_id,
        plan=False,
        verify=False,
        json=True,
    )
    exit_code: object = 0
    try:
        run_storage_migrate(args)
    except SystemExit as exc:
        exit_code = exc.code
    (entry,) = json.loads(capsys.readouterr().out)["modules"]
    return {"exit": exit_code, **entry}


def _downgrade_to_base(url: str, module_id: str) -> None:
    from alembic import command
    from alembic.config import Config

    ini_path, script_location = discover_module_alembic_paths(module_id)
    config = Config(str(ini_path))
    config.set_main_option("script_location", str(script_location))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.downgrade(config, "base")


@contextmanager
def _connect(url: str) -> Iterator[Any]:
    sa = pytest.importorskip("sqlalchemy")
    engine = sa.create_engine(url, future=True)
    try:
        with engine.connect() as conn:
            yield conn
    finally:
        engine.dispose()


def _scalars(url: str, sql: str, **params: Any) -> list[Any]:
    sa = pytest.importorskip("sqlalchemy")
    with _connect(url) as conn:
        return list(conn.execute(sa.text(sql), params).scalars())


def _tables(url: str) -> list[str]:
    return sorted(
        _scalars(
            url,
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = current_schema()",
        )
    )


def _columns(url: str, table_name: str) -> dict[str, tuple[str, str | None]]:
    sa = pytest.importorskip("sqlalchemy")
    with _connect(url) as conn:
        rows = conn.execute(
            sa.text(
                "SELECT column_name, data_type, column_default "
                "FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = :table_name "
                "ORDER BY ordinal_position"
            ),
            {"table_name": table_name},
        ).all()
    return {str(name): (str(data_type), default) for name, data_type, default in rows}


def _revision(url: str) -> list[str]:
    if "alembic_version" not in _tables(url):
        return []
    return _scalars(url, "SELECT version_num FROM alembic_version")


def _snapshot(url: str, module_id: str) -> dict[str, Any]:
    tables = _tables(url)
    return {
        "tables": tables,
        "revision": _revision(url),
        "columns": {table: _columns(url, table) for table in tables},
        "rows": {
            table: _scalars(url, f'SELECT COUNT(*) FROM "{table}"')
            for table in SERIAL_ID_TABLES[module_id]
            if table in tables
        },
    }


@contextmanager
def _module_store(url: str, module_id: str) -> Iterator[Any]:
    sa = pytest.importorskip("sqlalchemy")
    engine = sa.create_engine(url, future=True)
    try:
        record_store = RecordStorePostgres(engine)
        store: Any
        if module_id == "controlplane":
            store = PostgresControlPlaneStore(record_store=record_store)
        else:
            store = PostgresTelemetryStore(record_store=record_store)
        try:
            yield store
        finally:
            store.close()
    finally:
        engine.dispose()


def _write(store: Any, module_id: str, index: int) -> None:
    if module_id == "controlplane":
        store.put_inbound(
            chat_key="chat:1",
            user_key="user:1",
            text=f"message {index}",
            payload={"message_id": f"message-{index}"},
            session_id="session-1",
        )
        return
    store.insert_event(
        normalize_telemetry_event(
            TelemetryEvent(
                session_id="session-1",
                turn_id=f"turn-{index}",
                event_type="tool.completed",
                timestamp=TIMESTAMPS[index],
                data={"index": index},
            )
        )
    )


def _read(store: Any, module_id: str) -> list[tuple[int, Any]]:
    if module_id == "controlplane":
        return [
            (int(turn["id"]), turn["text"]) for turn in store.list_turns("session-1")
        ]
    page = store.fetch_event_page(high_water=store.event_high_water(), limit=10)
    return sorted((row.row_id, row.event.timestamp) for row in page)


def _expected_rows(module_id: str, count: int) -> list[tuple[int, Any]]:
    if module_id == "controlplane":
        return [(index + 1, f"message {index}") for index in range(count)]
    return [(index + 1, TIMESTAMPS[index]) for index in range(count)]


@pytest.mark.parametrize("module_id", MODULES)
def test_baseline_applies_to_an_empty_schema(
    schema: str, module_id: str, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    entry = _migrate(schema, module_id, tmp_path, capsys)
    module = _module_url(schema, module_id)

    assert entry == {"exit": 0, "module_id": module_id, "status": "ok", "success": True}
    assert _tables(schema) == []
    assert _revision(module) == [HEADS[module_id]]
    assert _scalars(module, "SELECT value FROM om_meta WHERE key = 'module_id'") == [
        module_id
    ]
    for table_name in SERIAL_ID_TABLES[module_id]:
        assert _columns(module, table_name)["id"] == (
            "integer",
            f"nextval('{table_name}_id_seq'::regclass)",
        )
    if module_id == "telemetry":
        assert _columns(module, "events")["timestamp"][0] == "double precision"


def test_controlplane_store_round_trips_on_the_baseline_schema(
    schema: str, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _migrate(schema, "controlplane", tmp_path, capsys)["status"] == "ok"
    schema = _module_url(schema, "controlplane")
    columns = {table: _columns(schema, table) for table in _tables(schema)}

    with _module_store(schema, "controlplane") as store:
        inbound_ids = [
            store.put_inbound(
                chat_key="chat:1",
                user_key="user:1",
                text=text,
                payload={"message_id": text},
                session_id="session-1",
            )
            for text in ("one", "two")
        ]
        outbound_id = store.put_outbound(
            chat_key="chat:1", text="reply", payload={}, session_id="session-1"
        )
        store.record_pair_attempt(
            channel="telegram",
            account_id="account-1",
            chat_key="chat:1",
            token="not-a-token",
            outcome="invalid_token",
        )
        turns = store.list_turns("session-1")
        attempts = store.count_recent_pair_attempts(
            channel="telegram", account_id="account-1", since_ts=0
        )

    assert inbound_ids == [1, 2]
    assert outbound_id == 1
    assert [(turn["id"], turn["text"]) for turn in turns] == [(1, "one"), (2, "two")]
    assert attempts == 1
    assert _scalars(schema, "SELECT version FROM cp_migrations ORDER BY version") == [
        version for version, _name, _sql in CP_MIGRATIONS
    ]
    assert {table: _columns(schema, table) for table in _tables(schema)} == columns
    assert _revision(schema) == [HEADS["controlplane"]]


def test_telemetry_store_keeps_timestamp_precision_on_the_baseline_schema(
    schema: str, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _migrate(schema, "telemetry", tmp_path, capsys)["status"] == "ok"
    schema = _module_url(schema, "telemetry")

    with _module_store(schema, "telemetry") as store:
        _write(store, "telemetry", 0)
        events = store.fetch_session_events("session-1")
        page = store.fetch_event_page(high_water=store.event_high_water(), limit=10)

    assert [event.timestamp for event in events] == [TIMESTAMPS[0]]
    assert [(row.row_id, row.event.timestamp, row.timestamp_valid) for row in page] == [
        (1, TIMESTAMPS[0], True)
    ]
    assert _scalars(schema, "SELECT timestamp FROM events") == [TIMESTAMPS[0]]
    assert _revision(schema) == [HEADS["telemetry"]]


@pytest.mark.parametrize("module_id", MODULES)
def test_second_upgrade_changes_nothing(
    schema: str, module_id: str, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _migrate(schema, module_id, tmp_path, capsys)["status"] == "ok"
    module = _module_url(schema, module_id)
    with _module_store(module, module_id) as store:
        _write(store, module_id, 0)
    before = _snapshot(module, module_id)

    entry = _migrate(schema, module_id, tmp_path, capsys)

    assert entry == {"exit": 0, "module_id": module_id, "status": "ok", "success": True}
    assert _snapshot(module, module_id) == before
    with _module_store(module, module_id) as store:
        assert _read(store, module_id) == _expected_rows(module_id, 1)


@pytest.mark.parametrize("module_id", MODULES)
def test_downgrade_removes_the_baseline_and_upgrade_restores_it(
    schema: str, module_id: str, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _migrate(schema, module_id, tmp_path, capsys)["status"] == "ok"
    module = _module_url(schema, module_id)
    created = _tables(module)

    _downgrade_to_base(module, module_id)

    assert _revision(module) == []
    # The module's Alembic environment writes om_meta again after every run.
    assert _tables(module) == ["alembic_version", "om_meta"]
    assert (
        _scalars(
            module,
            "SELECT sequence_name FROM information_schema.sequences "
            "WHERE sequence_schema = current_schema()",
        )
        == []
    )

    entry = _migrate(schema, module_id, tmp_path, capsys)

    assert entry == {"exit": 0, "module_id": module_id, "status": "ok", "success": True}
    assert _tables(module) == created
    assert _revision(module) == [HEADS[module_id]]
    with _module_store(module, module_id) as store:
        _write(store, module_id, 0)
        assert _read(store, module_id) == _expected_rows(module_id, 1)


@pytest.mark.parametrize("module_id", MODULES)
def test_tables_the_store_created_first_are_upgraded_in_place(
    schema: str, module_id: str, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    with _module_store(schema, module_id) as store:
        _write(store, module_id, 0)
    assert _revision(schema) == []
    columns = {table: _columns(schema, table) for table in SERIAL_ID_TABLES[module_id]}

    entry = _migrate(schema, module_id, tmp_path, capsys)

    assert entry == {"exit": 0, "module_id": module_id, "status": "ok", "success": True}
    assert _revision(schema) == [HEADS[module_id]]
    assert {
        table: _columns(schema, table) for table in SERIAL_ID_TABLES[module_id]
    } == columns
    with _module_store(schema, module_id) as store:
        assert _read(store, module_id) == _expected_rows(module_id, 1)
        _write(store, module_id, 1)
        assert _read(store, module_id) == _expected_rows(module_id, 2)


@pytest.mark.parametrize(
    ("module_id", "table_name", "create_sql", "insert_sql", "message"),
    [
        pytest.param(
            "telemetry",
            "events",
            "CREATE TABLE events (id SERIAL PRIMARY KEY, session_id TEXT NOT NULL, "
            "turn_id TEXT NOT NULL, event_type TEXT NOT NULL, "
            "timestamp REAL NOT NULL, data TEXT NOT NULL)",
            "INSERT INTO events (session_id, turn_id, event_type, timestamp, data) "
            "VALUES ('session-1', 'turn-1', 'tool.completed', 1761650000.123456, '{}')",
            "events.timestamp is REAL, expected DOUBLE PRECISION",
            id="telemetry-real-timestamp",
        ),
        pytest.param(
            "controlplane",
            "cp_inbound_messages",
            "CREATE TABLE cp_inbound_messages (id INTEGER PRIMARY KEY, "
            "message_id TEXT NOT NULL, chat_key TEXT NOT NULL, user_key TEXT NOT NULL, "
            "session_id TEXT, agent_id TEXT, timestamp TEXT NOT NULL, text TEXT, "
            "payload_json TEXT NOT NULL)",
            "INSERT INTO cp_inbound_messages VALUES "
            "(7, 'message-7', 'chat:1', 'user:1', NULL, NULL, 'ts', 'hello', '{}')",
            "cp_inbound_messages.id is INTEGER, expected SERIAL",
            id="controlplane-integer-id",
        ),
        pytest.param(
            "telemetry",
            "events",
            "CREATE TABLE events (event_id TEXT PRIMARY KEY, "
            "session_id TEXT NOT NULL, payload_json TEXT NOT NULL)",
            "INSERT INTO events VALUES ('event-1', 'session-1', '{}')",
            "events.id is missing, expected SERIAL; "
            "events.timestamp is missing, expected DOUBLE PRECISION",
            id="telemetry-other-columns",
        ),
    ],
)
def test_existing_table_with_other_types_is_not_adopted(
    schema: str,
    module_id: str,
    table_name: str,
    create_sql: str,
    insert_sql: str,
    message: str,
    tmp_path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sa = pytest.importorskip("sqlalchemy")
    engine = sa.create_engine(schema, future=True)
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(create_sql)
            conn.exec_driver_sql(insert_sql)
            rows = conn.exec_driver_sql(f"SELECT * FROM {table_name}").all()
    finally:
        engine.dispose()
    columns = _columns(schema, table_name)

    # `storage migrate` refuses the table before any write: no ledger proves it is
    # the module's (#446).
    entry = _migrate(schema, module_id, tmp_path, capsys)

    assert entry["exit"] == 1
    assert (entry["status"], entry["success"]) == ("error", False)
    assert (
        f"no {module_id} ledger is recorded there; {table_name} matches no recorded "
        "ledger" in entry["error"]
    )
    assert "Nothing was written" in entry["error"]
    assert _tables(schema) == [table_name]

    # The revision itself, run in that schema, does not adopt the table either.
    from openminion.modules.storage.migrations.postgres_schema import ModuleSchema

    engine = sa.create_engine(schema, future=True)
    try:
        report = MigrationRunner(
            module_id=module_id,
            db_path=tmp_path / f"{module_id}.db",
            module_application_id=get_module_application_id(module_id),
            backend_type="postgres",
            engine=engine,
            postgres_schema=ModuleSchema(
                module_id, _schema_name(schema), _schema_name(schema)
            ),
        ).migrate()
    finally:
        engine.dispose()

    assert report.success is False
    assert (
        "Existing tables differ from this revision and are not adopted: " + message
    ) in str(report.error)
    assert _tables(schema) == [table_name]
    assert _columns(schema, table_name) == columns
    with _connect(schema) as conn:
        assert conn.exec_driver_sql(f"SELECT * FROM {table_name}").all() == rows
