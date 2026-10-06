"""Run the same Agent program through any configured agent profile."""

from __future__ import annotations

import argparse

from openminion import Agent, ProviderError


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", nargs="?", default="Introduce yourself briefly.")
    parser.add_argument("--agent", required=True, help="Configured agent id")
    parser.add_argument("--config", default=None, help="Path to agents.json")
    args = parser.parse_args()

    try:
        with Agent(config_path=args.config, agent_id=args.agent) as agent:
            result = agent.run(args.prompt)
    except ProviderError as exc:
        print(f"provider failed: {exc.code}: {exc.message}")
        return 1

    print(f"agent={args.agent} session={result.session_id}")
    print(result.text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
