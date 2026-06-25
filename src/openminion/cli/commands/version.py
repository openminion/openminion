from __future__ import annotations

import argparse

from openminion import __version__
from openminion.cli.presentation.json_output import print_json_payload


def run_version(args: argparse.Namespace) -> int:
    if bool(getattr(args, "json", False)):
        print_json_payload({"package": "openminion", "version": __version__})
        return 0
    print(__version__)
    return 0


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    version = subparsers.add_parser("version", help="Show package version")
    version.add_argument(
        "--json",
        action="store_true",
        help="Print version payload as JSON",
    )
    version.set_defaults(handler=run_version, needs_app=False)
