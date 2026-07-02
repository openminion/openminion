from __future__ import annotations

import argparse
from pathlib import Path
from typing import TYPE_CHECKING, Any

from openminion.cli.parser.flags import add_json_output_flag
from openminion.cli.presentation.json_output import print_json_payload

if TYPE_CHECKING:
    from openminion.modules.storage.migrations.runner import MigrationRunner
    from openminion.modules.storage.migrations.telemetry import StorageTelemetryHook


_BACKEND_POSTGRES: str = "postgres"


def run_storage(args) -> int:
    from openminion.cli.bootstrap.loader import load_config
    from openminion.modules.storage.engine import StorageEngine

    cfg = load_config(args.config)
    sqlite_path = _resolve_sqlite_path(args.sqlite, default=cfg.storage.path)
    root = _resolve_root(args.root, sqlite_path=sqlite_path)
    fallback = Path(args.fallback).expanduser().resolve() if args.fallback else root

    engine = StorageEngine.from_paths(
        root_dir=root,
        sqlite_path=sqlite_path,
        fallback_root=fallback,
        wal=True,
    )
    module_store = engine.module(args.namespace)
    try:
        if args.storage_command == "status":
            payload = {
                "ok": True,
                "sqlite_path": str(sqlite_path),
                "root": str(root),
                "namespace": args.namespace,
                "status": module_store.status(),
            }
            _print(payload, as_json=bool(args.json))
            return 0

        if args.storage_command == "reindex":
            report = module_store.reindex(from_fs=True, since_ts=args.since_ts)
            payload = {
                "ok": True,
                "sqlite_path": str(sqlite_path),
                "fallback_root": str(fallback),
                "namespace": args.namespace,
                "report": report.to_dict(),
            }
            _print(payload, as_json=bool(args.json))
            return 0

        if args.storage_command == "gc":
            report = module_store.gc(
                {
                    "dry_run": bool(args.plan),
                    "max_age_days": int(args.max_age_days),
                    "max_total_bytes": int(args.max_total_bytes),
                }
            )
            payload = {"ok": True, "root": str(root), "report": report}
            _print(payload, as_json=bool(args.json))
            return 0

        raise RuntimeError("Unknown storage command")
    finally:
        engine.close()


def _resolve_sqlite_path(raw: str | None, *, default: str) -> Path:
    if raw and str(raw).strip():
        return Path(str(raw)).expanduser().resolve()
    return Path(str(default)).expanduser().resolve()


def _resolve_root(raw: str | None, *, sqlite_path: Path) -> Path:
    if raw and str(raw).strip():
        return Path(str(raw)).expanduser().resolve()
    return sqlite_path.parent / "storage"


def _print(payload: dict, *, as_json: bool) -> None:
    if as_json:
        print_json_payload(payload)
        return
    status = payload.get("status")
    if isinstance(status, dict):
        print(
            "storage status: "
            f"sqlite_ok={status.get('sqlite_ok')} "
            f"fallback_mode={status.get('fallback_mode')} "
            f"last_error={status.get('last_error')}"
        )
        return
    report = payload.get("report")
    if isinstance(report, dict):
        print("storage report:")
        for key in sorted(report.keys()):
            print(f"- {key}: {report[key]}")
        return
    print_json_payload(payload)


def _make_runner(
    module_id: str,
    db_path: str,
    *,
    backend_type: str = "sqlite",
    engine: object = None,
    telemetry_hook: StorageTelemetryHook | None = None,
) -> MigrationRunner:
    from openminion.modules.storage.migrations.module_ids import MODULE_APPLICATION_IDS
    from openminion.modules.storage.migrations.runner import MigrationRunner

    app_id = MODULE_APPLICATION_IDS.get(module_id, 0)
    kwargs: dict = dict(
        module_id=module_id,
        db_path=db_path,
        module_application_id=app_id,
        backend_type=backend_type,
        telemetry_hook=telemetry_hook,
    )
    if engine is not None:
        kwargs["engine"] = engine
    return MigrationRunner(**kwargs)


