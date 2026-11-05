"""Run a bounded subagent or expose a peer Agent as a handoff."""

from __future__ import annotations

import argparse
from typing import Any

from openminion import Agent, AgentRunResult, Handoff, subagent

HANDOFF_TOOL = "transfer_to_researcher"


def run_subagent(prompt: str, agent_id: str | None) -> str:
    with Agent(name="coordinator", agent_id=agent_id) as parent:
        reviewer = subagent(
            parent,
            name="reviewer",
            instructions="Review the request for factual gaps in two sentences.",
            timeout_seconds=120,
        )
        return reviewer.run(prompt).text


def operator_decision(approval: str):
    """Decide only for the researcher handoff.

    Any other tool is denied, an allowance is consumed once, and every
    denial is returned before the target runs.
    """

    allowed = approval == "allow_once"
    consumed = False

    def decide(tool_name: str, args: dict[str, Any], approval_id: str) -> bool:
        nonlocal consumed
        if tool_name != HANDOFF_TOOL:
            print(f"operator: denying confirmation for unrequested tool {tool_name}")
            return False
        message = str(args.get("message", ""))
        if not allowed:
            print(f"operator: denying handoff of {message!r}")
            return False
        if consumed:
            print(f"operator: allowance already used; denying {approval_id}")
            return False
        consumed = True
        print(f"operator: allowing handoff of {message!r}")
        return True

    return decide


def run_handoff(
    prompt: str, agent_id: str | None, approval: str
) -> AgentRunResult[str]:
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
            forced_tools=[HANDOFF_TOOL],
        ) as coordinator:
            return coordinator.run(
                prompt,
                approval_callback=(
                    None if approval == "pause" else operator_decision(approval)
                ),
            )


def _print_handoff(result: AgentRunResult[str]) -> int:
    if result.metadata.get("respond_kind") == "policy_confirmation_prompt":
        print("paused: the policy asks the operator before the handoff runs.")
        print("The text below is that operator prompt, not the researcher's answer:")
        print(result.text)
        print("This script ends here. An application that keeps its Agent open")
        print("answers the same prompt on the waiting thread, for example")
        print(f'agent.run("yes", thread_id={result.thread_id!r}).')
        return 0
    print(result.text)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", nargs="?", default="Review this launch plan.")
    parser.add_argument("--agent", default=None, help="Configured agent id")
    parser.add_argument(
        "--mode",
        choices=("subagent", "handoff"),
        default="subagent",
    )
    parser.add_argument(
        "--approval",
        choices=("pause", "allow_once", "deny"),
        default="pause",
        help="Operator decision for the handoff confirmation (handoff mode only)",
    )
    args = parser.parse_args()

    if args.mode == "handoff":
        return _print_handoff(run_handoff(args.prompt, args.agent, args.approval))
    print(run_subagent(args.prompt, args.agent))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
