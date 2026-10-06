"""Run a bounded subagent or expose a peer Agent as a handoff."""

from __future__ import annotations

import argparse

from openminion import Agent, Handoff, subagent


def run_subagent(prompt: str, agent_id: str | None) -> str:
    with Agent(name="coordinator", agent_id=agent_id) as parent:
        reviewer = subagent(
            parent,
            name="reviewer",
            instructions="Review the request for factual gaps in two sentences.",
            timeout_seconds=120,
        )
        return reviewer.run(prompt).text


def run_handoff(prompt: str, agent_id: str | None) -> str:
    with Agent(
        name="researcher",
        agent_id=agent_id,
        instructions="Research one bounded question and return concise findings.",
    ) as researcher:
        with Agent(
            name="coordinator",
            agent_id=agent_id,
            instructions="Delegate research questions to the researcher.",
            handoffs=[Handoff(target=researcher)],
            forced_tools=["transfer_to_researcher"],
        ) as coordinator:
            return coordinator.run(prompt).text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", nargs="?", default="Review this launch plan.")
    parser.add_argument("--agent", default=None, help="Configured agent id")
    parser.add_argument(
        "--mode",
        choices=("subagent", "handoff"),
        default="subagent",
    )
    args = parser.parse_args()

    if args.mode == "handoff":
        print(run_handoff(args.prompt, args.agent))
    else:
        print(run_subagent(args.prompt, args.agent))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
