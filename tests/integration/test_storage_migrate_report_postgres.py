"""`storage migrate --backend postgres` on a new schema.

Entries carry the report error (#446), and `--module compress` finds its
migrations (#493). Each module migrates into its own schema,
`<schema>__<module id>` (#446).
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace
import uuid

import pytest

from openminion.cli.commands.storage import run_storage_migrate
from tests.storage.postgres_test_utils import drop_schemas, schema_url

pytestmark = pytest.mark.postgres


def test_postgres_module_migration_reports_success_and_failure(
    tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    sa = pytest.importorskip("sqlalchemy")
    postgres_url = str(os.getenv("OPENMINION_TEST_POSTGRES_URL", "")).strip()
    if not postgres_url:
        pytest.skip("OPENMINION_TEST_POSTGRES_URL is not set")

    schema_name = f"migrate_report_{uuid.uuid4().hex}"
    admin_engine = sa.create_engine(postgres_url, future=True)
    with admin_engine.begin() as conn:
        conn.execute(sa.text(f'CREATE SCHEMA "{schema_name}"'))
    scoped_url = schema_url(postgres_url, schema_name)
    args = SimpleNamespace(
        backend="postgres",
        postgres_url=scoped_url,
        sqlite=str(tmp_path / "openminion.db"),
        module="secret",
        plan=False,
        verify=False,
        json=True,
    )
    try:
        run_storage_migrate(args)
        assert json.loads(capsys.readouterr().out)["modules"] == [
            {"module_id": "secret", "status": "ok", "success": True}
        ]

        engine = sa.create_engine(scoped_url, future=True)
        try:
            with engine.begin() as conn:
                conn.execute(
                    sa.text(
                        f'UPDATE "{schema_name}__secret".alembic_version '
                        "SET version_num = '9999_not_a_revision'"
                    )
                )
        finally:
            engine.dispose()

        with pytest.raises(SystemExit) as exc:
            run_storage_migrate(args)

        assert exc.value.code == 1
        (entry,) = json.loads(capsys.readouterr().out)["modules"]
        assert entry["status"] == "error"
        assert entry["success"] is False
        assert "9999_not_a_revision" in entry["error"]
        assert "applied" not in entry
    finally:
        drop_schemas(admin_engine, schema_name)
        admin_engine.dispose()


def test_postgres_compress_module_migrates_a_new_schema(
    tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    sa = pytest.importorskip("sqlalchemy")
    postgres_url = str(os.getenv("OPENMINION_TEST_POSTGRES_URL", "")).strip()
    if not postgres_url:
        pytest.skip("OPENMINION_TEST_POSTGRES_URL is not set")

    schema_name = f"migrate_compress_{uuid.uuid4().hex}"
    admin_engine = sa.create_engine(postgres_url, future=True)
    with admin_engine.begin() as conn:
        conn.execute(sa.text(f'CREATE SCHEMA "{schema_name}"'))
    scoped_url = schema_url(postgres_url, schema_name)
    args = SimpleNamespace(
        backend="postgres",
        postgres_url=scoped_url,
        sqlite=str(tmp_path / "openminion.db"),
        module="compress",
        plan=False,
        verify=False,
        json=True,
    )
    try:
        code = 0
        try:
            run_storage_migrate(args)
        except SystemExit as exc:
            code = exc.code

        assert json.loads(capsys.readouterr().out)["modules"] == [
            {"module_id": "compress", "status": "ok", "success": True}
        ]
        assert code == 0
        module_schema = f"{schema_name}__compress"
        engine = sa.create_engine(scoped_url, future=True)
        try:
            with engine.connect() as conn:
                revisions = conn.execute(
                    sa.text(
                        f'SELECT version_num FROM "{module_schema}".alembic_version'
                    )
                )
                assert revisions.scalars().all() == ["0001_baseline"]
                module_id = conn.execute(
                    sa.text(
                        f'SELECT value FROM "{module_schema}".om_meta '
                        "WHERE key = 'module_id'"
                    )
                )
                assert module_id.scalar_one() == "compress"
                tables = set(sa.inspect(conn).get_table_names(schema=module_schema))
                base_tables = sa.inspect(conn).get_table_names(schema=schema_name)
        finally:
            engine.dispose()
        assert base_tables == []
        assert tables == {
            "alembic_version",
            "compression_failures",
            "compression_runs",
            "dropped_reasons",
            "om_meta",
        }
    finally:
        drop_schemas(admin_engine, schema_name)
        admin_engine.dispose()
