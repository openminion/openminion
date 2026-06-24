from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from openminion.modules.storage.engine import StorageEngineConfig


def _base_kwargs(tmp_path: Path) -> dict[str, Path]:
    return {
        "root_dir": tmp_path / "blob",
        "sqlite_path": tmp_path / "storage.db",
        "fallback_root": tmp_path / "fallback",
    }


def test_pool_fields_default_to_none(tmp_path: Path) -> None:
    config = StorageEngineConfig(**_base_kwargs(tmp_path))

    assert config.pg_pool_recycle_seconds is None
    assert config.pg_pool_size is None
    assert config.pg_pool_max_overflow is None
    assert config.pg_pool_timeout_seconds is None


def test_pool_fields_accept_valid_values(tmp_path: Path) -> None:
    config = StorageEngineConfig(
        **_base_kwargs(tmp_path),
        pg_pool_recycle_seconds=300,
        pg_pool_size=10,
        pg_pool_max_overflow=5,
        pg_pool_timeout_seconds=30.0,
    )

    assert config.pg_pool_recycle_seconds == 300
    assert config.pg_pool_size == 10
    assert config.pg_pool_max_overflow == 5
    assert config.pg_pool_timeout_seconds == pytest.approx(30.0)


def test_pool_fields_admit_zero(tmp_path: Path) -> None:
    # SEPRC-Q2 (locked): zero is a meaningful operator choice
    # (SQLAlchemy treats pool_recycle=0 as "never recycle").
    config = StorageEngineConfig(
        **_base_kwargs(tmp_path),
        pg_pool_recycle_seconds=0,
        pg_pool_size=0,
        pg_pool_max_overflow=0,
        pg_pool_timeout_seconds=0.0,
    )

    assert config.pg_pool_recycle_seconds == 0
    assert config.pg_pool_size == 0
    assert config.pg_pool_max_overflow == 0
    assert config.pg_pool_timeout_seconds == pytest.approx(0.0)


def test_negative_pg_pool_recycle_seconds_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="pg_pool_recycle_seconds"):
        StorageEngineConfig(**_base_kwargs(tmp_path), pg_pool_recycle_seconds=-1)


def test_negative_pg_pool_size_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="pg_pool_size"):
        StorageEngineConfig(**_base_kwargs(tmp_path), pg_pool_size=-1)


def test_negative_pg_pool_max_overflow_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="pg_pool_max_overflow"):
        StorageEngineConfig(**_base_kwargs(tmp_path), pg_pool_max_overflow=-1)


def test_negative_pg_pool_timeout_seconds_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="pg_pool_timeout_seconds"):
        StorageEngineConfig(**_base_kwargs(tmp_path), pg_pool_timeout_seconds=-0.5)


def test_existing_caller_signature_unaffected(tmp_path: Path) -> None:
    # Mirrors the pre-SEPRC ``StorageEngine.from_paths`` construction path:
    # callers that do not supply any pool-tuning fields must still produce a
    # valid frozen dataclass instance with all new fields defaulted to None.
    config = StorageEngineConfig(
        root_dir=tmp_path / "blob",
        sqlite_path=tmp_path / "storage.db",
        fallback_root=tmp_path / "fallback",
        wal=True,
        synchronous="NORMAL",
        busy_timeout_ms=5000,
        autocheckpoint_pages=1000,
        default_namespace=None,
        record_backend="record.sqlite",
        blob_backend="blob.fs",
        vector_backend=None,
        record_backend_options={},
        blob_backend_options={},
        vector_backend_options={},
    )

    assert config.pg_pool_recycle_seconds is None
    assert config.pg_pool_size is None
    assert config.pg_pool_max_overflow is None
    assert config.pg_pool_timeout_seconds is None
    # Pre-SEPRC fields preserved verbatim.
    assert config.wal is True
    assert config.synchronous == "NORMAL"
    assert config.busy_timeout_ms == 5000
    assert config.autocheckpoint_pages == 1000


def test_frozen_dataclass_invariant_preserved(tmp_path: Path) -> None:
    config = StorageEngineConfig(**_base_kwargs(tmp_path))

    assert dataclasses.is_dataclass(StorageEngineConfig)

    with pytest.raises(dataclasses.FrozenInstanceError):
        config.pg_pool_recycle_seconds = 60  # type: ignore[misc]

    with pytest.raises(dataclasses.FrozenInstanceError):
        config.pg_pool_size = 5  # type: ignore[misc]
