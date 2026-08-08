from __future__ import annotations

import argparse
import asyncio
from typing import Any


def run_acp(_args: Any, app: Any) -> int:
    try:
        from openminion.api.operations.acp import run_local_acp_agent
    except ImportError as exc:
        raise RuntimeError(
            "ACP support is not installed. Install OpenMinion with the 'acp' extra."
        ) from exc

    try:
        asyncio.run(run_local_acp_agent(app))
    finally:
        app.close()
    return 0


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("acp", help="Run the local ACP agent")
    parser.set_defaults(handler=run_acp, needs_app=True)
