from __future__ import annotations

import argparse
import importlib
import os
from dataclasses import replace
from pathlib import Path
import sqlite3

from openminion.modules.cli_common import print_json_payload
from openminion.modules.storage.engine import StorageEngine
from openminion.modules.config import (
    is_module_standalone_mode,
    resolve_module_data_root,
    resolve_module_home_root,
)
from openminion.modules.storage.migrations import (
    MigrationRunner,
    build_migration_plan,
    export_omx,
    import_omx,
)
from openminion.modules.storage.progress import (
    NullProgressReporter,
    ProgressReporter,
    select_default_reporter,
)
from openminion.modules.storage.migrations.metadata import ensure_module_metadata
from openminion.modules.storage.migrations.module_ids import (
    get_module_application_id,
    schema_head_from_migrations,
)
from openminion.modules.storage.migrations.registry import (
    get_module_spec,
    ModuleMigrationSpec,
)
from openminion.modules.cli_common import (
    add_common_module_root_args,
    apply_home_data_root_env,
)
from openminion.modules.storage.constants import (
    DEFAULT_INTEGRATED_ROOT_SUBPATH,
    DEFAULT_INTEGRATED_SQLITE_SUBPATH,
    DEFAULT_STANDALONE_ROOT_SUBPATH,
    DEFAULT_STANDALONE_SQLITE_SUBPATH,
)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    home_root = str(getattr(args, "home_root", "") or "").strip()
    data_root = str(getattr(args, "data_root", "") or "").strip()
    apply_home_data_root_env(home_root=home_root, data_root=data_root)

    env_map = os.environ
    standalone_mode = is_module_standalone_mode(env_map)
    resolved_home_root = resolve_module_home_root(
        None,
        env_map,
        fallback_to_cwd=True,
    )
    resolved_data_root = resolve_module_data_root(
        home_root=resolved_home_root,
        env=env_map,
    )
    root_dir = str(getattr(args, "root", "") or "").strip()
    sqlite_path = str(getattr(args, "sqlite", "") or "").strip()
    fallback_root = str(getattr(args, "fallback", "") or "").strip()
    if not standalone_mode:
        default_root = (resolved_data_root / DEFAULT_INTEGRATED_ROOT_SUBPATH).resolve()
        if not root_dir:
            root_dir = str(default_root)
        if not sqlite_path:
            sqlite_path = str(
                (resolved_data_root / DEFAULT_INTEGRATED_SQLITE_SUBPATH).resolve()
            )
        if not fallback_root:
            fallback_root = str(default_root)
    else:
        if not root_dir:
            root_dir = str((Path.home() / DEFAULT_STANDALONE_ROOT_SUBPATH).resolve())
        if not sqlite_path:
            sqlite_path = str(
                (Path.home() / DEFAULT_STANDALONE_SQLITE_SUBPATH).resolve()
            )
        if not fallback_root:
            fallback_root = str(
                (Path.home() / DEFAULT_STANDALONE_ROOT_SUBPATH).resolve()
            )

    if args.cmd == "pool-stats":
        return _run_pool_stats(args, env_map)

    if args.cmd == "dr-drill":
        return _run_drill(args, sqlite_path=sqlite_path)

    if args.cmd == "a2a-archive-old":
        return _run_a2a_archive_old(args, env_map)

    engine = StorageEngine.from_paths(
        root_dir=root_dir,
        sqlite_path=sqlite_path,
        fallback_root=fallback_root,
    )
    module_store = engine.module(args.namespace)

    try:
        if args.cmd == "status":
            payload = {
                "ok": True,
                "status": module_store.status(),
                "db": engine.record_store.diagnostics(),
            }
            _print_json(payload)
            return 0

        if args.cmd == "db-health":
            _print_json({"ok": True, "db": engine.record_store.diagnostics()})
            return 0

        if args.cmd == "reindex":
            report = module_store.reindex(
                from_fs=not args.skip_fs,
                since_ts=args.since_ts,
                dry_run=args.dry_run,
                archive_replayed=args.archive_replayed,
                archive_root=args.archive_root,
            )
            _print_json({"ok": True, "report": report.to_dict()})
            return 0

        if args.cmd == "gc":
            report = module_store.gc(
                {
                    "dry_run": bool(args.plan),
                    "max_age_days": int(args.max_age_days),
                    "max_total_bytes": int(args.max_total_bytes),
                }
            )
            _print_json({"ok": True, "report": report})
            return 0

        if args.cmd == "blob-verify":
            result = engine.blob_store.verify(args.hash)
            _print_json({"ok": result.get("matches", False), "result": result})
            return 0

        if args.cmd == "events":
            events = module_store.list_events(args.session_id, limit=args.limit)
            _print_json({"ok": True, "events": events})
            return 0
        if args.cmd == "plan":
            module_id = _resolve_module_id(args.namespace)
            spec = get_module_spec(module_id)
            if spec is None:
                spec = ModuleMigrationSpec(
                    module_id=module_id,
                    module_application_id=get_module_application_id(module_id),
                )
            has_db = Path(sqlite_path).expanduser().resolve(strict=False).exists()
            spec = replace(spec, has_db=has_db)
            plan = build_migration_plan([spec])
            _print_json(
                {
                    "ok": True,
                    "module_id": module_id,
                    "plan": [item.__dict__ for item in plan],
                }
            )
            return 0
        if args.cmd == "migrate":
            module_id = _resolve_module_id(args.namespace)
            reporter = _select_reporter(args)
            _reporter_safe_start(reporter, label=f"migrate[{module_id}]")
            try:
                run_migrations, list_migrations = _load_module_migrations(module_id)
                run_migrations(sqlite_path)
                _ensure_module_identity(
                    sqlite_path=sqlite_path,
                    module_id=module_id,
                    list_migrations=list_migrations,
                )
                runner = _build_runner(
                    module_id=module_id,
                    sqlite_path=sqlite_path,
                )
                state = runner.detect()
                _reporter_safe_end(reporter, success=True)
                _print_json({"ok": True, "state": state.to_dict()})
                return 0
            except Exception:
                _reporter_safe_end(reporter, success=False)
                raise
        if args.cmd == "backup":
            module_id = _resolve_module_id(args.namespace)
            reporter = _select_reporter(args)
            _reporter_safe_start(reporter, label=f"backup[{module_id}]")
            try:
                _ensure_module_identity(
                    sqlite_path=sqlite_path,
                    module_id=module_id,
                    list_migrations=_load_module_migrations(module_id)[1],
                )
                runner = _build_runner(
                    module_id=module_id,
                    sqlite_path=sqlite_path,
                    snapshot_root=args.snapshot_root,
                )
                artifact = runner.backup(mode=args.mode)
                _reporter_safe_end(reporter, success=True)
                _print_json({"ok": True, "backup": artifact.to_dict()})
                return 0
            except Exception:
                _reporter_safe_end(reporter, success=False)
                raise
        if args.cmd == "restore":
            module_id = _resolve_module_id(args.namespace)
            reporter = _select_reporter(args)
            _reporter_safe_start(reporter, label=f"restore[{module_id}]")
            try:
                runner = _build_runner(
                    module_id=module_id,
                    sqlite_path=sqlite_path,
                )
                runner.restore(
                    snapshot_path=args.snapshot_path, target_db_path=sqlite_path
                )
                state = runner.detect()
                _reporter_safe_end(reporter, success=True)
                _print_json({"ok": True, "state": state.to_dict()})
                return 0
            except Exception:
                _reporter_safe_end(reporter, success=False)
                raise
        if args.cmd == "verify":
            module_id = _resolve_module_id(args.namespace)
            _ensure_module_identity(
                sqlite_path=sqlite_path,
                module_id=module_id,
                list_migrations=_load_module_migrations(module_id)[1],
            )
            runner = _build_runner(
                module_id=module_id,
                sqlite_path=sqlite_path,
            )
            report = runner.verify(level=args.level)
            _print_json({"ok": report.ok, "report": report.to_dict()})
            return 0
        if args.cmd == "export":
            module_id = _resolve_module_id(args.namespace)
            backend = str(getattr(args, "backend", "sqlite") or "sqlite").lower()
            if backend == "sqlite":
                _ensure_module_identity(
                    sqlite_path=sqlite_path,
                    module_id=module_id,
                    list_migrations=_load_module_migrations(module_id)[1],
                )
            since_arg = getattr(args, "since", None)
            namespace_filter = getattr(args, "namespace_filter", None)
            where_clause = getattr(args, "where", None)
            since_dt = None
            if since_arg:
                from datetime import datetime as _dt

                # Accept ISO-8601 with or without trailing Z.
                normalised = (
                    since_arg.replace("Z", "+00:00")
                    if since_arg.endswith("Z")
                    else since_arg
                )
                since_dt = _dt.fromisoformat(normalised)
            if backend == "postgres":
                pg_store, pg_close = _open_postgres_record_store(
                    args, env_map, verb="export"
                )
                if pg_store is None:
                    return 2
                try:
                    manifest = export_omx(
                        db_path=None,
                        record_store=pg_store,
                        module_id=module_id,
                        module_application_id=get_module_application_id(module_id),
                        export_dir=args.out,
                        export_notes=args.notes,
                        since=since_dt,
                        namespace=namespace_filter,
                        where_clause=where_clause,
                        reporter=_select_reporter(args),
                    )
                finally:
                    pg_close()
            else:
                manifest = export_omx(
                    db_path=sqlite_path,
                    module_id=module_id,
                    module_application_id=get_module_application_id(module_id),
                    export_dir=args.out,
                    export_notes=args.notes,
                    since=since_dt,
                    namespace=namespace_filter,
                    where_clause=where_clause,
                    reporter=_select_reporter(args),
                )
            _print_json({"ok": True, "manifest": manifest.to_dict()})
            return 0
        if args.cmd == "import":
            backend = str(getattr(args, "backend", "sqlite") or "sqlite").lower()
            if backend == "postgres":
                pg_store, pg_close = _open_postgres_record_store(
                    args, env_map, verb="import"
                )
                if pg_store is None:
                    return 2
                try:
                    report = import_omx(
                        omx_dir=args.input,
                        target_db_path=None,
                        target_record_store=pg_store,
                        verify_checksums=not args.skip_checksum,
                        reporter=_select_reporter(args),
                    )
                finally:
                    pg_close()
            else:
                report = import_omx(
                    omx_dir=args.input,
                    target_db_path=sqlite_path,
                    verify_checksums=not args.skip_checksum,
                    reporter=_select_reporter(args),
                )
            _print_json({"ok": report.success, "report": report.to_dict()})
            return 0
    finally:
        engine.close()
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="storagectl")
    add_common_module_root_args(parser)
    parser.add_argument("--root", default=None)
    parser.add_argument("--sqlite", default=None)
    parser.add_argument("--fallback", default=None)
    parser.add_argument(
        "--namespace", default=None, help="Optional module namespace (example: sessctl)"
    )
    parser.add_argument(
        "--progress",
        action="store_true",
        help=(
            "Show a progress bar on TTY for long-running verbs "
            "(migrate, backup, restore, export, import, dr-drill). "
            "Requires the [progress] extra (tqdm); silently degrades "
            "to a no-op when tqdm is missing or stdout is not a TTY."
        ),
    )

    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="Show storage and DB health summary")
    sub.add_parser("db-health", help="Show SQLite diagnostics")

    pool_stats = sub.add_parser(
        "pool-stats",
        help="Show Postgres connection pool stats (requires --postgres-url or OPENMINION_STORAGE_POSTGRES_URL)",
    )
    pool_stats.add_argument(
        "--postgres-url",
        default=None,
        help="Postgres connection URL; falls back to OPENMINION_STORAGE_POSTGRES_URL",
    )

    reindex = sub.add_parser("reindex", help="Replay JSONL sidecars into sqlite")
    reindex.add_argument("--since-ts", default=None)
    reindex.add_argument(
        "--dry-run", action="store_true", help="Do not write to SQLite or ingest log"
    )
    reindex.add_argument(
        "--archive-replayed",
        action="store_true",
        help="Move processed sidecars into archive dir",
    )
    reindex.add_argument(
        "--archive-root", default=None, help="Override archive directory root"
    )
    reindex.add_argument(
        "--skip-fs",
        action="store_true",
        help="Skip filesystem scan (for future adapters)",
    )

    gc = sub.add_parser("gc", help="Run blob garbage collection")
    gc.add_argument("--plan", action="store_true")
    gc.add_argument("--max-age-days", type=int, default=-1)
    gc.add_argument("--max-total-bytes", type=int, default=-1)

    blob_verify = sub.add_parser(
        "blob-verify", help="Re-hash a blob and report mismatch"
    )
    blob_verify.add_argument("--hash", required=True)

    events = sub.add_parser("events", help="List recent events for a session")
    events.add_argument("--session-id", required=True)
    events.add_argument("--limit", type=int, default=50)

    plan = sub.add_parser("plan", help="Plan migrations for a module database")
    plan.add_argument("--namespace", required=True, help="Module id (example: session)")
    plan.add_argument("--sqlite", required=True, help="SQLite database path")

    migrate = sub.add_parser("migrate", help="Run module migrations")
    migrate.add_argument(
        "--namespace", required=True, help="Module id (example: session)"
    )
    migrate.add_argument("--sqlite", required=True, help="SQLite database path")

    backup = sub.add_parser("backup", help="Create a database snapshot")
    backup.add_argument(
        "--namespace", required=True, help="Module id (example: session)"
    )
    backup.add_argument("--sqlite", required=True, help="SQLite database path")
    backup.add_argument(
        "--snapshot-root", default=None, help="Snapshot directory override"
    )
    backup.add_argument("--mode", default=None, help="Backup mode override")

    restore = sub.add_parser("restore", help="Restore database from snapshot")
    restore.add_argument(
        "--namespace", required=True, help="Module id (example: session)"
    )
    restore.add_argument("--sqlite", required=True, help="SQLite database path")
    restore.add_argument(
        "--snapshot-path", required=True, help="Snapshot path to restore"
    )

    verify = sub.add_parser("verify", help="Verify database integrity")
    verify.add_argument(
        "--namespace", required=True, help="Module id (example: session)"
    )
    verify.add_argument("--sqlite", required=True, help="SQLite database path")
    verify.add_argument(
        "--level", default="quick", help="Verification level (default: quick)"
    )

    export = sub.add_parser(
        "export",
        help="Export database to OMX (full or partial via --since/--namespace-filter/--where)",
    )
    export.add_argument(
        "--namespace", required=True, help="Module id (example: session)"
    )
    export.add_argument("--sqlite", required=True, help="SQLite database path")
    export.add_argument("--out", required=True, help="Output directory for OMX bundle")
    export.add_argument("--notes", default=None, help="Optional export notes")
    export.add_argument(
        "--since",
        default=None,
        help="Partial export: only rows with updated_at/created_at >= ISO-8601 timestamp",
    )
    export.add_argument(
        "--namespace-filter",
        default=None,
        help="Partial export: only rows in this namespace (tables without namespace column are skipped)",
    )
    export.add_argument(
        "--where",
        default=None,
        help="Partial export: append a SQL WHERE clause (advanced; AND-ed with --since/--namespace-filter)",
    )
    export.add_argument(
        "--backend",
        choices=("sqlite", "postgres"),
        default="sqlite",
        help=(
            "Source backend for the export. 'sqlite' (default) reads from "
            "--sqlite. 'postgres' reads via RecordStorePostgres; requires "
            "--postgres-url or OPENMINION_STORAGE_POSTGRES_URL."
        ),
    )
    export.add_argument(
        "--postgres-url",
        default=None,
        help=(
            "Postgres connection URL for --backend postgres; falls back to "
            "OPENMINION_STORAGE_POSTGRES_URL."
        ),
    )

    a2a_archive_old = sub.add_parser(
        "a2a-archive-old",
        help=(
            "Promote daily a2a audit SQLite files older than N days into "
            "the Postgres a2a_audit_archive table. By default the SQLite "
            "files are deleted after a successful archive; pass "
            "--keep-files to retain them. Requires --postgres-url or "
            "OPENMINION_STORAGE_POSTGRES_URL."
        ),
    )
    a2a_archive_old.add_argument(
        "--older-than-days",
        type=int,
        required=True,
        help="Archive daily files whose date is at least N days old (>=1).",
    )
    a2a_archive_old.add_argument(
        "--audit-root",
        default=None,
        help=(
            "Directory containing YYYY-MM-DD.db daily audit files. "
            "Defaults to the configured data root's a2a audit directory."
        ),
    )
    a2a_archive_old.add_argument(
        "--keep-files",
        action="store_true",
        help="Retain archived SQLite files instead of deleting them.",
    )
    a2a_archive_old.add_argument(
        "--postgres-url",
        default=None,
        help=(
            "Postgres connection URL for the archive table; "
            "falls back to OPENMINION_STORAGE_POSTGRES_URL."
        ),
    )

    drill_parser = sub.add_parser(
        "dr-drill",
        help=(
            "Run a backup -> restore -> verify drill on a module DB. "
            "Restores into a temp file by default; use --restore-target to "
            "land the rehydrated copy at a specific path."
        ),
    )
    drill_parser.add_argument(
        "--namespace", required=True, help="Module id (example: session)"
    )
    drill_parser.add_argument("--sqlite", required=True, help="SQLite database path")
    drill_parser.add_argument(
        "--snapshot-root",
        default=None,
        help="Snapshot directory override (defaults to db_path.parent)",
    )
    drill_parser.add_argument(
        "--restore-target",
        default=None,
        help=(
            "Filesystem path for the restored DB. Default: a fresh file in a "
            "temp directory cleaned up after the drill."
        ),
    )
    drill_parser.add_argument(
        "--verify-level",
        default="full",
        help="Verification level for the post-restore check (default: full).",
    )

    import_cmd = sub.add_parser("import", help="Import OMX bundle into database")
    import_cmd.add_argument(
        "--namespace", required=True, help="Module id (example: session)"
    )
    import_cmd.add_argument("--sqlite", required=True, help="SQLite database path")
    import_cmd.add_argument("--input", required=True, help="Input OMX directory")
    import_cmd.add_argument(
        "--skip-checksum", action="store_true", help="Skip checksum verification"
    )
    import_cmd.add_argument(
        "--backend",
        choices=("sqlite", "postgres"),
        default="sqlite",
        help=(
            "Target backend for the import. 'sqlite' (default) writes to "
            "--sqlite. 'postgres' writes via RecordStorePostgres; requires "
            "--postgres-url or OPENMINION_STORAGE_POSTGRES_URL."
        ),
    )
    import_cmd.add_argument(
        "--postgres-url",
        default=None,
        help=(
            "Postgres connection URL for --backend postgres; falls back to "
            "OPENMINION_STORAGE_POSTGRES_URL."
        ),
    )

    return parser


