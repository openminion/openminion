from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pytest

from openminion.modules.storage.runtime.context import (
    SCHEMA_DRIFT_WARNING_EVENT,
    build_runtime_storage,
)


def _pre_seed_drift(db_path: Path) -> None:
    from openminion.modules.storage.runtime.migrations import migrate_database

    migrate_database(db_path)
    with sqlite3.connect(str(db_path)) as connection:
        connection.execute(
            "ALTER TABLE sessions ADD COLUMN injected_drift_column TEXT NOT NULL DEFAULT ''"
        )
        connection.commit()


def test_build_runtime_storage_emits_typed_warning_on_drift(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(tmp_path / ".openminion"))

    db_path = tmp_path / ".openminion" / "state" / "drift.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _pre_seed_drift(db_path)

    caplog.set_level(
        logging.WARNING, logger="openminion.modules.storage.runtime.context"
    )

    ctx = build_runtime_storage(db_path)
    try:
        assert ctx.schema_drift_report is not None
        assert ctx.schema_drift_report.has_drift is True

        # The warning is a single typed log record carrying the event tag and
        # the structured report payload.
        matching = [
            record
            for record in caplog.records
            if record.levelno == logging.WARNING
            and getattr(record, "event", None) == SCHEMA_DRIFT_WARNING_EVENT
        ]
        assert len(matching) == 1, [
            (r.levelname, r.getMessage()) for r in caplog.records
        ]
        record = matching[0]
        payload = getattr(record, "schema_drift_report")
        assert payload["has_drift"] is True
        kinds = {f["kind"] for f in payload["findings"]}
        assert "extra_column" in kinds
    finally:
        ctx.close()


def test_build_runtime_storage_no_warning_on_clean_db(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(tmp_path / ".openminion"))

    db_path = tmp_path / ".openminion" / "state" / "clean.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    caplog.set_level(
        logging.WARNING, logger="openminion.modules.storage.runtime.context"
    )

    ctx = build_runtime_storage(db_path)
    try:
        assert ctx.schema_drift_report is not None
        assert ctx.schema_drift_report.has_drift is False
        drift_warnings = [
            record
            for record in caplog.records
            if getattr(record, "event", None) == SCHEMA_DRIFT_WARNING_EVENT
        ]
        assert drift_warnings == []
    finally:
        ctx.close()


def test_build_runtime_storage_drift_check_can_be_disabled(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(tmp_path / ".openminion"))

    db_path = tmp_path / ".openminion" / "state" / "disabled.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _pre_seed_drift(db_path)

    caplog.set_level(
        logging.WARNING, logger="openminion.modules.storage.runtime.context"
    )

    ctx = build_runtime_storage(db_path, check_schema_drift_on_startup=False)
    try:
        assert ctx.schema_drift_report is None
        drift_warnings = [
            record
            for record in caplog.records
            if getattr(record, "event", None) == SCHEMA_DRIFT_WARNING_EVENT
        ]
        assert drift_warnings == []
    finally:
        ctx.close()
