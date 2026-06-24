import argparse
from pathlib import Path

from openminion.modules.storage.cli import main as storage_main


def add_storage_subcommands(subparsers: argparse._SubParsersAction) -> None:
    storage = subparsers.add_parser(
        "storage",
        help="Storage maintenance commands (backup/restore/export/import/verify/status)",
    )
    storage.add_argument("--db", default=None, help="SQLite database path override")
    storage.add_argument("--root", default=None, help="Blob root override")
    storage.add_argument(
        "--fallback", default=None, help="Fallback sidecar root override"
    )
    storage.add_argument(
        "--json", action="store_true", help="Print machine-readable output"
    )

    storage_sub = storage.add_subparsers(dest="storage_command", required=True)

    storage_sub.add_parser("status", help="Show storage status")
    storage_sub.add_parser("plan", help="Plan migrations")
    storage_sub.add_parser("migrate", help="Run migrations")

    backup = storage_sub.add_parser("backup", help="Create a database snapshot")
    backup.add_argument("--snapshot-root", default=None)
    backup.add_argument("--mode", default=None)

    restore = storage_sub.add_parser("restore", help="Restore database snapshot")
    restore.add_argument("--snapshot-path", required=True)

    verify = storage_sub.add_parser("verify", help="Verify database integrity")
    verify.add_argument("--level", default="quick")

    export = storage_sub.add_parser("export", help="Export database to OMX")
    export.add_argument("--out", required=True)
    export.add_argument("--notes", default=None)

    import_cmd = storage_sub.add_parser(
        "import", help="Import OMX bundle into database"
    )
    import_cmd.add_argument("--input", required=True)
    import_cmd.add_argument("--skip-checksum", action="store_true")


def build_storage_argv(
    *,
    module_id: str,
    db_path: Path,
    command: str,
    home_root: str | None = None,
    data_root: str | None = None,
    root: str | None = None,
    fallback: str | None = None,
    snapshot_root: str | None = None,
    snapshot_path: str | None = None,
    mode: str | None = None,
    level: str | None = None,
    out: str | None = None,
    notes: str | None = None,
    input_dir: str | None = None,
    skip_checksum: bool = False,
) -> list[str]:
    argv: list[str] = []
    if home_root:
        argv.extend(["--home-root", str(home_root)])
    if data_root:
        argv.extend(["--data-root", str(data_root)])

    argv.extend(["--namespace", module_id, "--sqlite", str(db_path)])

    if root:
        argv.extend(["--root", root])
    if fallback:
        argv.extend(["--fallback", fallback])

    argv.append(command)

    if command == "backup":
        if snapshot_root:
            argv.extend(["--snapshot-root", snapshot_root])
        if mode:
            argv.extend(["--mode", mode])
    elif command == "restore":
        if not snapshot_path:
            raise RuntimeError("restore requires snapshot_path")
        argv.extend(["--snapshot-path", snapshot_path])
    elif command == "verify":
        if level:
            argv.extend(["--level", level])
    elif command == "export":
        if not out:
            raise RuntimeError("export requires out")
        argv.extend(["--out", out])
        if notes:
            argv.extend(["--notes", notes])
    elif command == "import":
        if not input_dir:
            raise RuntimeError("import requires input_dir")
        argv.extend(["--input", input_dir])
        if skip_checksum:
            argv.append("--skip-checksum")

    return argv


def run_module_storage_command(
    *,
    args: argparse.Namespace,
    module_id: str,
    db_path: Path,
    home_root: str | None = None,
    data_root: str | None = None,
) -> int:
    command = str(getattr(args, "storage_command", "") or "").strip()
    if not command:
        raise RuntimeError("storage_command is required")
    argv = build_storage_argv(
        module_id=module_id,
        db_path=db_path,
        command=command,
        home_root=home_root,
        data_root=data_root,
        root=str(getattr(args, "root", "") or "").strip() or None,
        fallback=str(getattr(args, "fallback", "") or "").strip() or None,
        snapshot_root=str(getattr(args, "snapshot_root", "") or "").strip() or None,
        mode=str(getattr(args, "mode", "") or "").strip() or None,
        level=str(getattr(args, "level", "") or "").strip() or None,
        out=str(getattr(args, "out", "") or "").strip() or None,
        notes=str(getattr(args, "notes", "") or "").strip() or None,
        input_dir=str(getattr(args, "input", "") or "").strip() or None,
        snapshot_path=str(getattr(args, "snapshot_path", "") or "").strip() or None,
        skip_checksum=bool(getattr(args, "skip_checksum", False)),
    )
    storage_main(argv)
    return 0


def run_storage_argv(argv: list[str]) -> int:
    storage_main(argv)
    return 0
