from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from openminion.modules.secret.service import SecretNotFoundError, SecretService
from openminion.modules.secret.storage.store import PostgresSecretStore

pytestmark = pytest.mark.postgres


@pytest.fixture
def master_key() -> str:
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode()


def _open_postgres_secret_store():
    postgres_url = str(os.getenv("OPENMINION_TEST_POSTGRES_URL", "")).strip()
    if not postgres_url:
        pytest.skip("OPENMINION_TEST_POSTGRES_URL is not set")

    sqlalchemy = pytest.importorskip("sqlalchemy")
    schema_name = f"smbe_secret_{uuid.uuid4().hex}"
    admin_engine = sqlalchemy.create_engine(postgres_url, future=True)
    with admin_engine.begin() as conn:
        conn.execute(sqlalchemy.text(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"'))
    engine = sqlalchemy.create_engine(
        postgres_url,
        future=True,
        connect_args={"options": f"-csearch_path={schema_name}"},
    )
    from openminion.modules.storage.backends.postgres import (
        RecordStorePostgres,
    )

    record_store = RecordStorePostgres(engine)
    store = PostgresSecretStore(record_store=record_store)
    return sqlalchemy, admin_engine, engine, schema_name, store


@pytest.mark.postgres
def test_postgres_secret_store_crud_round_trip() -> None:
    sqlalchemy, admin_engine, engine, schema_name, store = _open_postgres_secret_store()
    try:
        store.upsert(
            key="token",
            namespace="default",
            value="cipher1",
            created_at=1.0,
            updated_at=1.0,
        )
        assert store.fetch_value(key="token", namespace="default") == "cipher1"

        store.upsert(
            key="token",
            namespace="default",
            value="cipher2",
            created_at=1.0,
            updated_at=2.0,
        )
        assert store.fetch_value(key="token", namespace="default") == "cipher2"
        assert store.list_keys(namespace="default") == ["token"]

        store.delete(key="token", namespace="default")
        assert store.fetch_value(key="token", namespace="default") is None
    finally:
        try:
            store.close()
        finally:
            engine.dispose()
            with admin_engine.begin() as conn:
                conn.execute(
                    sqlalchemy.text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
                )
            admin_engine.dispose()


@pytest.mark.postgres
def test_secret_service_supports_external_postgres_record_store(master_key) -> None:
    sqlalchemy, admin_engine, engine, schema_name, store = _open_postgres_secret_store()
    service = SecretService(master_key=master_key, record_store=store._record_store)
    try:
        asyncio.run(service.set_secret("api", "value1"))
        assert asyncio.run(service.get_secret("api")) == "value1"
        asyncio.run(service.delete_secret("api"))
        with pytest.raises(SecretNotFoundError):
            asyncio.run(service.get_secret("api"))
    finally:
        asyncio.run(service.close())
        engine.dispose()
        with admin_engine.begin() as conn:
            conn.execute(
                sqlalchemy.text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
            )
        admin_engine.dispose()
