from __future__ import annotations

import argparse

from openminion.cli.commands.cron import register_schedule_alias


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    register_schedule_alias(subparsers)


__all__ = ["register"]