def _print_json(payload: dict) -> None:
    print_json_payload(payload, sort_keys=False, ensure_ascii=True)


def _select_reporter(args: argparse.Namespace) -> ProgressReporter:
    if bool(getattr(args, "progress", False)):
        return select_default_reporter()
    return NullProgressReporter()


def _reporter_safe_start(reporter: ProgressReporter, *, label: str) -> None:
    try:
        reporter.on_start(total=None, label=label)
    except Exception:  # noqa: BLE001
        return


def _reporter_safe_end(
    reporter: ProgressReporter, *, success: bool, message: str | None = None
) -> None:
    try:
        reporter.on_end(success=success, message=message)
    except Exception:  # noqa: BLE001
        return


def _open_postgres_record_store(args: argparse.Namespace, env_map, *, verb: str):
    postgres_url = _resolve_postgres_url(args, env_map)
    if not postgres_url:
        _print_json(
            {
                "ok": False,
                "error": (
                    f"{verb} --backend postgres requires a Postgres URL. "
                    "Pass --postgres-url or set OPENMINION_STORAGE_POSTGRES_URL."
                ),
            }
        )
        return None, lambda: None

    try:
        from openminion.modules.storage.backends.postgres import (
            RecordStorePostgres,
        )
    except Exception as exc:  # noqa: BLE001
        _print_json({"ok": False, "error": f"postgres backend unavailable: {exc}"})
        return None, lambda: None

    store = RecordStorePostgres(postgres_url)

    def _close() -> None:
        try:
            store.close()
        except Exception:  # noqa: BLE001
            pass

    return store, _close


