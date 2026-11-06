from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
from typing import Iterator
from urllib.parse import quote_plus
import uuid

import pytest

from openminion.modules.storage.engine import StorageEngineConfig
from openminion.modules.storage.backends.postgres import (
    RecordStorePostgres,
)


def schema_url(base_url: str, schema_name: str) -> str:
    separator = "&" if "?" in base_url else "?"
    return f"{base_url}{separator}options={quote_plus(f'-csearch_path={schema_name}')}"


def build_postgres_storage_config(
    *,
    tmp_path: Path,
    schema_name: str,
    sqlite_name: str,
) -> StorageEngineConfig:
    postgres_url = _postgres_url()
    return StorageEngineConfig(
        root_dir=tmp_path / "storage",
        sqlite_path=tmp_path / sqlite_name,
        fallback_root=tmp_path,
        record_backend="record.postgres",
        record_backend_options={"url": schema_url(postgres_url, schema_name)},
    )


@contextmanager
def open_postgres_record_store(
    prefix: str,
) -> Iterator[tuple[RecordStorePostgres, str]]:
    postgres_url = _postgres_url()
    sqlalchemy = pytest.importorskip("sqlalchemy")

    # Short enough for the modules' own schemas, <schema>__<module id> (#446).
    schema_name = f"{prefix}_{uuid.uuid4().hex[:12]}"
    admin_engine = sqlalchemy.create_engine(postgres_url, future=True)
    with admin_engine.begin() as conn:
        conn.execute(sqlalchemy.text(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"'))

    engine = sqlalchemy.create_engine(
        schema_url(postgres_url, schema_name), future=True
    )
    store = RecordStorePostgres(engine)
    try:
        yield store, schema_name
    finally:
        try:
            store.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            engine.dispose()
        except Exception:  # noqa: BLE001
            pass
        drop_schemas(admin_engine, schema_name)
        admin_engine.dispose()


def drop_schemas(admin_engine, schema_name: str) -> None:
    """Drop a test schema and the module schemas created under it (#446)."""
    from sqlalchemy import text

    with admin_engine.begin() as conn:
        names = conn.execute(
            text(
                "SELECT nspname FROM pg_namespace WHERE nspname = :s "
                "OR starts_with(nspname, :prefix)"
            ),
            {"s": schema_name, "prefix": f"{schema_name}__"},
        ).scalars()
        for name in list(names):
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{name}" CASCADE'))


def _postgres_url() -> str:
    postgres_url = str(os.getenv("OPENMINION_TEST_POSTGRES_URL", "")).strip()
    if not postgres_url:
        pytest.skip("OPENMINION_TEST_POSTGRES_URL is not set")
    return postgres_url
