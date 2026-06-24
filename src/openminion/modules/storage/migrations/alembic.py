from __future__ import annotations

from datetime import datetime, timezone
import importlib
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

from openminion.modules.storage.migrations.module_ids import get_module_application_id

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection as SAConnection
    from sqlalchemy.engine import Engine

_NESTED_MODULE_STORAGE_IMPORT_PATHS: dict[str, list[str]] = {
    "authoring": ["openminion.modules.tool.authoring.storage"],
}


def discover_module_storage_root(module_id: str) -> Path | None:
    normalized = str(module_id or "").strip().lower()
    if not normalized:
        return None
    # controlplane channel families with durable module_id
    candidate_paths = [f"openminion.modules.{normalized}.storage"]
    candidate_paths.extend(_NESTED_MODULE_STORAGE_IMPORT_PATHS.get(normalized, ()))
    if normalized.startswith("controlplane_"):
        channel_name = normalized[len("controlplane_") :]
        candidate_paths.append(
            f"openminion.modules.controlplane.channels.{channel_name}.storage"
        )
    storage_pkg = None
    for candidate in candidate_paths:
        try:
            storage_pkg = importlib.import_module(candidate)
            break
        except Exception:  # noqa: BLE001
            continue
    if storage_pkg is None:
        return None
    storage_file = getattr(storage_pkg, "__file__", None)
    if not storage_file:
        return None
    return Path(storage_file).expanduser().resolve(strict=False).parent


def discover_module_alembic_paths(module_id: str) -> tuple[Path | None, Path | None]:
    storage_root = discover_module_storage_root(module_id)
    if storage_root is None:
        return None, None
    ini_path = storage_root / "alembic.ini"
    script_location = storage_root / "migrations"
    if not (script_location / "env.py").exists():
        script_location = None
    return (ini_path if ini_path.exists() else None), script_location


def run_module_alembic_migrations(
    *,
    module_id: str,
    db_path: str | Path,
    target_user_version: int | None = 0,
) -> None:
    from openminion.modules.storage.migrations.runner import MigrationRunner

    path = Path(db_path).expanduser().resolve(strict=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)

    runner = MigrationRunner(
        module_id=module_id,
        db_path=path,
        module_application_id=get_module_application_id(module_id),
        target_user_version=target_user_version,
    )
    report = runner.migrate(target="head")
    if not report.success:
        raise RuntimeError(
            report.error or f"Alembic migration failed for module '{module_id}'"
        )


def run_module_env(
    *,
    module_id: str,
    module_application_id: int,
    target_user_version: int | None = 0,
) -> None:
    from alembic import context
    from sqlalchemy import engine_from_config, pool, text

    target_metadata = None
    config = context.config

    def _now_iso() -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    def _sync_openminion_identity(
        connection: SAConnection, *, dialect_name: str
    ) -> None:
        if dialect_name == "sqlite":
            connection.execute(
                text(f"PRAGMA application_id={int(module_application_id)}")
            )
            if target_user_version is not None:
                connection.execute(
                    text(f"PRAGMA user_version={int(target_user_version)}")
                )

        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS om_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
        )

        revision_row = connection.execute(
            text("SELECT version_num FROM alembic_version LIMIT 1")
        ).fetchone()
        schema_head = revision_row[0] if revision_row and revision_row[0] else "head"
        migrated_at = _now_iso()

        for key, value in (
            ("module_id", module_id),
            ("schema_head", str(schema_head)),
            ("last_migrated_at", migrated_at),
        ):
            connection.execute(
                text(
                    """
                    INSERT INTO om_meta(key, value)
                    VALUES (:key, :value)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value
                    """
                ),
                {"key": key, "value": value},
            )

    def run_migrations_offline() -> None:
        url = config.get_main_option("sqlalchemy.url")
        context.configure(
            url=url,
            target_metadata=target_metadata,
            literal_binds=True,
            dialect_opts={"paramstyle": "named"},
            render_as_batch=str(url).startswith("sqlite"),
        )
        with context.begin_transaction():
            context.run_migrations()

    def _run_with_connection(connection: SAConnection) -> None:
        dialect_name = connection.dialect.name
        if dialect_name == "sqlite":
            connection.execute(text("PRAGMA foreign_keys=ON"))
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=dialect_name == "sqlite",
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()
            _sync_openminion_identity(connection, dialect_name=dialect_name)

    def run_migrations_online() -> None:
        external_connection = config.attributes.get("connection")
        external_engine: Engine | None = config.attributes.get("engine")
        if external_connection is not None:
            _run_with_connection(external_connection)
            return
        if external_engine is None:
            external_engine = engine_from_config(
                config.get_section(config.config_ini_section, {}),
                prefix="sqlalchemy.",
                poolclass=pool.NullPool,
            )

        with external_engine.connect() as connection:
            _run_with_connection(connection)

    if context.is_offline_mode():
        run_migrations_offline()
    else:
        run_migrations_online()


def apply_ddl_statements(statements: Iterable[str]) -> None:
    from alembic import op

    bind = op.get_bind()
    for statement in statements:
        bind.exec_driver_sql(str(statement).strip())


def drop_sql_objects(
    *, table_names: Iterable[str] = (), index_names: Iterable[str] = ()
) -> None:
    from alembic import op

    bind = op.get_bind()
    for index_name in reversed(list(index_names)):
        bind.exec_driver_sql(f'DROP INDEX IF EXISTS "{index_name}"')
    for table_name in reversed(list(table_names)):
        bind.exec_driver_sql(f'DROP TABLE IF EXISTS "{table_name}"')
