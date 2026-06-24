from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path

import pytest

from openminion.modules.identity.models import AgentProfile
from openminion.modules.identity.storage import build_identity_store
from openminion.modules.identity.storage.store import (
    PostgresIdentityStore,
    SQLiteIdentityStore,
)
from openminion.modules.storage.engine import StorageEngineConfig
from tests.storage.postgres_test_utils import (
    build_postgres_storage_config,
    open_postgres_record_store,
)


def _backend_params():
    return [
        pytest.param("sqlite", id="sqlite"),
        pytest.param("postgres", marks=pytest.mark.postgres, id="postgres"),
    ]


def _profile(agent_id: str = "router-agent") -> AgentProfile:
    return AgentProfile.model_validate(
        {
            "agent_id": agent_id,
            "display_name": "Router Agent",
            "profile_revision": 3,
            "role": {"mission": "Route safely"},
            "personality": {"tone": "direct"},
            "risk": {"risk_level": "medium"},
            "tool_posture": {"tool_use": "restricted"},
        }
    )


@pytest.fixture(params=_backend_params())
def identity_store_case(request: pytest.FixtureRequest, tmp_path: Path):
    backend = str(request.param)
    with ExitStack() as stack:
        if backend == "sqlite":
            store = SQLiteIdentityStore(tmp_path / "identity.db")
        else:
            record_store, _schema_name = stack.enter_context(
                open_postgres_record_store("mpt1_identity")
            )
            store = PostgresIdentityStore(record_store=record_store)
        stack.callback(store.close)
        yield backend, store


def test_identity_store_profile_and_cache_round_trip(identity_store_case) -> None:
    _backend, store = identity_store_case
    profile = _profile()
    store.upsert_profile(profile, "v1")

    loaded = store.get_profile("router-agent")
    assert loaded is not None
    assert loaded.profile.agent_id == "router-agent"
    assert loaded.profile_version == "v1"
    assert [item.agent_id for item in store.list_profiles()] == ["router-agent"]

    store.update_profile_version("router-agent", "v2")
    updated = store.get_profile("router-agent")
    assert updated is not None
    assert updated.profile_version == "v2"

    store.upsert_cached_snippet(
        cache_key="router-agent|respond",
        snippet_text="identity text",
        used_tokens=7,
        used_chars=13,
        sections={"role": "Route safely"},
        included_fields=["role"],
        omitted_fields=["risk"],
        warnings=["none"],
    )
    cached = store.get_cached_snippet("router-agent|respond")
    assert cached is not None
    assert cached.sections == {"role": "Route safely"}
    assert cached.included_fields == ["role"]

    store.clear_cache("router-agent")
    assert store.get_cached_snippet("router-agent|respond") is None

    store.delete_profile("router-agent")
    assert store.get_profile("router-agent") is None


def test_build_identity_store_returns_sqlite_store(tmp_path: Path) -> None:
    store = build_identity_store(
        config=StorageEngineConfig(
            root_dir=tmp_path / "storage",
            sqlite_path=tmp_path / "identity.db",
            fallback_root=tmp_path,
            record_backend="record.sqlite",
        ),
        database_path=tmp_path / "identity.db",
    )
    try:
        assert isinstance(store, SQLiteIdentityStore)
    finally:
        store.close()


@pytest.mark.postgres
def test_build_identity_store_returns_postgres_store(tmp_path: Path) -> None:
    with open_postgres_record_store("mpt1_identity_factory") as (
        _record_store,
        schema_name,
    ):
        store = build_identity_store(
            config=build_postgres_storage_config(
                tmp_path=tmp_path,
                schema_name=schema_name,
                sqlite_name="identity.db",
            ),
            database_path=tmp_path / "identity.db",
        )
        try:
            assert isinstance(store, PostgresIdentityStore)
        finally:
            store.close()
