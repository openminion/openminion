from __future__ import annotations

from datetime import datetime, timezone
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool, text

# Revision scripts in this template are operation-centric; no ORM metadata required.
target_metadata = None

# Replace these defaults in each module.
MODULE_ID = "replace-module-id"
MODULE_APPLICATION_ID = 0x4F4D0000
TARGET_USER_VERSION = 1

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _xarg(name: str, default: str) -> str:
    xargs = context.get_x_argument(as_dictionary=True)
    return str(xargs.get(name, default))


def _sync_openminion_identity(connection) -> None:
    module_id = _xarg("module_id", MODULE_ID)
    module_version = _xarg("module_version", "0.0.1")
    app_id = int(_xarg("module_app_id", str(MODULE_APPLICATION_ID)))
    target_user_version = int(_xarg("target_user_version", str(TARGET_USER_VERSION)))

    created_at = _now_iso()

    connection.execute(text(f"PRAGMA application_id={app_id}"))
    connection.execute(text(f"PRAGMA user_version={target_user_version}"))

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
    schema_head = revision_row[0] if revision_row and revision_row[0] else "unknown"

    for key, value in (
        ("module_id", module_id),
        ("module_version", module_version),
        ("schema_head", str(schema_head)),
        ("created_at", created_at),
        ("last_migrated_at", created_at),
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
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        connection.execute(text("PRAGMA foreign_keys=ON"))

        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()

        # Keep OpenMinion identity metadata synchronized at each migration run.
        _sync_openminion_identity(connection)
        connection.commit()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