def _run_pool_stats(args: argparse.Namespace, env_map) -> int:
    postgres_url = _resolve_postgres_url(args, env_map)
    if not postgres_url:
        _print_json(
            {
                "ok": False,
                "error": (
                    "pool-stats requires a Postgres URL. Pass --postgres-url or set "
                    "OPENMINION_STORAGE_POSTGRES_URL. SQLite backends have no pool."
                ),
            }
        )
        return 2

    try:
        from openminion.modules.storage.backends.postgres import (
            RecordStorePostgres,
        )
    except Exception as exc:  # noqa: BLE001
        _print_json({"ok": False, "error": f"postgres backend unavailable: {exc}"})
        return 2

    store = RecordStorePostgres(postgres_url)
    try:
        store.healthcheck()
        stats = store.pool_health()
        _print_json({"ok": stats is not None, "pool": stats})
        return 0
    finally:
        try:
            store.close()
        except Exception:  # noqa: BLE001
            pass


def _run_drill(args: argparse.Namespace, *, sqlite_path: str) -> int:
    import tempfile
    from openminion.modules.storage.drill import run_drill

    module_id = _resolve_module_id(args.namespace)
    try:
        _ensure_module_identity(
            sqlite_path=sqlite_path,
            module_id=module_id,
            list_migrations=_load_module_migrations(module_id)[1],
        )
    except Exception as exc:  # noqa: BLE001
        _print_json({"ok": False, "error": f"identity setup failed: {exc}"})
        return 2

    runner = _build_runner(
        module_id=module_id,
        sqlite_path=sqlite_path,
        snapshot_root=getattr(args, "snapshot_root", None),
    )

    explicit_target = getattr(args, "restore_target", None)
    verify_level = str(getattr(args, "verify_level", "full") or "full")

    reporter = _select_reporter(args)
    _reporter_safe_start(reporter, label=f"dr-drill[{module_id}]")

    try:
        if explicit_target:
            report = run_drill(
                runner=runner,
                target_path=Path(explicit_target),
                verify_level=verify_level,
            )
            _reporter_safe_end(reporter, success=report.ok, message=report.error)
            _print_json({"ok": report.ok, "report": report.to_dict()})
            return 0 if report.ok else 1

        with tempfile.TemporaryDirectory(prefix="om_storage_drill_") as tmpdir:
            target = Path(tmpdir) / f"{module_id}.restored.db"
            report = run_drill(
                runner=runner,
                target_path=target,
                verify_level=verify_level,
            )
            _reporter_safe_end(reporter, success=report.ok, message=report.error)
            _print_json({"ok": report.ok, "report": report.to_dict()})
            return 0 if report.ok else 1
    except Exception:
        _reporter_safe_end(reporter, success=False)
        raise