def _build_storage_telemetry_hook() -> StorageTelemetryHook:
    from openminion.modules.telemetry import storage_hook
    from openminion.modules.telemetry.service import TelemetryService

    service = TelemetryService()
    return storage_hook.TelemetryServiceStorageHook(service)


def _get_postgres_engine(postgres_url: str) -> Any | None:
    try:
        import sqlalchemy as sa

        return sa.create_engine(postgres_url)
    except ImportError:
        return None


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


def _get_validated_module_ids(
    backend_type: str, requested_module: str | None
) -> tuple[list[str], list[str]]:
    from openminion.modules.storage.migrations.module_ids import MODULE_APPLICATION_IDS
    from openminion.modules.storage.migrations.registry import POSTGRES_VALIDATED_MODULES

    all_ids = list(MODULE_APPLICATION_IDS.keys())

    if requested_module:
        if requested_module not in MODULE_APPLICATION_IDS:
            raise SystemExit(
                f"Unknown module: {requested_module!r}. Known: {sorted(MODULE_APPLICATION_IDS)}"
            )
        if (
            backend_type == _BACKEND_POSTGRES
            and requested_module not in POSTGRES_VALIDATED_MODULES
        ):
            raise SystemExit(
                f"Module {requested_module!r} is not validated for Postgres. "
                f"Validated modules: {sorted(POSTGRES_VALIDATED_MODULES)}"
            )
        return [requested_module], []

    if backend_type == _BACKEND_POSTGRES:
        to_run = [m for m in all_ids if m in POSTGRES_VALIDATED_MODULES]
        skipped = [m for m in all_ids if m not in POSTGRES_VALIDATED_MODULES]
        return to_run, skipped

    return all_ids, []


def _resolve_backend_type(args) -> tuple[str, str]:
    postgres_url = str(getattr(args, "postgres_url", None) or "")
    backend_type = (
        "postgres"
        if (getattr(args, "backend", None) == _BACKEND_POSTGRES or postgres_url)
        else "sqlite"
    )
    return backend_type, postgres_url


def _resolve_postgres_engine(backend_type: str, postgres_url: str) -> Any | None:
    if backend_type != _BACKEND_POSTGRES:
        return None
    if not postgres_url:
        raise SystemExit("--postgres-url is required for Postgres backend")
    return _get_postgres_engine(postgres_url)


