from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openminion.modules.storage.cli import _build_parser, _select_reporter
from openminion.modules.storage.migrations.transfer import (
    export_omx,
    import_omx,
)
from openminion.modules.storage.progress import NullProgressReporter


@dataclass
class _RecordingReporter:
    starts: list[dict[str, Any]] = field(default_factory=list)
    advances: list[dict[str, Any]] = field(default_factory=list)
    ends: list[dict[str, Any]] = field(default_factory=list)
    raise_on_progress: bool = False

    def on_start(self, *, total: int | None, label: str) -> None:
        self.starts.append({"total": total, "label": label})

    def on_progress(self, *, advance: int = 1, message: str | None = None) -> None:
        if self.raise_on_progress:
            raise RuntimeError("recording reporter failed")
        self.advances.append({"advance": advance, "message": message})

    def on_end(self, *, success: bool, message: str | None = None) -> None:
        self.ends.append({"success": success, "message": message})


def _build_minimal_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute('CREATE TABLE "items" ("id" INTEGER PRIMARY KEY, "payload" TEXT)')
        conn.executemany(
            'INSERT INTO "items" ("id", "payload") VALUES (?, ?)',
            [(1, "a"), (2, "b"), (3, "c")],
        )
        conn.commit()
    finally:
        conn.close()


def test_export_omx_invokes_reporter_for_each_row(tmp_path: Path) -> None:
    db_path = tmp_path / "src.db"
    out_dir = tmp_path / "omx_out"
    _build_minimal_db(db_path)

    reporter = _RecordingReporter()
    manifest = export_omx(
        db_path=db_path,
        module_id="testmod",
        module_application_id=99,
        export_dir=out_dir,
        reporter=reporter,
    )

    # Sanity — manifest is valid and three rows were exported.
    total_exported = sum(table.row_count for table in manifest.tables)
    assert total_exported == 3

    assert len(reporter.starts) == 1
    assert "testmod" in reporter.starts[0]["label"]
    # One advance per row written.
    assert len(reporter.advances) == 3
    assert all(item["advance"] == 1 for item in reporter.advances)
    assert len(reporter.ends) == 1
    assert reporter.ends[0]["success"] is True


def test_import_omx_invokes_reporter_with_known_total(tmp_path: Path) -> None:
    src_db = tmp_path / "src.db"
    omx_dir = tmp_path / "omx"
    target_db = tmp_path / "target.db"
    _build_minimal_db(src_db)

    export_omx(
        db_path=src_db,
        module_id="testmod",
        module_application_id=99,
        export_dir=omx_dir,
    )

    reporter = _RecordingReporter()
    report = import_omx(
        omx_dir=omx_dir,
        target_db_path=target_db,
        verify_checksums=False,
        reporter=reporter,
    )

    assert report.imported_rows == 3
    assert len(reporter.starts) == 1
    # Import reporter knows total upfront from manifest.
    assert reporter.starts[0]["total"] == 3
    # One advance per table (advance=len(rows)).
    assert sum(item["advance"] for item in reporter.advances) == 3
    assert len(reporter.ends) == 1


def test_export_omx_swallows_reporter_failures(tmp_path: Path) -> None:
    db_path = tmp_path / "src.db"
    out_dir = tmp_path / "omx_out"
    _build_minimal_db(db_path)

    reporter = _RecordingReporter(raise_on_progress=True)
    # Must not raise.
    manifest = export_omx(
        db_path=db_path,
        module_id="testmod",
        module_application_id=99,
        export_dir=out_dir,
        reporter=reporter,
    )
    total_exported = sum(table.row_count for table in manifest.tables)
    assert total_exported == 3
    # on_end is still called because on_progress failures are swallowed.
    assert len(reporter.ends) == 1


def test_cli_progress_flag_is_recognised() -> None:
    parser = _build_parser()
    args = parser.parse_args(
        [
            "--progress",
            "status",
        ]
    )
    assert getattr(args, "progress") is True


def test_cli_select_reporter_defaults_to_null() -> None:
    parser = _build_parser()
    args = parser.parse_args(["status"])
    reporter = _select_reporter(args)
    assert isinstance(reporter, NullProgressReporter)