def _run_a2a_archive_old(args: argparse.Namespace, env_map) -> int:
    older_than_days = int(getattr(args, "older_than_days", 0) or 0)
    if older_than_days < 1:
        _print_json(
            {
                "ok": False,
                "error": "a2a-archive-old requires --older-than-days >= 1.",
            }
        )
        return 2

    postgres_url = _resolve_postgres_url(args, env_map)
    if not postgres_url:
        _print_json(
            {
                "ok": False,
                "error": (
                    "a2a-archive-old requires a Postgres URL. Pass --postgres-url "
                    "or set OPENMINION_STORAGE_POSTGRES_URL."
                ),
            }
        )
        return 2

    try:
        from sqlalchemy import create_engine
    except Exception as exc:  # noqa: BLE001
        _print_json({"ok": False, "error": f"sqlalchemy unavailable: {exc}"})
        return 2

    try:
        from openminion.modules.storage.backends.postgres import (
            RecordStorePostgres,
        )
    except Exception as exc:  # noqa: BLE001
        _print_json({"ok": False, "error": f"postgres backend unavailable: {exc}"})
        return 2

    from openminion.modules.a2a.storage.archive import (
        PostgresAuditArchiveStore,
    )

    audit_root_arg = str(getattr(args, "audit_root", "") or "").strip()
    if audit_root_arg:
        audit_root = Path(audit_root_arg).expanduser().resolve(strict=False)
    else:
        resolved_home_root = resolve_module_home_root(
            None,
            env_map,
            fallback_to_cwd=True,
        )
        resolved_data_root = resolve_module_data_root(
            home_root=resolved_home_root,
            env=env_map,
        )
        audit_root = (resolved_data_root / "a2a" / "audit").resolve()

    engine = create_engine(postgres_url, future=True)
    record_store = RecordStorePostgres(engine)
    keep_files = bool(getattr(args, "keep_files", False))

    archive_store = PostgresAuditArchiveStore(
        record_store=record_store,
        audit_root=audit_root,
        engine=engine,
        owns_engine=False,
    )
    try:
        report = archive_store.archive_files_older_than(
            older_than_days, keep_files=keep_files
        )
        _print_json({"ok": True, "report": report.to_dict()})
        return 0
    finally:
        try:
            archive_store.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            record_store.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            engine.dispose()
        except Exception:  # noqa: BLE001
            pass


