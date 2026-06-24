import argparse

from openminion.modules.cli_common import (
    add_common_module_root_args,
    apply_home_data_root_env,
    resolve_module_cli_db_path,
)
from openminion.modules.storage.module_cli import (
    add_storage_subcommands,
    run_module_storage_command,
)
from .constants import DEFAULT_INTEGRATED_SQLITE_SUBPATH


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="taskctl",
        description="openminion-task standalone CLI",
    )
    add_common_module_root_args(parser)
    sub = parser.add_subparsers(dest="command", required=True)
    add_storage_subcommands(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    home_root = str(getattr(args, "home_root", "") or "").strip()
    data_root = str(getattr(args, "data_root", "") or "").strip()
    apply_home_data_root_env(home_root=home_root, data_root=data_root)

    if args.command != "storage":
        raise SystemExit("taskctl only supports storage operations in V1")

    db_path = resolve_module_cli_db_path(args, DEFAULT_INTEGRATED_SQLITE_SUBPATH)
    return run_module_storage_command(
        args=args,
        module_id="task",
        db_path=db_path,
        home_root=home_root or None,
        data_root=data_root or None,
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
