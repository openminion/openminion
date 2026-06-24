import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, TextIO

from openminion.base.config import resolve_data_root, resolve_home_root
from openminion.base.config.env import resolve_environment_config
from openminion.base.constants import OPENMINION_DATA_ROOT_ENV, OPENMINION_HOME_ENV

HOME_ROOT_OPTION_HELP = "OpenMinion Home for generated state (anchors .openminion/)."
DATA_ROOT_OPTION_HELP = (
    "Centralized data root for module outputs (overrides OPENMINION_DATA_ROOT). "
    "Enforced under data_root unless OPENMINION_DATA_ROOT_ENFORCEMENT=soft."
)


def _normalize_optional_path(value: Path | str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def add_common_module_root_args(parser: argparse.ArgumentParser) -> None:
    """Add the standard `--home-root` and `--data-root` argparse args."""
    parser.add_argument(
        "--home-root",
        default=None,
        help=HOME_ROOT_OPTION_HELP,
    )
    parser.add_argument(
        "--data-root",
        default=None,
        help=DATA_ROOT_OPTION_HELP,
    )


def has_tty() -> bool:
    stdin_tty = bool(getattr(sys.stdin, "isatty", lambda: False)())
    stdout_tty = bool(getattr(sys.stdout, "isatty", lambda: False)())
    return stdin_tty and stdout_tty


def resolve_module_cli_db_path(
    args: argparse.Namespace,
    sqlite_subpath: str | Path,
) -> Path:
    """Canonical helper for resolving the module SQLite db path from CLI args.

    Checks args.db first (explicit override), then falls back to
    OPENMINION_DATA_ROOT env / home root resolution.
    """
    if getattr(args, "db", None):
        return Path(str(args.db)).expanduser().resolve(strict=False)
    home_root = resolve_home_root()
    data_root = resolve_data_root(
        home_root, data_root=resolve_environment_config().get(OPENMINION_DATA_ROOT_ENV)
    )
    return (data_root / sqlite_subpath).resolve()


def apply_home_data_root_env(
    *,
    home_root: Path | str | None = None,
    data_root: Path | str | None = None,
) -> tuple[str | None, str | None]:
    normalized_home = _normalize_optional_path(home_root)
    normalized_data = _normalize_optional_path(data_root)
    process_env = os.environ
    if normalized_home is not None:
        process_env.update({OPENMINION_HOME_ENV: normalized_home})
    if normalized_data is not None:
        process_env.update({OPENMINION_DATA_ROOT_ENV: normalized_data})
    return normalized_home, normalized_data


def print_json_payload(
    payload: Any,
    *,
    indent: int | None = 2,
    sort_keys: bool = True,
    default: Any | None = None,
    ensure_ascii: bool = False,
    stream: TextIO | None = None,
) -> None:
    """Emit canonical pretty JSON for argparse-based module CLIs."""

    target = sys.stdout if stream is None else stream
    target.write(
        json.dumps(
            payload,
            indent=indent,
            sort_keys=sort_keys,
            default=default,
            ensure_ascii=ensure_ascii,
        )
    )
    target.write("\n")
