"""Request and validate a typed result from an Agent."""

from __future__ import annotations

import argparse

from pydantic import BaseModel

from openminion import Agent, AgentOutputValidationError, ProviderError


class Review(BaseModel):
    decision: str
    reason: str


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "prompt",
        nargs="?",
        default="Approve a demo with passing tests.",
    )
    parser.add_argument("--agent", default=None, help="Configured agent id")
    args = parser.parse_args()

    try:
        with Agent(
            agent_id=args.agent,
            instructions=("Return JSON with string fields named decision and reason."),
            output_type=Review,
        ) as agent:
            result = agent.run(args.prompt)
    except AgentOutputValidationError as exc:
        print(f"invalid model output: {exc.raw_text}")
        return 2
    except ProviderError as exc:
        print(f"provider failed: {exc.code}: {exc.message}")
        return 1

    print(result.output.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
