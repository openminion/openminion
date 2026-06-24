from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

from openminion.modules.storage.migrations.alembic import (
    discover_module_alembic_paths,
)
from openminion.modules.storage.migrations.module_ids import get_module_application_id
from openminion.modules.storage.migrations.runner import MigrationRunner
from openminion.modules.tool.authoring.schemas import AuthoredToolRow
from openminion.modules.tool.authoring.storage.migrations import (
    list_migrations,
    run_migrations,
)
from openminion.modules.tool.authoring.storage.store import SQLiteAuthoredToolStore


def test_authoring_migrations_apply_forward_and_set_module_identity(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "authoring.sqlite"

    run_migrations(db_path)

    runner = MigrationRunner(
        module_id="authoring",
        db_path=db_path,
        module_application_id=get_module_application_id("authoring"),
    )
    state = runner.detect()

    assert state.exists is True
    assert state.application_id == get_module_application_id("authoring")
    assert state.application_id_matches is True
    assert state.alembic_revision == "0001_baseline"
    assert state.om_meta["module_id"] == "authoring"
    assert state.om_meta["schema_head"] == "0001_baseline"
    assert list_migrations() == ["0001_baseline"]

    with sqlite3.connect(str(db_path)) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "tool_drafts" in tables
        assert "authored_tools" in tables
        assert "tool_authoring_audit_events" in tables


def test_authoring_migrations_downgrade_back_to_base(tmp_path: Path) -> None:
    db_path = tmp_path / "authoring.sqlite"
    run_migrations(db_path)

    ini_path, script_location = discover_module_alembic_paths("authoring")
    assert ini_path is not None
    assert script_location is not None

    cfg = Config(str(ini_path))
    cfg.set_main_option("script_location", str(script_location))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.downgrade(cfg, "base")

    with sqlite3.connect(str(db_path)) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert "tool_drafts" not in tables
    assert "authored_tools" not in tables
    assert "tool_authoring_audit_events" not in tables


def test_authored_tools_reject_duplicate_local_name_version_hash(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "authoring.sqlite"
    run_migrations(db_path)
    store = SQLiteAuthoredToolStore(db_path)

    row = AuthoredToolRow(
        tool_name="authored.demo@v1",
        local_name="demo",
        version_number=1,
        version_hash="sha256:abc",
        source_code="def tool(x):\n    return x\n",
        unit_tests_source="def test_tool():\n    assert True\n",
        args_schema_json='{"type":"object"}',
        returns_schema_json='{"type":"object"}',
        description="demo tool",
        dependencies_json="[]",
        tier="experimental",
        min_scope="POWER_USER",
        policy_grant_id=None,
        created_at="2026-05-20T00:00:00+00:00",
        updated_at="2026-05-20T00:00:00+00:00",
        created_by_agent_id="agent-1",
        promoted_at=None,
        promoted_by=None,
        success_count=0,
        failure_count=0,
        last_invocation_at=None,
        removed_at=None,
        removed_by=None,
    )
    store.insert_authored_tool(row)

    duplicate_hash = AuthoredToolRow(
        tool_name="authored.demo@v2",
        local_name="demo",
        version_number=2,
        version_hash="sha256:abc",
        source_code="def tool(x):\n    return x + 1\n",
        unit_tests_source="def test_tool():\n    assert True\n",
        args_schema_json='{"type":"object"}',
        returns_schema_json='{"type":"object"}',
        description="demo tool v2",
        dependencies_json="[]",
        tier="experimental",
        min_scope="POWER_USER",
        policy_grant_id=None,
        created_at="2026-05-20T00:00:00+00:00",
        updated_at="2026-05-20T00:00:00+00:00",
        created_by_agent_id="agent-1",
        promoted_at=None,
        promoted_by=None,
        success_count=0,
        failure_count=0,
        last_invocation_at=None,
        removed_at=None,
        removed_by=None,
    )

    try:
        try:
            store.insert_authored_tool(duplicate_hash)
        except sqlite3.IntegrityError:
            pass
        else:  # pragma: no cover - failure branch documents the expected invariant
            raise AssertionError("expected UNIQUE(local_name, version_hash) violation")
    finally:
        store.close()
