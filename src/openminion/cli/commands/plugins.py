from __future__ import annotations

import argparse
from typing import Any


def list_plugins(_args: Any, app: Any) -> int:
    names = app.plugins.names()
    if not names:
        print("No plugins enabled")
        return 0

    for name in names:
        print(name)
    return 0


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    plugins = subparsers.add_parser("plugins", help="Plugin operations")
    plugins_subcommands = plugins.add_subparsers(dest="plugins_command")
    plugins_list = plugins_subcommands.add_parser("list", help="List enabled plugins")
    plugins_list.set_defaults(handler=list_plugins, needs_app=True)
