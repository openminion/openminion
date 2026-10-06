"""Demonstrate shared and isolated session continuity."""

from __future__ import annotations

import argparse

from openminion import Agent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", default=None, help="Configured agent id")
    parser.add_argument("--session", default="sdk-shared-session")
    args = parser.parse_args()

    with Agent(agent_id=args.agent, session_id=args.session) as agent:
        first = agent.run("Remember that the launch color is teal.")
        second = agent.run("What is the launch color?")
        isolated = agent.run(
            "What is the launch color?",
            session_id="sdk-isolated-session",
        )

    print(f"shared first: {first.session_id}: {first.text}")
    print(f"shared second: {second.session_id}: {second.text}")
    print(f"isolated: {isolated.session_id}: {isolated.text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
