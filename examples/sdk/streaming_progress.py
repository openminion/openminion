"""Observe progress events while running one synchronous Agent turn."""

from __future__ import annotations

import argparse
from typing import Any

from openminion import Agent


def print_event(event: dict[str, Any]) -> None:
    kind = event.get("kind") or event.get("type") or "progress"
    print(f"[{kind}] {event}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", nargs="?", default="Explain MCP briefly.")
    parser.add_argument("--agent", default=None, help="Configured agent id")
    args = parser.parse_args()

    with Agent(agent_id=args.agent) as agent:
        result = agent.run_stream(args.prompt, on_delta=print_event)

    print(f"final: {result.text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
