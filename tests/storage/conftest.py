from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

import pytest

from openminion.modules.storage.record_store import RecordStore, RecordStoreSQLite


@dataclass(frozen=True)
class BackendCase:
    name: str
    store: RecordStore


def _backend_params():
    return [
        pytest.param("sqlite", id="sqlite"),
        pytest.param("postgres", marks=pytest.mark.postgres, id="postgres"),
    ]


@pytest.fixture(params=_backend_params())
def record_store_case(
    request: pytest.FixtureRequest, tmp_path: Path
) -> Iterator[BackendCase]:
    backend_name = str(request.param)
    if backend_name == "sqlite":
        store = RecordStoreSQLite(tmp_path / "conformance.db")
        try:
            yield BackendCase(name="sqlite", store=store)
        finally:
            store.close()
        return

    postgres_url = str(os.getenv("OPENMINION_TEST_POSTGRES_URL", "")).strip()
    if not postgres_url:
        pytest.skip("OPENMINION_TEST_POSTGRES_URL is not set")

    try:
        from sqlalchemy import create_engine
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"sqlalchemy unavailable for postgres backend: {exc}")
    try:
        from openminion.modules.storage.backends.postgres import (
            RecordStorePostgres,
        )
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"postgres backend unavailable: {exc}")

    schema_name = f"smbe_{uuid.uuid4().hex}"
    admin_engine = create_engine(postgres_url, future=True)
    admin_store = RecordStorePostgres(admin_engine)
    engine = create_engine(
        postgres_url,
        future=True,
        connect_args={"options": f"-csearch_path={schema_name}"},
    )
    store = RecordStorePostgres(engine)
    try:
        admin_store.execute_count(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"')
        yield BackendCase(name="postgres", store=store)
    finally:
        try:
            store.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            engine.dispose()
        except Exception:  # noqa: BLE001
            pass
        try:
            admin_store.execute_count(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        except Exception:  # noqa: BLE001
            pass
        try:
            admin_store.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            admin_engine.dispose()
        except Exception:  # noqa: BLE001
            pass


@pytest.fixture
def temp_omx_dir(tmp_path: Path) -> Path:

    target = tmp_path / "omx_bundle"
    target.mkdir(parents=True, exist_ok=True)
    return target


@dataclass
class RecordedTelemetryHook:
    events: list[dict[str, Any]] = field(default_factory=list)

    def record(self, event: dict[str, Any]) -> None:
        # Deep-copy guard so tests can mutate ``event`` after submitting.
        self.events.append(dict(event))

    def __call__(self, event: dict[str, Any]) -> None:  # pragma: no cover
        # Callable form for callers that treat the hook as a function.
        self.record(event)


@pytest.fixture
def mock_telemetry_hook() -> RecordedTelemetryHook:

    return RecordedTelemetryHook()


@pytest.fixture
def populated_record_store(
    tmp_path: Path,
) -> Callable[..., RecordStore]:

    opened: list[RecordStore] = []

    def _factory(*, rows: int = 0, table: str = "rows") -> RecordStore:
        db_path = tmp_path / f"populated_{uuid.uuid4().hex}.db"
        store = RecordStoreSQLite(db_path)
        opened.append(store)
        store.execute_count(
            f'CREATE TABLE IF NOT EXISTS "{table}" '
            f"(id INTEGER PRIMARY KEY AUTOINCREMENT, payload TEXT NOT NULL)"
        )
        if rows > 0:
            store.insert_many(
                table,
                [{"payload": f"row-{i}"} for i in range(rows)],
            )
        return store

    try:
        yield _factory
    finally:
        for store in opened:
            try:
                store.close()  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass
