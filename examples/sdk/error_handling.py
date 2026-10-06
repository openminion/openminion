"""Handle provider and structured-output failures explicitly."""

from __future__ import annotations

import argparse

from pydantic import BaseModel

from openminion import Agent, AgentOutputValidationError, ProviderError


class Answer(BaseModel):
    answer: str
    confidence: float


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", nargs="?", default="What is OpenMinion?")
    parser.add_argument("--agent", default=None, help="Configured agent id")
    args = parser.parse_args()

    try:
        with Agent(
            agent_id=args.agent,
            instructions=(
                "Return JSON with answer as text and confidence from 0 to 1."
            ),
            output_type=Answer,
        ) as agent:
            result = agent.run(args.prompt)
    except ProviderError as exc:
        print(f"provider failed: {exc.code}: {exc.message}")
        if exc.details:
            print(f"details: {exc.details}")
        return 1
    except AgentOutputValidationError as exc:
        print(f"invalid output: {exc.raw_text}")
        return 2

    print(result.output.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
