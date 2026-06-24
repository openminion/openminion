from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

from openminion.cli.commands.storage import (
    _get_validated_module_ids,
    run_storage_migrate,
    run_storage_verify,
)
from tests.storage.postgres_test_utils import schema_url

pytestmark = pytest.mark.postgres


TIER2_VALIDATED_MODULES = {
    "secret",
    "session",
    "telemetry",
    "identity",
    "registry",
    "task",
    "skill",
    "controlplane",
    "policy",
    "compress",
    "retrieve",
    "artifact",
    "a2a",
    "memory",
}


@pytest.mark.postgres
def test_postgres_tier2_modules_are_validated() -> None:
    to_run, skipped = _get_validated_module_ids("postgres", None)
    assert set(to_run) == TIER2_VALIDATED_MODULES
    # After Phase 5C, memory is also validated — no persistent modules skipped
    assert "memory" not in skipped


@pytest.mark.postgres
def test_storage_cli_plan_and_verify_cover_tier2_modules(
    tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    pytest.importorskip("sqlalchemy")
    postgres_url = str(os.getenv("OPENMINION_TEST_POSTGRES_URL", ""))
    if not postgres_url:
        pytest.skip("OPENMINION_TEST_POSTGRES_URL is not set")

    import sqlalchemy as sa
    import uuid

    schema_name = f"mpt2_integration_{uuid.uuid4().hex}"
    admin_engine = sa.create_engine(postgres_url, future=True)
    with admin_engine.begin() as conn:
        conn.execute(sa.text(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"'))
    try:
        scoped_url = schema_url(postgres_url, schema_name)
        run_storage_migrate(
            SimpleNamespace(
                backend="postgres",
                postgres_url=scoped_url,
                sqlite=str(tmp_path / "openminion.db"),
                module=None,
                plan=True,
                json=True,
            )
        )
        plan_payload = json.loads(capsys.readouterr().out)
        plan_rows = {row["module_id"]: row["status"] for row in plan_payload["modules"]}

        for module_id in TIER2_VALIDATED_MODULES:
            assert plan_rows[module_id] == "plan"
        assert plan_rows.get("memory") == "plan"  # memory now validated (Phase 5C)

        run_storage_verify(
            SimpleNamespace(
                backend="postgres",
                postgres_url=scoped_url,
                sqlite=str(tmp_path / "openminion.db"),
                module=None,
                json=True,
            )
        )
        verify_payload = json.loads(capsys.readouterr().out)
        verify_rows = {
            row["module_id"]: row["status"] for row in verify_payload["modules"]
        }

        for module_id in TIER2_VALIDATED_MODULES:
            assert verify_rows[module_id] == "passed"
        assert verify_rows.get("memory") == "passed"  # memory now validated (Phase 5C)
    finally:
        with admin_engine.begin() as conn:
            conn.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()
