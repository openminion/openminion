from __future__ import annotations

import sqlite3
import os
import importlib
from pathlib import Path
import subprocess
import sys

from openminion.modules.skill.storage import migrations


def _run_alembic(db_path: Path, action: str, target: str) -> None:
    storage_root = Path(migrations.__file__).resolve().parent.parent
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2] / "src")
    code = """
from alembic import command
from alembic.config import Config
from pathlib import Path
import sys

storage_root = Path(sys.argv[1])
db_path = Path(sys.argv[2])
config = Config(str(storage_root / "alembic.ini"))
config.set_main_option("script_location", str(storage_root / "migrations"))
config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
getattr(command, sys.argv[3])(config, sys.argv[4])
"""
    subprocess.run(
        [
            sys.executable,
            "-c",
            code,
            str(storage_root),
            str(db_path),
            action,
            target,
        ],
        cwd=storage_root,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def test_admission_migration_backfills_legacy_active_version_and_downgrades(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "skill.db"
    with sqlite3.connect(db_path) as conn:
        for revision in ("0001_baseline", "0002_queue", "0003_audit"):
            module = importlib.import_module(
                f"openminion.modules.skill.storage.migrations.versions.{revision}"
            )
            for statement in module.DDL:
                conn.execute(statement)
        conn.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        conn.execute("INSERT INTO alembic_version VALUES ('0003_audit')")

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO skills(
                skill_id, name, status, scope, agent_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "skill.deploy",
                "Deploy",
                "verified",
                "global",
                None,
                "2026-01-01T00:00:00Z",
                "2026-01-02T00:00:00Z",
            ),
        )
        for version_hash, created_at in (
            ("v1", "2026-01-01T00:00:00Z"),
            ("v2", "2026-01-02T00:00:00Z"),
        ):
            conn.execute(
                """
                INSERT INTO skill_versions(
                    skill_id, version_hash, source_artifact_ref,
                    package_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    "skill.deploy",
                    version_hash,
                    f"artifact://{version_hash}",
                    f'{{"version_hash":"{version_hash}"}}',
                    created_at,
                ),
            )

    migrations.run_migrations(db_path)
    with sqlite3.connect(db_path) as conn:
        active = conn.execute(
            "SELECT active_version_hash FROM skills WHERE skill_id = ?",
            ("skill.deploy",),
        ).fetchone()
        admission = conn.execute(
            """
            SELECT state, authority_class, content_fingerprint
            FROM skill_version_admissions
            WHERE skill_id = ? AND version_hash = ?
            """,
            ("skill.deploy", "v2"),
        ).fetchone()

    assert active == ("v2",)
    assert admission == ("legacy_grandfathered", "legacy_grandfathered", "legacy:v2")

    _run_alembic(db_path, "downgrade", "0003_audit")
    with sqlite3.connect(db_path) as conn:
        skill_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(skills)").fetchall()
        }
        version_columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(skill_versions)").fetchall()
        }
        admission_table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            ("skill_version_admissions",),
        ).fetchone()

    assert "active_version_hash" not in skill_columns
    assert "content_fingerprint" not in version_columns
    assert admission_table is None
