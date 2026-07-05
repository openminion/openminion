from __future__ import annotations

import json
from pathlib import Path

from openminion.modules.storage.cli import main as storage_main
from openminion.modules.storage.migrations.module_ids import get_module_application_id
from openminion.modules.storage.migrations import export_omx, import_omx


def _create_sample_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    import sqlite3

    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
        conn.execute("INSERT INTO sample (name) VALUES ('alpha'), ('beta')")
        conn.commit()


class _RaisingReporter:
    def on_start(self, **_kwargs) -> None:
        raise RuntimeError("reporter start failed")

    def on_progress(self, **_kwargs) -> None:
        raise RuntimeError("reporter progress failed")

    def on_end(self, **_kwargs) -> None:
        raise RuntimeError("reporter end failed")


def test_omx_roundtrip_via_helpers(tmp_path: Path) -> None:
    db_path = tmp_path / "sample.db"
    _create_sample_db(db_path)

    omx_dir = tmp_path / "omx"
    manifest = export_omx(
        db_path=db_path,
        module_id="task",
        module_application_id=get_module_application_id("task"),
        export_dir=omx_dir,
        export_notes="test",
    )
    assert (omx_dir / "manifest.json").exists()
    assert manifest.module_id == "task"

    restored_path = tmp_path / "restored.db"
    report = import_omx(
        omx_dir=omx_dir, target_db_path=restored_path, verify_checksums=True
    )
    assert report.success is True


def test_omx_reporter_failures_are_best_effort(tmp_path: Path) -> None:
    db_path = tmp_path / "sample.db"
    _create_sample_db(db_path)

    omx_dir = tmp_path / "omx"
    export_omx(
        db_path=db_path,
        module_id="task",
        module_application_id=get_module_application_id("task"),
        export_dir=omx_dir,
        reporter=_RaisingReporter(),
    )

    restored_path = tmp_path / "restored.db"
    report = import_omx(
        omx_dir=omx_dir,
        target_db_path=restored_path,
        verify_checksums=True,
        reporter=_RaisingReporter(),
    )

    assert report.success is True


def test_omx_roundtrip_via_cli(tmp_path: Path, capsys) -> None:
    db_path = tmp_path / "sample.db"
    _create_sample_db(db_path)

    omx_dir = tmp_path / "omx-cli"
    storage_main(
        [
            "export",
            "--namespace",
            "task",
            "--sqlite",
            str(db_path),
            "--out",
            str(omx_dir),
        ]
    )
    export_out = capsys.readouterr().out.strip()
    assert export_out, "export should emit JSON"
    export_payload = json.loads(export_out)
    assert export_payload["ok"] is True

    restored_path = tmp_path / "restored.db"
    storage_main(
        [
            "import",
            "--namespace",
            "task",
            "--sqlite",
            str(restored_path),
            "--input",
            str(omx_dir),
        ]
    )
    import_out = capsys.readouterr().out.strip()
    assert import_out, "import should emit JSON"
    import_payload = json.loads(import_out)
    assert import_payload["ok"] is True
    assert restored_path.exists()
