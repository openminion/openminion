from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

from openminion.modules.skill.storage import migrations


def test_workflow_observation_migration_round_trip(tmp_path: Path) -> None:
    db_path = tmp_path / "skill.db"

    migrations.run_migrations(db_path)

    with sqlite3.connect(db_path) as connection:
        head = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(skill_workflow_observations)"
            ).fetchall()
        }
    assert head == ("0007_workflow_observations",)
    assert columns == {
        "agent_id",
        "source_run_ref",
        "bundle_id",
        "provenance_checksum",
        "bundle_json",
        "created_at",
    }

    storage_root = Path(migrations.__file__).resolve().parent.parent
    config = Config(str(storage_root / "alembic.ini"))
    config.set_main_option("script_location", str(storage_root / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.downgrade(config, "0006_proposal_replay_proof")

    with sqlite3.connect(db_path) as connection:
        downgraded_head = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
        table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            ("skill_workflow_observations",),
        ).fetchone()
        index = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name=?",
            ("idx_skill_workflow_observations_agent",),
        ).fetchone()
        proposal_table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            ("skill_proposals",),
        ).fetchone()
    assert downgraded_head == ("0006_proposal_replay_proof",)
    assert table is None
    assert index is None
    assert proposal_table == ("skill_proposals",)

    command.upgrade(config, "head")
    with sqlite3.connect(db_path) as connection:
        upgraded_head = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
        restored_table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            ("skill_workflow_observations",),
        ).fetchone()
    assert upgraded_head == ("0007_workflow_observations",)
    assert restored_table == ("skill_workflow_observations",)
