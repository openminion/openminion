"""Run one Agent turn with the configured OpenMinion runtime."""

from __future__ import annotations

import sys

from openminion import Agent, __version__, tool


@tool
def hello_tool(name: str = "world") -> str:
    """Return a short greeting."""

    return f"hello {name.strip() or 'world'}"


def main() -> int:
    prompt = " ".join(sys.argv[1:]).strip() or "Say hello in one short sentence."
    print(f"[openminion {__version__}] quickstart turn")
    print(f"  prompt: {prompt}")

    with Agent(tools=[hello_tool]) as agent:
        result = agent.run(prompt)
        print(f"  reply: {result.text}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
