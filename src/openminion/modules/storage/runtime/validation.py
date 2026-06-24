import logging

logger = logging.getLogger(__name__)


class StorageConfigError(RuntimeError):
    """Raised when storage backend configuration is invalid at boot time."""


def _redact_url(url: str) -> str:
    try:
        from urllib.parse import urlparse, urlunparse

        parsed = urlparse(url)
        if parsed.password:
            netloc = parsed.netloc.replace(f":{parsed.password}@", ":***@")
            return urlunparse(parsed._replace(netloc=netloc))
        return url
    except Exception:
        return "<connection string>"


def validate_postgres_config(
    postgres_url: str,
    *,
    check_connection: bool = True,
    check_migrations: bool = True,
) -> None:
    """Validate Postgres backend configuration at boot time.

    Raises StorageConfigError with actionable messages on failure.
    Does NOT log or include the raw postgres_url — uses _redact_url().
    """
    if not postgres_url.strip():
        raise StorageConfigError(
            "storage.postgres_url is required when storage.backend is 'postgres'. "
            "Set it via the OPENMINION_STORAGE_POSTGRES_URL environment variable "
            "or the storage.postgres_url config field."
        )

    try:
        import sqlalchemy  # noqa: F401
    except ImportError:
        raise StorageConfigError(
            "The 'sqlalchemy' package is required for Postgres storage. "
            "Install it with: pip install openminion[postgres]"
        )
    try:
        import psycopg  # noqa: F401
    except ImportError:
        raise StorageConfigError(
            "The 'psycopg' package is required for Postgres storage. "
            "Install it with: pip install openminion[postgres]"
        )

    if not check_connection:
        return

    redacted = _redact_url(postgres_url)
    try:
        import sqlalchemy as sa

        engine = sa.create_engine(postgres_url, pool_pre_ping=True)
        with engine.connect() as conn:
            conn.execute(sa.text("SELECT 1"))
        engine.dispose()
    except Exception as exc:
        raise StorageConfigError(
            f"Cannot connect to Postgres at {redacted}: {exc}. "
            "Check that the database is running and the connection string is correct."
        ) from exc

    if not check_migrations:
        return

    try:
        from openminion.modules.storage.migrations.registry import (
            POSTGRES_VALIDATED_MODULES,
        )
        from openminion.modules.storage.migrations.module_ids import (
            MODULE_APPLICATION_IDS,
        )
        from openminion.modules.storage.migrations.runner import MigrationRunner
        import sqlalchemy as sa

        engine = sa.create_engine(postgres_url)
        for module_id in POSTGRES_VALIDATED_MODULES:
            if module_id not in MODULE_APPLICATION_IDS:
                continue
            app_id = MODULE_APPLICATION_IDS[module_id]
            runner = MigrationRunner(
                module_id=module_id,
                db_path=":memory:",  # not used for postgres detect
                module_application_id=app_id,
                backend_type="postgres",
                engine=engine,
            )
            try:
                state = runner.detect()
                if state.alembic_revision is None:
                    logger.warning(
                        "Postgres module '%s' has pending migrations. "
                        "Run: openminion storage migrate --module %s",
                        module_id,
                        module_id,
                    )
            except Exception:
                pass  # detect failure is non-fatal at startup
        engine.dispose()
    except Exception:
        pass  # migration check failure is non-fatal


def validate_storage_config(
    backend: str,
    postgres_url: str,
    *,
    check_connection: bool = True,
    check_migrations: bool = True,
) -> None:
    """Entry point for boot-time storage config validation.

    For SQLite: no-op (always valid).
    For Postgres: validates URL, extras, connection, migrations.
    """
    if backend != "postgres":
        return
    validate_postgres_config(
        postgres_url,
        check_connection=check_connection,
        check_migrations=check_migrations,
    )
