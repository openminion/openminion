"""The configured PostgreSQL pool pair reaches the store as retained capacity.

``pool_size`` is what QueuePool keeps after release, not a pre-created floor, and
``pool_max_overflow`` is the room above it, so ``min``/``max`` is a retained/total
pair. The producing owner is :meth:`StorageConfig.record_backend_options`; the
storage-engine overlay only decides precedence between that pair and the explicit
``pg_pool_size``/``pg_pool_max_overflow`` settings.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from openminion.base.config.core import ConfigValidationError, StorageConfig
from openminion.base.config.io import load_config
from openminion.modules.storage.backends import registry
from openminion.modules.storage.engine import (
    NoopStorageTelemetryHook,
    StorageEngineConfig,
    _record_backend_options,
)

URL = "postgresql://user:***@db.example/openminion"


def _engine_config(
    storage: StorageConfig | None = None,
    **kwargs: Any,
) -> StorageEngineConfig:
    pair = storage.record_backend_options() if storage is not None else {"url": URL}
    return StorageEngineConfig(
        root_dir=Path("."),
        sqlite_path=Path(":memory:"),
        fallback_root=Path("."),
        record_backend="record.postgres",
        record_backend_options=dict(pair),
        **kwargs,
    )


def _captured(config: StorageEngineConfig) -> dict[str, object]:
    """What the record factory forwards to the store for this engine config."""
    from openminion.modules.storage.backends import postgres as pg_backend

    captured: dict[str, object] = {}

    class _FakeStore:
        def __init__(self, url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)

    original = pg_backend.RecordStorePostgres
    pg_backend.RecordStorePostgres = _FakeStore
    try:
        options = _record_backend_options(
            config, sqlite=Path(":memory:"), telemetry_hook=NoopStorageTelemetryHook()
        )
        registry._postgres_record_factory(options)
    finally:
        pg_backend.RecordStorePostgres = original
    return captured


def test_the_configured_pair_reaches_the_factory_as_size_and_overflow() -> None:
    storage = StorageConfig(
        backend="postgres", postgres_url=URL, postgres_pool_min=2, postgres_pool_max=9
    )

    captured = _captured(_engine_config(storage, pg_pool_size=None))

    assert captured["pool_size"] == 2
    assert captured["pool_max_overflow"] == 7


def test_the_preserved_defaults_reach_the_factory() -> None:
    storage = StorageConfig(backend="postgres", postgres_url=URL)

    captured = _captured(_engine_config(storage))

    assert captured["pool_size"] == 5
    assert captured["pool_max_overflow"] == 10


def test_a_partial_explicit_overflow_overrides_only_the_overflow() -> None:
    storage = StorageConfig(
        backend="postgres", postgres_url=URL, postgres_pool_min=2, postgres_pool_max=9
    )

    captured = _captured(_engine_config(storage, pg_pool_max_overflow=3))

    assert captured["pool_size"] == 2
    assert captured["pool_max_overflow"] == 3


def test_an_explicit_pair_overrides_the_configured_pair() -> None:
    storage = StorageConfig(
        backend="postgres", postgres_url=URL, postgres_pool_min=2, postgres_pool_max=9
    )

    captured = _captured(
        _engine_config(storage, pg_pool_size=7, pg_pool_max_overflow=8)
    )

    assert captured["pool_size"] == 7
    assert captured["pool_max_overflow"] == 8


def test_direct_engine_options_still_reach_the_factory_without_the_pair() -> None:
    captured = _captured(_engine_config(None, pg_pool_size=6, pg_pool_max_overflow=2))

    assert captured["pool_size"] == 6
    assert captured["pool_max_overflow"] == 2


def test_sqlite_emits_no_pool_options_or_overrides() -> None:
    """SQLite keeps its unused-option behaviour: nothing is produced or overlaid."""
    storage = StorageConfig()
    assert storage.record_backend_options() == {}

    options = _record_backend_options(
        StorageEngineConfig(
            root_dir=Path("."),
            sqlite_path=Path(":memory:"),
            fallback_root=Path("."),
            record_backend="record.sqlite",
            record_backend_options={"sqlite_path": Path(":memory:")},
        ),
        sqlite=Path(":memory:"),
        telemetry_hook=NoopStorageTelemetryHook(),
    )

    assert "pool_size" not in options
    assert "pool_max_overflow" not in options


@pytest.mark.parametrize(("minimum", "maximum"), [(0, 5), (-1, 5), (9, 2)])
def test_an_invalid_postgres_pair_is_refused_by_the_config_owner(
    minimum: int, maximum: int
) -> None:
    """The range belongs to the settings owner, not to SQLAlchemy's pool constructor."""
    storage = StorageConfig(
        backend="postgres",
        postgres_url=URL,
        postgres_pool_min=minimum,
        postgres_pool_max=maximum,
    )

    with pytest.raises(ConfigValidationError, match="postgres_pool_min/max"):
        storage.record_backend_options()


