from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path

import pytest

from openminion.modules.policy.models import PolicyGrantInput
from openminion.modules.policy.storage import build_policy_store
from openminion.modules.policy.storage.store import (
    PostgresPolicyStore,
    SQLitePolicyStore,
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


@pytest.fixture(params=_backend_params())
def policy_store_case(request: pytest.FixtureRequest, tmp_path: Path):
    backend = str(request.param)
    with ExitStack() as stack:
        if backend == "sqlite":
            store = SQLitePolicyStore(tmp_path / "policy.db")
            stack.callback(store.close)
        else:
            record_store, _schema_name = stack.enter_context(
                open_postgres_record_store("mpt2_policy")
            )
            store = PostgresPolicyStore(record_store=record_store)
        yield backend, store


def test_policy_store_round_trip(policy_store_case) -> None:
    _backend, store = policy_store_case

    grant_id = store.create_grant(
        PolicyGrantInput(
            subject_id="user:1",
            effect="allow",
            tool="weather",
            method="current",
            target_json={"location": "SF"},
            risk_floor="low",
            duration_type="once",
            expires_at=None,
            session_id="sess-1",
            invocation_hash="inv-1",
            max_uses=1,
            reason="test",
            created_trace_id="trace-1",
        )
    )

    fetched = store.get_grant(grant_id)
    assert fetched is not None
    assert fetched.target_json == {"location": "SF"}
    assert store.list_grants(subject_id="user:1")[0].grant_id == grant_id

    consumed = store.consume_grant_use(grant_id)
    assert consumed is not None
    assert consumed.uses_count == 1
    assert consumed.revoked_at is not None

    second_grant = store.create_grant(
        PolicyGrantInput(
            subject_id="user:2",
            effect="allow",
            tool="search",
            method="query",
            target_json={"q": "news"},
            risk_floor="low",
            duration_type="until",
            expires_at="2000-01-01T00:00:00+00:00",
            session_id=None,
            invocation_hash=None,
            max_uses=None,
            reason=None,
            created_trace_id=None,
        )
    )
    assert store.cleanup_expired() == 1
    expired = store.get_grant(second_grant)
    assert expired is not None
    assert expired.revoked_at is not None

    decision_id = store.log_decision(
        trace_id="trace-1",
        session_id="sess-1",
        agent_id="agent-1",
        invocation_id="inv-1",
        tool="weather",
        method="current",
        decision="allow",
        matched_grant_id=grant_id,
        reason_code="grant_match",
        risk_spec={"risk_level": "low"},
    )
    decisions = store.list_decisions(limit=5)
    assert decisions[0]["decision_id"] == decision_id
    assert decisions[0]["risk_spec_json"] == {"risk_level": "low"}

    store.set_setting("mode", "enforce")
    assert store.get_setting("mode") == "enforce"
    assert store.revoke_grant(grant_id) is False


def test_build_policy_store_returns_sqlite_store(tmp_path: Path) -> None:
    store = build_policy_store(
        config=StorageEngineConfig(
            root_dir=tmp_path / "storage",
            sqlite_path=tmp_path / "policy.db",
            fallback_root=tmp_path,
            record_backend="record.sqlite",
        ),
        database_path=tmp_path / "policy.db",
    )
    try:
        assert isinstance(store, SQLitePolicyStore)
    finally:
        store.close()


@pytest.mark.postgres
def test_build_policy_store_returns_postgres_store(tmp_path: Path) -> None:
    with open_postgres_record_store("mpt2_policy_factory") as (
        _record_store,
        schema_name,
    ):
        store = build_policy_store(
            config=build_postgres_storage_config(
                tmp_path=tmp_path,
                schema_name=schema_name,
                sqlite_name="policy.db",
            ),
            database_path=tmp_path / "policy.db",
        )
        try:
            assert isinstance(store, PostgresPolicyStore)
        finally:
            store.close()