def _resolve_postgres_url(args: argparse.Namespace, env_map) -> str:
    explicit = getattr(args, "postgres_url", None)
    env_value = env_map.get("OPENMINION_STORAGE_POSTGRES_URL", "")
    return str(explicit or env_value or "").strip()


def _resolve_module_id(raw: str | None) -> str:
    module_id = str(raw or "").strip().lower()
    if not module_id:
        raise ValueError("module_id/namespace is required")
    return module_id


def _load_module_migrations(module_id: str):
    module = importlib.import_module(
        f"openminion.modules.{module_id}.storage.migrations"
    )
    run_migrations = getattr(module, "run_migrations")
    list_migrations = getattr(module, "list_migrations")
    return run_migrations, list_migrations


def _ensure_module_identity(
    *, sqlite_path: str, module_id: str, list_migrations
) -> None:
    path = Path(sqlite_path).expanduser().resolve(strict=False)
    if not path.exists():
        return
    schema_head = schema_head_from_migrations(list_migrations())
    with sqlite3.connect(str(path)) as conn:
        ensure_module_metadata(
            conn,
            module_id=module_id,
            module_application_id=get_module_application_id(module_id),
            schema_head=schema_head,
        )


def _build_runner(
    *, module_id: str, sqlite_path: str, snapshot_root: str | None = None
) -> MigrationRunner:
    return MigrationRunner(
        module_id=module_id,
        db_path=sqlite_path,
        module_application_id=get_module_application_id(module_id),
        snapshot_root=snapshot_root,
    )


if __name__ == "__main__":
    raise SystemExit(main())
