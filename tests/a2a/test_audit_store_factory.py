from __future__ import annotations

from pathlib import Path

import pytest

from openminion.base.config import OpenMinionConfig
from openminion.modules.a2a.config import from_base_config, load_config
from openminion.modules.a2a.storage import (
    PostgresAuditStore,
    SQLiteAuditStore,
    build_a2a_audit_store,
)
from openminion.modules.storage.engine import StorageEngineConfig
from tests.storage.postgres_test_utils import (
    build_postgres_storage_config,
    open_postgres_record_store,
)


def test_build_a2a_audit_store_returns_sqlite_store(tmp_path: Path) -> None:
    store = build_a2a_audit_store(
        config=StorageEngineConfig(
            root_dir=tmp_path / "storage",
            sqlite_path=tmp_path / "audit.db",
            fallback_root=tmp_path,
            record_backend="record.sqlite",
        ),
        audit_root=tmp_path / "audit",
        retention_days=7,
    )
    try:
        assert isinstance(store, SQLiteAuditStore)
        assert store.capture_payloads is False
        assert store.retention_days == 7
        assert store.archive_retention_days == 0
    finally:
        store.close()


@pytest.mark.postgres
def test_build_a2a_audit_store_returns_postgres_store(tmp_path: Path) -> None:
    with open_postgres_record_store("sfc_a2a_audit_factory") as (_store, schema_name):
        store = build_a2a_audit_store(
            config=build_postgres_storage_config(
                tmp_path=tmp_path,
                schema_name=schema_name,
                sqlite_name="audit.db",
            ),
            audit_root=tmp_path / "audit",
            retention_days=7,
        )
        try:
            assert isinstance(store, PostgresAuditStore)
        finally:
            store.close()


def test_a2a_audit_config_defaults_and_overrides() -> None:
    defaults = load_config({})
    assert defaults.storage.audit.capture_payloads is False
    assert defaults.storage.audit.retention_days == 14
    assert defaults.storage.audit.archive_retention_days == 0

    configured = load_config(
        {
            "storage": {
                "audit": {
                    "capture_payloads": True,
                    "retention_days": 30,
                    "archive_retention_days": 365,
                }
            }
        }
    )
    assert configured.storage.audit.capture_payloads is True
    assert configured.storage.audit.retention_days == 30
    assert configured.storage.audit.archive_retention_days == 365


@pytest.mark.parametrize(
    ("retention_days", "archive_retention_days"),
    [(0, 0), (36_501, 0), (14, -1), (14, 13), (14, 36_501)],
)
def test_a2a_audit_config_rejects_invalid_retention(
    retention_days: int,
    archive_retention_days: int,
) -> None:
    with pytest.raises(ValueError):
        load_config(
            {
                "storage": {
                    "audit": {
                        "retention_days": retention_days,
                        "archive_retention_days": archive_retention_days,
                    }
                }
            }
        )


def test_openminion_a2a_module_settings_flow_into_a2a_config(tmp_path: Path) -> None:
    base = OpenMinionConfig.from_dict(
        {
            "a2a": {
                "storage": {
                    "audit": {
                        "capture_payloads": True,
                        "retention_days": 21,
                        "archive_retention_days": 90,
                    }
                }
            }
        }
    )

    configured = from_base_config(
        base_config=base,
        home_root=tmp_path / "home",
        data_root=tmp_path / "data",
    )

    assert configured.storage.audit.capture_payloads is True
    assert configured.storage.audit.retention_days == 21
    assert configured.storage.audit.archive_retention_days == 90


def test_explicit_a2a_config_is_authoritative(tmp_path: Path) -> None:
    base = OpenMinionConfig.from_dict(
        {
            "a2a": {
                "storage": {
                    "audit": {
                        "capture_payloads": False,
                        "retention_days": 7,
                        "archive_retention_days": 0,
                    }
                }
            },
        }
    )

    configured = from_base_config(
        base_config=base,
        home_root=tmp_path / "home",
        data_root=tmp_path / "data",
    )

    assert configured.storage.audit.capture_payloads is False
    assert configured.storage.audit.retention_days == 7
    assert configured.storage.audit.archive_retention_days == 0


@pytest.mark.parametrize(
    "audit",
    [
        {"retention_days": 0},
        {"retention_days": 30, "archive_retention_days": 29},
        {"archive_retention_days": 36_501},
    ],
)
def test_openminion_a2a_module_config_rejects_invalid_audit_retention(
    tmp_path: Path,
    audit: dict[str, int],
) -> None:
    base = OpenMinionConfig.from_dict({"a2a": {"storage": {"audit": audit}}})
    with pytest.raises(ValueError):
        from_base_config(
            base_config=base,
            home_root=tmp_path / "home",
            data_root=tmp_path / "data",
        )
