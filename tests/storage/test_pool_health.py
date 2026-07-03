from __future__ import annotations

import os
from pathlib import Path

import pytest

from openminion.modules.storage.record_store import RecordStoreSQLite

pytestmark = pytest.mark.postgres


def test_record_store_base_pool_health_returns_none() -> None:
    store = RecordStoreSQLite(":memory:")
    try:
        assert store.pool_health() is None
    finally:
        store.close()


def test_sqlite_healthcheck_omits_pool_key(tmp_path: Path) -> None:
    store = RecordStoreSQLite(tmp_path / "p.db")
    try:
        health = store.healthcheck()
        assert health["ok"] is True
        assert health["error"] is None
        assert "pool" not in health
    finally:
        store.close()


def _require_postgres() -> str:
    postgres_url = str(os.getenv("OPENMINION_TEST_POSTGRES_URL", "")).strip()
    if not postgres_url:
        pytest.skip("OPENMINION_TEST_POSTGRES_URL is not set")
    pytest.importorskip("sqlalchemy")
    return postgres_url


@pytest.mark.postgres
def test_postgres_pool_health_returns_stats_dict() -> None:
    postgres_url = _require_postgres()
    from openminion.modules.storage.backends.postgres import (
        RecordStorePostgres,
    )

    store = RecordStorePostgres(postgres_url)
    try:
        store.healthcheck()
        stats = store.pool_health()
        assert stats is not None
        for key in (
            "pool_size",
            "checked_out",
            "overflow",
            "oldest_connection_age_seconds",
        ):
            assert key in stats
    finally:
        store.close()


@pytest.mark.postgres
def test_postgres_healthcheck_includes_pool_subdict() -> None:
    postgres_url = _require_postgres()
    from openminion.modules.storage.backends.postgres import (
        RecordStorePostgres,
    )

    store = RecordStorePostgres(postgres_url)
    try:
        health = store.healthcheck()
        # Preserve existing contract:
        assert "ok" in health
        assert "error" in health
        assert health["ok"] is True
        # Additive surface:
        assert "pool" in health
        assert isinstance(health["pool"], dict)
    finally:
        store.close()


@pytest.mark.postgres
def test_postgres_oldest_connection_age_grows_after_use() -> None:
    postgres_url = _require_postgres()
    from openminion.modules.storage.backends.postgres import (
        RecordStorePostgres,
    )

    store = RecordStorePostgres(postgres_url)
    try:
        store.healthcheck()
        stats = store.pool_health()
        assert stats is not None
        age = stats["oldest_connection_age_seconds"]
        assert age is None or (isinstance(age, (int, float)) and age >= 0.0)
    finally:
        store.close()
