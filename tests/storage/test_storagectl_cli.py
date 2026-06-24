from __future__ import annotations

import json
from pathlib import Path

import pytest

from openminion.modules.storage.cli import main as storage_main
from openminion.modules.session.storage.migrations import (
    run_migrations as run_session_migrations,
)


def _run_storagectl(args: list[str], capsys) -> dict:
    storage_main(args)
    output = capsys.readouterr().out.strip()
    assert output, "expected storagectl to emit JSON output"
    return json.loads(output)


def test_storagectl_pool_stats_without_url_reports_error(monkeypatch, capsys) -> None:

    monkeypatch.delenv("OPENMINION_STORAGE_POSTGRES_URL", raising=False)
    rc = storage_main(["pool-stats"])
    assert rc == 2
    output = capsys.readouterr().out.strip()
    assert output, "expected pool-stats to emit JSON error"
    payload = json.loads(output)
    assert payload["ok"] is False
    assert "Postgres URL" in payload["error"]


def test_storagectl_plan_emits_plan(tmp_path: Path, capsys) -> None:
    db_path = tmp_path / "session.db"
    payload = _run_storagectl(
        ["plan", "--namespace", "session", "--sqlite", str(db_path)],
        capsys,
    )
    assert payload["ok"] is True
    assert payload["module_id"] == "session"
    assert isinstance(payload.get("plan"), list)


def test_storagectl_backup_restore_roundtrip(tmp_path: Path, capsys) -> None:
    db_path = tmp_path / "session.db"
    run_session_migrations(db_path)

    snapshot_root = tmp_path / "snapshots"
    backup_payload = _run_storagectl(
        [
            "backup",
            "--namespace",
            "session",
            "--sqlite",
            str(db_path),
            "--snapshot-root",
            str(snapshot_root),
        ],
        capsys,
    )
    assert backup_payload["ok"] is True
    snapshot_path = backup_payload["backup"]["snapshot_path"]
    assert Path(snapshot_path).exists()

    restored_path = tmp_path / "restored.db"
    restore_payload = _run_storagectl(
        [
            "restore",
            "--namespace",
            "session",
            "--sqlite",
            str(restored_path),
            "--snapshot-path",
            snapshot_path,
        ],
        capsys,
    )
    assert restore_payload["ok"] is True
    assert restored_path.exists()


def test_storagectl_dr_drill_succeeds_end_to_end(tmp_path: Path, capsys) -> None:

    db_path = tmp_path / "session.db"
    run_session_migrations(db_path)

    restore_target = tmp_path / "restored" / "session.restored.db"
    payload = _run_storagectl(
        [
            "dr-drill",
            "--namespace",
            "session",
            "--sqlite",
            str(db_path),
            "--snapshot-root",
            str(tmp_path / "snapshots"),
            "--restore-target",
            str(restore_target),
            "--verify-level",
            "quick",
        ],
        capsys,
    )

    assert payload["ok"] is True, payload
    report = payload["report"]
    assert report["module_id"] == "session"
    assert Path(report["snapshot_path"]).exists()
    assert Path(report["restore_target_path"]).exists()
    assert report["verification"] is not None
    assert report["verification"]["ok"] is True
    assert report["restore_duration_seconds"] >= 0.0


def test_storagectl_plan_rejects_unknown_module(capsys) -> None:
    with pytest.raises(Exception):
        _run_storagectl(
            ["plan", "--namespace", "unknown-module", "--sqlite", "/tmp/om-unknown.db"],
            capsys,
        )


def test_storagectl_export_backend_postgres_without_url_errors(
    tmp_path: Path, monkeypatch, capsys
) -> None:

    monkeypatch.delenv("OPENMINION_STORAGE_POSTGRES_URL", raising=False)
    rc = storage_main(
        [
            "export",
            "--namespace",
            "task",
            "--sqlite",
            str(tmp_path / "irrelevant.db"),
            "--out",
            str(tmp_path / "omx-out"),
            "--backend",
            "postgres",
        ]
    )
    assert rc == 2
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["ok"] is False
    assert "Postgres URL" in payload["error"]
    # Bundle directory must not have been created for a failed --backend.
    assert not (tmp_path / "omx-out" / "manifest.json").exists()


def test_storagectl_import_backend_postgres_without_url_errors(
    tmp_path: Path, monkeypatch, capsys
) -> None:

    monkeypatch.delenv("OPENMINION_STORAGE_POSTGRES_URL", raising=False)
    # Build a fake input dir (rc=2 must short-circuit before manifest load).
    omx_in = tmp_path / "omx-in"
    omx_in.mkdir()
    rc = storage_main(
        [
            "import",
            "--namespace",
            "task",
            "--sqlite",
            str(tmp_path / "irrelevant.db"),
            "--input",
            str(omx_in),
            "--backend",
            "postgres",
        ]
    )
    assert rc == 2
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["ok"] is False
    assert "Postgres URL" in payload["error"]


def test_storagectl_a2a_archive_old_without_url_errors(
    tmp_path: Path, monkeypatch, capsys
) -> None:

    monkeypatch.delenv("OPENMINION_STORAGE_POSTGRES_URL", raising=False)
    rc = storage_main(
        [
            "a2a-archive-old",
            "--older-than-days",
            "7",
            "--audit-root",
            str(tmp_path / "audit"),
        ]
    )
    assert rc == 2
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["ok"] is False
    assert "Postgres URL" in payload["error"]


def test_storagectl_a2a_archive_old_rejects_zero_days(
    tmp_path: Path, monkeypatch, capsys
) -> None:

    monkeypatch.setenv("OPENMINION_STORAGE_POSTGRES_URL", "postgresql://unused/test")
    rc = storage_main(
        [
            "a2a-archive-old",
            "--older-than-days",
            "0",
            "--audit-root",
            str(tmp_path / "audit"),
        ]
    )
    assert rc == 2
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["ok"] is False
    assert "--older-than-days" in payload["error"]


def test_storagectl_export_backend_default_is_sqlite(tmp_path: Path, capsys) -> None:

    db_path = tmp_path / "sample.db"
    import sqlite3

    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO sample (name) VALUES ('alpha')")
        conn.commit()

    payload = _run_storagectl(
        [
            "export",
            "--namespace",
            "task",
            "--sqlite",
            str(db_path),
            "--out",
            str(tmp_path / "omx"),
        ],
        capsys,
    )
    assert payload["ok"] is True
    assert (tmp_path / "omx" / "manifest.json").exists()
