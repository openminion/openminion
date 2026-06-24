from __future__ import annotations

from pathlib import Path

import pytest

from openminion.modules.session.interfaces import (
    SESSION_INTERFACE_VERSION,
    SESSION_REPOSITORY_INTERFACE_VERSION,
    ensure_cron_repository_compatibility,
    ensure_session_component_compatibility,
)
from openminion.modules.session.runtime.session_client import SessctlSessionClient
from openminion.modules.session.storage.sqlite_store import SQLiteSessionStore


def test_session_store_and_context_client_contracts(tmp_path: Path) -> None:
    store = SQLiteSessionStore(tmp_path / "sessions.db")
    try:
        sid = store.create_session()
        client = SessctlSessionClient(store)

        assert store.contract_version == SESSION_INTERFACE_VERSION
        assert client.contract_version == SESSION_INTERFACE_VERSION

        ensure_session_component_compatibility(store, component_type="store")
        ensure_session_component_compatibility(client, component_type="context_client")

        slice_payload = client.get_slice(
            session_id=sid,
            purpose="act",
            limits={"max_turns": 4, "max_tool_events": 2},
        )
        assert getattr(slice_payload, "session_id", "") == sid
    finally:
        store.close()


def test_validator_rejects_incompatible_component() -> None:
    class _BrokenClient:
        contract_version = "v1"

    with pytest.raises(TypeError):
        ensure_session_component_compatibility(
            _BrokenClient(), component_type="context_client"
        )


def test_cron_repository_contract_compatibility(tmp_path: Path) -> None:
    store = SQLiteSessionStore(tmp_path / "sessions.db")
    try:
        assert store.contract_version == SESSION_REPOSITORY_INTERFACE_VERSION
        ensure_cron_repository_compatibility(store)
    finally:
        store.close()


def test_cron_repository_contract_rejects_missing_member() -> None:
    class _BrokenCronRepo:
        repository_contract_version = SESSION_REPOSITORY_INTERFACE_VERSION

        def get_cron_job(self, job_id: str):
            return None

    with pytest.raises(TypeError, match="missing members"):
        ensure_cron_repository_compatibility(_BrokenCronRepo())
