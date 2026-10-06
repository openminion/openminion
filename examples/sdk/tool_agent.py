"""Run a tool-using Agent with a verifiable first tool call."""

from __future__ import annotations

import argparse

from openminion import Agent, ProviderError, tool


@tool
def lookup_order(order_id: str) -> str:
    """Return the current status for one order."""

    return f"order {order_id}: ready"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("order_id", nargs="?", default="A-104")
    parser.add_argument("--agent", default=None, help="Configured agent id")
    parser.add_argument(
        "--model-choice",
        action="store_true",
        help="Let the model decide whether to call the tool",
    )
    args = parser.parse_args()

    forced_tools = [] if args.model_choice else ["lookup_order"]
    try:
        with Agent(
            agent_id=args.agent,
            instructions="Use lookup_order and answer in one sentence.",
            tools=[lookup_order],
            forced_tools=forced_tools,
            session_id="sdk-tool-example",
        ) as agent:
            result = agent.run(f"Check order {args.order_id}")
    except ProviderError as exc:
        print(f"provider failed: {exc.code}: {exc.message}")
        return 1

    print(result.text)
    print(f"session={result.session_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
