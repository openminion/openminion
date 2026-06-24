from __future__ import annotations

import logging
from pathlib import Path

from openminion.modules.controlplane.storage.sqlite import SQLiteControlPlaneStore
from openminion.services.runtime.lifecycle import LifecycleService
from openminion.services.security.policy import SecurityPolicyEngine
from tests.integration.test_unified_config_bootstrap import _close_runtime, _make_config


def _controlplane_db_path(tmp_path: Path) -> Path:
    return tmp_path / ".openminion" / "controlplane" / "cp.db"


def _seed_cross_owner_binding(db_path: Path) -> None:
    store = SQLiteControlPlaneStore(db_path)
    try:
        alice_session = store.resolve_session("telegram:alice", "telegram:100")
        store.bind_session("telegram:bob", "telegram:200", alice_session)
    finally:
        store.close()


def _build_runtime(tmp_path: Path):
    config = _make_config(tmp_path, mode="polling")
    lifecycle = LifecycleService.from_config(
        config,
        config_path=str(tmp_path / "config.json"),
        home_root=tmp_path,
        data_root=tmp_path / ".openminion",
        logger=logging.getLogger("test.controlplane.boot_binding_audit"),
    )
    runtime = lifecycle.build(
        security_policy=SecurityPolicyEngine(),
        load_tool_plugins=False,
    )
    return lifecycle, runtime


def test_boot_binding_audit_logs_no_warning_for_clean_store(
    tmp_path: Path,
    caplog,
) -> None:
    lifecycle, runtime = _build_runtime(tmp_path)
    try:
        assert "controlplane.security.binding.crossowner.detected" not in caplog.text
        runner = runtime.channels.get("telegram")
        assert getattr(runner, "_binding_warning_count", 0) == 0
    finally:
        _close_runtime(runtime)


def test_boot_binding_audit_warns_for_cross_owner_binding(
    tmp_path: Path,
    caplog,
) -> None:
    _seed_cross_owner_binding(_controlplane_db_path(tmp_path))

    with caplog.at_level(logging.WARNING):
        lifecycle, runtime = _build_runtime(tmp_path)
    try:
        assert "controlplane.security.binding.crossowner.detected" in caplog.text
        runner = runtime.channels.get("telegram")
        assert getattr(runner, "_binding_warning_count", 0) == 1
    finally:
        _close_runtime(runtime)


def test_boot_binding_audit_honors_row_cap(tmp_path: Path) -> None:
    store = SQLiteControlPlaneStore(_controlplane_db_path(tmp_path))
    try:
        for idx in range(1005):
            owner = f"telegram:owner:{idx}"
            owner_chat = f"telegram:chat-owner:{idx}"
            rebound_chat = f"telegram:chat-rebound:{idx}"
            session_id = store.resolve_session(owner, owner_chat)
            store.bind_session(f"telegram:other:{idx}", rebound_chat, session_id)
    finally:
        store.close()

    lifecycle, runtime = _build_runtime(tmp_path)
    try:
        runner = runtime.channels.get("telegram")
        assert getattr(runner, "_binding_scan_count", 0) == 1000
        assert getattr(runner, "_binding_warning_count", 0) <= 1000
    finally:
        _close_runtime(runtime)
