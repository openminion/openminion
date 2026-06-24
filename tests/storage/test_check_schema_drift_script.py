from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from io import StringIO
from pathlib import Path
from types import ModuleType

import pytest

from openminion.modules.storage.runtime.migrations import migrate_database


SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "ci" / "check_schema_drift.py"
)


@pytest.fixture(scope="module")
def script_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "openminion_check_schema_drift_script",
        SCRIPT_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _capture_stdout(monkeypatch: pytest.MonkeyPatch) -> StringIO:
    buf = StringIO()
    monkeypatch.setattr(sys, "stdout", buf)
    return buf


def test_self_check_returns_zero_when_baseline_matches(
    script_module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    buf = _capture_stdout(monkeypatch)
    exit_code = script_module.run(["--self-check"])
    assert exit_code == script_module.EXIT_OK
    payload = json.loads(buf.getvalue())
    assert payload["has_drift"] is False


def test_db_path_must_exist(
    script_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing = tmp_path / "nope.db"
    exit_code = script_module.run(["--db", str(missing), "--quiet"])
    assert exit_code == script_module.EXIT_USAGE
    captured = capsys.readouterr()
    assert "does not exist" in captured.err


def test_green_path_returns_zero_on_freshly_migrated_db(
    script_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "live.db"
    migrate_database(db_path)
    buf = _capture_stdout(monkeypatch)
    exit_code = script_module.run(["--db", str(db_path)])
    assert exit_code == script_module.EXIT_OK
    payload = json.loads(buf.getvalue())
    assert payload["has_drift"] is False


def test_red_path_returns_one_when_table_missing(
    script_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "live.db"
    migrate_database(db_path)
    with sqlite3.connect(str(db_path)) as connection:
        connection.execute("DROP TABLE memory_vectors")
        connection.commit()

    buf = _capture_stdout(monkeypatch)
    exit_code = script_module.run(["--db", str(db_path)])
    assert exit_code == script_module.EXIT_DRIFT
    payload = json.loads(buf.getvalue())
    assert payload["has_drift"] is True
    kinds = {f["kind"] for f in payload["findings"]}
    assert "missing_table" in kinds


def test_red_path_returns_one_when_ledger_behind(
    script_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from openminion.modules.storage.runtime.migrations import DEFAULT_MIGRATIONS

    db_path = tmp_path / "partial.db"
    migrate_database(db_path, migrations=DEFAULT_MIGRATIONS[:1])

    buf = _capture_stdout(monkeypatch)
    exit_code = script_module.run(["--db", str(db_path)])
    assert exit_code == script_module.EXIT_DRIFT
    payload = json.loads(buf.getvalue())
    assert payload["has_drift"] is True


def test_quiet_suppresses_stdout(
    script_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "live.db"
    migrate_database(db_path)
    buf = _capture_stdout(monkeypatch)
    exit_code = script_module.run(["--db", str(db_path), "--quiet"])
    assert exit_code == script_module.EXIT_OK
    assert buf.getvalue() == ""


def test_invocation_without_target_returns_usage(
    script_module: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = script_module.run([])
    assert exit_code == script_module.EXIT_USAGE
