from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from openminion.modules.controlplane.storage import SQLiteControlPlaneStore
from openminion.modules.controlplane.wizard.store import (
    _STORE_REGISTRY,
    InMemoryWizardStore,
    SqliteWizardStore,
    WizardState,
    get_wizard_store,
)
from openminion.services.runtime.lifecycle import LifecycleService
from openminion.services.security.policy import SecurityPolicyEngine

from tests.integration.test_unified_config_bootstrap import (
    _close_runtime,
    _make_config,
)


@pytest.fixture(autouse=True)
def _hermetic_wizard_registry():
    saved = dict(_STORE_REGISTRY)
    _STORE_REGISTRY.clear()
    try:
        yield
    finally:
        _STORE_REGISTRY.clear()
        _STORE_REGISTRY.update(saved)


def _build_runtime(tmp_path: Path):
    config = _make_config(tmp_path, mode="polling")
    lifecycle = LifecycleService.from_config(
        config,
        config_path=str(tmp_path / "config.json"),
        home_root=tmp_path,
        data_root=tmp_path / ".openminion",
    )
    runtime = lifecycle.build(
        security_policy=SecurityPolicyEngine(),
        load_tool_plugins=False,
    )
    return runtime


def test_lifecycle_registers_sqlite_wizard_store(tmp_path: Path) -> None:
    runtime = _build_runtime(tmp_path)
    try:
        runner = runtime.channels.get("telegram")
        assert runner is not None
        assert isinstance(runner._store, SQLiteControlPlaneStore)

        assert "sqlite" in _STORE_REGISTRY
        wizard_store = _STORE_REGISTRY["sqlite"]
        assert isinstance(wizard_store, SqliteWizardStore)

        wizard_db = tmp_path / ".openminion" / "controlplane" / "wizard.db"
        assert wizard_db.exists() or wizard_db.parent.exists()
    finally:
        _close_runtime(runtime)


def test_get_wizard_store_default_returns_sqlite_when_registered(
    tmp_path: Path,
) -> None:
    runtime = _build_runtime(tmp_path)
    try:
        loop = asyncio.new_event_loop()
        try:
            store = loop.run_until_complete(get_wizard_store())
        finally:
            loop.close()
        assert isinstance(store, SqliteWizardStore)
        assert not isinstance(store, InMemoryWizardStore)
    finally:
        _close_runtime(runtime)


def test_wizard_session_persists_across_runtime_rebuild(tmp_path: Path) -> None:
    runtime = _build_runtime(tmp_path)
    try:
        loop = asyncio.new_event_loop()
        try:
            store = loop.run_until_complete(get_wizard_store())
            assert isinstance(store, SqliteWizardStore)
            session = loop.run_until_complete(
                store.create_session(
                    command_name="test_wizard",
                    step=1,
                    total_steps=3,
                    user_key="user-42",
                    chat_key="chat-7",
                    session_id="sess-99",
                )
            )
            wizard_id = session.wizard_id
        finally:
            loop.close()
    finally:
        _close_runtime(runtime)

    _STORE_REGISTRY.clear()

    runtime2 = _build_runtime(tmp_path)
    try:
        loop = asyncio.new_event_loop()
        try:
            store2 = loop.run_until_complete(get_wizard_store())
            assert isinstance(store2, SqliteWizardStore)
            session2 = loop.run_until_complete(store2._get_raw_session(wizard_id))
            assert session2 is not None
            assert session2.wizard_id == wizard_id
            assert session2.command_name == "test_wizard"
            assert session2.user_key == "user-42"
            assert session2.state == WizardState.ACTIVE
        finally:
            loop.close()
    finally:
        _close_runtime(runtime2)