def test_sqlite_keeps_an_unused_pair_unvalidated() -> None:
    """A stored pair is inert for SQLite and must not fail a SQLite configuration."""
    storage = StorageConfig(backend="sqlite", postgres_pool_min=9, postgres_pool_max=2)

    assert storage.record_backend_options() == {}


def _storage_from_config_file(
    tmp_path: Path, monkeypatch: Any, storage: dict[str, Any]
) -> StorageConfig:
    """Load a real config file, with the storage env overrides out of the way."""
    for name in (
        "OPENMINION_STORAGE_BACKEND",
        "OPENMINION_STORAGE_POSTGRES_URL",
        "OPENMINION_STORAGE_POSTGRES_POOL_MIN",
        "OPENMINION_STORAGE_POSTGRES_POOL_MAX",
    ):
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"storage": storage}), encoding="utf-8")
    return load_config(config_path=str(path), home_root=tmp_path).storage


def test_the_config_file_pair_reaches_the_factory(
    tmp_path: Path, monkeypatch: Any
) -> None:
    storage = _storage_from_config_file(
        tmp_path,
        monkeypatch,
        {
            "backend": "postgres",
            "postgres_url": URL,
            "postgres_pool_min": 2,
            "postgres_pool_max": 9,
        },
    )

    captured = _captured(_engine_config(storage))

    assert captured["pool_size"] == 2
    assert captured["pool_max_overflow"] == 7


def test_an_omitted_config_file_pair_keeps_the_capacity(
    tmp_path: Path, monkeypatch: Any
) -> None:
    storage = _storage_from_config_file(
        tmp_path, monkeypatch, {"backend": "postgres", "postgres_url": URL}
    )

    captured = _captured(_engine_config(storage))

    assert captured["pool_size"] == 5
    assert captured["pool_max_overflow"] == 10


def test_an_explicit_override_does_not_bypass_the_range_check() -> None:
    storage = StorageConfig(
        backend="postgres", postgres_url=URL, postgres_pool_min=9, postgres_pool_max=2
    )

    with pytest.raises(ConfigValidationError, match="postgres_pool_min/max"):
        _engine_config(storage, pg_pool_size=7)


def test_an_equal_pair_reaches_the_factory_as_zero_overflow() -> None:
    storage = StorageConfig(
        backend="postgres", postgres_url=URL, postgres_pool_min=3, postgres_pool_max=3
    )

    captured = _captured(_engine_config(storage))

    assert captured["pool_size"] == 3
    assert captured["pool_max_overflow"] == 0


def test_unrelated_timeout_and_recycle_precedence_is_unchanged() -> None:
    record = {"url": URL, "pool_timeout_seconds": 41, "pool_recycle_seconds": 120}
    config = StorageEngineConfig(
        root_dir=Path("."),
        sqlite_path=Path(":memory:"),
        fallback_root=Path("."),
        record_backend="record.postgres",
        record_backend_options=dict(record),
        pg_pool_timeout_seconds=7,
        pg_pool_recycle_seconds=60,
    )

    captured = _captured(config)

    assert captured["pool_timeout_seconds"] == 41
    assert captured["pool_recycle_seconds"] == 120