def _append_skipped_module_results(
    skipped: list[str], *, as_json: bool
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for module_id in skipped:
        results.append(
            {
                "module_id": module_id,
                "status": "skipped",
                "reason": "not validated for Postgres",
            }
        )
        if not as_json:
            print(f"  skip  {module_id}: not validated for Postgres")
    return results


def _run_migrate_plan(module_id: str, runner, *, as_json: bool) -> dict[str, Any]:
    try:
        state = runner.detect()
        result = {
            "module_id": module_id,
            "status": "plan",
            "current_revision": state.alembic_revision,
            "head": "head",
            "pending": state.alembic_revision is None,
        }
        if not as_json:
            pending = "pending" if state.alembic_revision is None else "up to date"
            print(
                f"  {module_id}: revision={state.alembic_revision or 'none'} ({pending})"
            )
        return result
    except Exception as exc:
        if not as_json:
            print(f"  error {module_id}: {exc}")
        return {"module_id": module_id, "status": "error", "error": str(exc)}


def _run_migrate_apply(
    module_id: str, runner, *, verify_after: bool, as_json: bool
) -> dict[str, Any]:
    try:
        if verify_after:
            report = runner.migrate_with_verify(target="head")
        else:
            report = runner.migrate(target="head")
        result = {
            "module_id": module_id,
            "status": "ok" if report.success else "error",
            "applied": (
                list(report.applied_versions)
                if hasattr(report, "applied_versions")
                else []
            ),
            "success": report.success,
        }
        if not as_json:
            status = "ok" if report.success else "FAIL"
            print(f"  {status}  {module_id}")
        return result
    except Exception as exc:
        if not as_json:
            print(f"  error {module_id}: {exc}")
        return {"module_id": module_id, "status": "error", "error": str(exc)}


def _dispose_engine_safely(engine: Any | None) -> None:
    if engine is None:
        return
    try:
        engine.dispose()
    except Exception:
        pass


def run_storage_migrate(args) -> None:
    sqlite_path = str(getattr(args, "sqlite", None) or "")
    backend_type, postgres_url = _resolve_backend_type(args)
    plan_only = bool(getattr(args, "plan", False))
    verify_after = bool(getattr(args, "verify", False))
    requested_module = getattr(args, "module", None)
    as_json = bool(getattr(args, "json", False))

    to_run, skipped = _get_validated_module_ids(backend_type, requested_module)
    engine = _resolve_postgres_engine(backend_type, postgres_url)
    telemetry_hook = _build_storage_telemetry_hook()

    results = _append_skipped_module_results(skipped, as_json=as_json)
    for module_id in to_run:
        runner = _make_runner(
            module_id,
            sqlite_path or ":memory:",
            backend_type=backend_type,
            engine=engine,
            telemetry_hook=telemetry_hook,
        )
        if plan_only:
            results.append(_run_migrate_plan(module_id, runner, as_json=as_json))
        else:
            results.append(
                _run_migrate_apply(
                    module_id, runner, verify_after=verify_after, as_json=as_json
                )
            )

    _dispose_engine_safely(engine)

    if as_json:
        print_json_payload({"modules": results}, sort_keys=False)

    failures = [r for r in results if r.get("status") == "error"]
    if failures:
        raise SystemExit(1)


def run_storage_verify(args) -> None:
    sqlite_path = str(getattr(args, "sqlite", None) or "")
    postgres_url = str(getattr(args, "postgres_url", None) or "")
    backend_type = (
        "postgres"
        if (getattr(args, "backend", None) == _BACKEND_POSTGRES or postgres_url)
        else "sqlite"
    )
    requested_module = getattr(args, "module", None)
    as_json = bool(getattr(args, "json", False))

    to_run, skipped = _get_validated_module_ids(backend_type, requested_module)

    engine = None
    if backend_type == _BACKEND_POSTGRES:
        if not postgres_url:
            raise SystemExit("--postgres-url is required for Postgres backend")
        engine = _get_postgres_engine(postgres_url)

    results = []

    for module_id in skipped:
        results.append(
            {
                "module_id": module_id,
                "status": "skipped",
                "reason": "not validated for Postgres",
            }
        )
        if not as_json:
            print(f"  skip  {module_id}: not validated for Postgres")

    for module_id in to_run:
        runner = _make_runner(
            module_id,
            sqlite_path or ":memory:",
            backend_type=backend_type,
            engine=engine,
        )
        try:
            report = runner.verify(level="quick")
            passed = getattr(report, "passed", True)
            result = {
                "module_id": module_id,
                "status": "passed" if passed else "failed",
                "report": str(report),
            }
            if not as_json:
                status = "pass" if passed else "FAIL"
                print(f"  {status}  {module_id}")
        except Exception as exc:
            result = {"module_id": module_id, "status": "error", "error": str(exc)}
            if not as_json:
                print(f"  error {module_id}: {exc}")
        results.append(result)

    if engine is not None:
        try:
            engine.dispose()
        except Exception:
            pass

    if as_json:
        print_json_payload({"modules": results}, sort_keys=False)

    failures = [r for r in results if r.get("status") in ("failed", "error")]
    if failures:
        raise SystemExit(1)


def run_storage_backup(args) -> None:
    sqlite_path = str(getattr(args, "sqlite", None) or "")
    backend = str(getattr(args, "backend", None) or "sqlite")
    output_path = getattr(args, "output", None)
    as_json = bool(getattr(args, "json", False))

    if backend == _BACKEND_POSTGRES:
        msg = (
            "Postgres backup is not managed by OpenMinion.\n"
            "Use pg_dump with your connection string from OPENMINION_STORAGE_POSTGRES_URL:\n"
            '  pg_dump "$OPENMINION_STORAGE_POSTGRES_URL" -Fc -f backup.dump'
        )
        if as_json:
            print_json_payload(
                {"ok": True, "message": msg}, indent=None, sort_keys=False
            )
        else:
            print(msg)
        return

    if not sqlite_path:
        raise SystemExit("--sqlite is required for SQLite backup")

    runner = _make_runner("storage", sqlite_path, backend_type="sqlite")
    try:
        artifact = runner.backup()
        snapshot_path = str(getattr(artifact, "snapshot_path", artifact))
        if output_path:
            import shutil

            shutil.copy2(snapshot_path, output_path)
            snapshot_path = output_path
        result = {"ok": True, "snapshot_path": snapshot_path}
        if as_json:
            print_json_payload(result, indent=None, sort_keys=False)
        else:
            print(f"Backup created: {snapshot_path}")
    except Exception as exc:
        if as_json:
            print_json_payload(
                {"ok": False, "error": str(exc)},
                indent=None,
                sort_keys=False,
            )
        else:
            import sys

            print(f"Backup failed: {exc}", file=sys.stderr)
        raise SystemExit(1)


def run_storage_restore(args) -> None:
    import sys

    sqlite_path = str(getattr(args, "sqlite", None) or "")
    snapshot_path = str(getattr(args, "snapshot", None) or "")
    backend = str(getattr(args, "backend", None) or "sqlite")
    yes = bool(getattr(args, "yes", False))
    as_json = bool(getattr(args, "json", False))

    if backend == _BACKEND_POSTGRES:
        msg = (
            "Postgres restore is not managed by OpenMinion.\n"
            "Use pg_restore with your connection string from OPENMINION_STORAGE_POSTGRES_URL:\n"
            '  pg_restore -d "$OPENMINION_STORAGE_POSTGRES_URL" backup.dump'
        )
        if as_json:
            print_json_payload(
                {"ok": True, "message": msg}, indent=None, sort_keys=False
            )
        else:
            print(msg)
        return

    if not snapshot_path:
        raise SystemExit("--snapshot is required")
    if not sqlite_path:
        raise SystemExit("--sqlite is required for SQLite restore")

    if not yes:
        print(
            f"This will restore {sqlite_path!r} from {snapshot_path!r}. All current data will be replaced."
        )
        answer = input("Continue? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("Restore cancelled.")
            return

    runner = _make_runner("storage", sqlite_path, backend_type="sqlite")
    try:
        runner.restore(snapshot_path=snapshot_path, target_db_path=sqlite_path)
        result = {"ok": True, "restored_from": snapshot_path, "target": sqlite_path}
        if as_json:
            print_json_payload(result, indent=None, sort_keys=False)
        else:
            print(f"Restored {sqlite_path!r} from {snapshot_path!r}")
    except Exception as exc:
        if as_json:
            print_json_payload(
                {"ok": False, "error": str(exc)},
                indent=None,
                sort_keys=False,
            )
        else:
            print(f"Restore failed: {exc}", file=sys.stderr)
        raise SystemExit(1)


def _add_engine_path_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--sqlite", default=None, help="SQLite path override")
    parser.add_argument("--root", default=None, help="Blob root override")
    parser.add_argument(
        "--fallback", default=None, help="Fallback sidecar root override"
    )


def _add_backend_selection_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--backend",
        default="sqlite",
        choices=["sqlite", "postgres"],
        help="Storage backend",
    )
    parser.add_argument(
        "--postgres-url",
        dest="postgres_url",
        default="",
        help="Postgres connection URL",
    )


def _register_storage_status_subcommand(storage_subcommands) -> None:
    parser = storage_subcommands.add_parser(
        "status", help="Show storage health and fallback mode"
    )
    _add_engine_path_args(parser)
    parser.add_argument(
        "--namespace", default=None, help="Optional module namespace filter"
    )
    add_json_output_flag(parser)
    parser.set_defaults(handler=run_storage, needs_app=False)


def _register_storage_reindex_subcommand(storage_subcommands) -> None:
    parser = storage_subcommands.add_parser(
        "reindex", help="Replay sidecar logs into sqlite"
    )
    _add_engine_path_args(parser)
    parser.add_argument(
        "--namespace", default=None, help="Optional module namespace filter"
    )
    parser.add_argument(
        "--since-ts", default=None, help="Replay only records at/after timestamp"
    )
    add_json_output_flag(parser)
    parser.set_defaults(handler=run_storage, needs_app=False)


def _register_storage_gc_subcommand(storage_subcommands) -> None:
    parser = storage_subcommands.add_parser("gc", help="Run blob garbage collection")
    _add_engine_path_args(parser)
    parser.add_argument(
        "--namespace", default=None, help="Optional module namespace for status output"
    )
    parser.add_argument("--plan", action="store_true", help="Dry run only")
    parser.add_argument(
        "--max-age-days",
        type=int,
        default=-1,
        help="Delete candidates older than N days",
    )
    parser.add_argument(
        "--max-total-bytes",
        type=int,
        default=-1,
        help="Trim oldest blobs until total size is <= this value",
    )
    add_json_output_flag(parser)
    parser.set_defaults(handler=run_storage, needs_app=False)


def _register_storage_migrate_subcommand(storage_subcommands) -> None:
    parser = storage_subcommands.add_parser(
        "migrate", help="Run pending migrations for all modules"
    )
    parser.add_argument("--sqlite", default="", help="Path to SQLite database")
    _add_backend_selection_args(parser)
    parser.add_argument("--module", default=None, help="Migrate a single module by ID")
    parser.add_argument(
        "--plan", action="store_true", help="Show pending migrations without applying"
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Run the combined migrate+verify flow with automatic rollback on failed verify",
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.set_defaults(handler=run_storage_migrate, needs_app=False)


def _register_storage_verify_subcommand(storage_subcommands) -> None:
    parser = storage_subcommands.add_parser(
        "verify", help="Verify database integrity for all modules"
    )
    parser.add_argument("--sqlite", default="", help="Path to SQLite database")
    _add_backend_selection_args(parser)
    parser.add_argument("--module", default=None, help="Verify a single module by ID")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.set_defaults(handler=run_storage_verify, needs_app=False)


def _register_storage_backup_subcommand(storage_subcommands) -> None:
    parser = storage_subcommands.add_parser(
        "backup",
        help="Create a database backup snapshot (SQLite) or print pg_dump guidance (Postgres)",
    )
    parser.add_argument("--sqlite", default="", help="Path to SQLite database")
    parser.add_argument(
        "--backend",
        default="sqlite",
        choices=["sqlite", "postgres"],
        help="Storage backend",
    )
    parser.add_argument(
        "--output", default=None, help="Output path for backup file (SQLite only)"
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.set_defaults(handler=run_storage_backup, needs_app=False)


def _register_storage_restore_subcommand(storage_subcommands) -> None:
    parser = storage_subcommands.add_parser(
        "restore",
        help="Restore database from a snapshot (SQLite) or print pg_restore guidance (Postgres)",
    )
    parser.add_argument(
        "--snapshot", required=True, help="Path to snapshot file to restore from"
    )
    parser.add_argument("--sqlite", default="", help="Path to target SQLite database")
    parser.add_argument(
        "--backend",
        default="sqlite",
        choices=["sqlite", "postgres"],
        help="Storage backend",
    )
    parser.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.set_defaults(handler=run_storage_restore, needs_app=False)


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    storage = subparsers.add_parser("storage", help="Shared storage-core operations")
    storage_subcommands = storage.add_subparsers(dest="storage_command", required=True)

    _register_storage_status_subcommand(storage_subcommands)
    _register_storage_reindex_subcommand(storage_subcommands)
    _register_storage_gc_subcommand(storage_subcommands)
    _register_storage_migrate_subcommand(storage_subcommands)
    _register_storage_verify_subcommand(storage_subcommands)
    _register_storage_backup_subcommand(storage_subcommands)
    _register_storage_restore_subcommand(storage_subcommands)
