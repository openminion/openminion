"""Share one application-owned APIRuntime across Agent calls."""

from __future__ import annotations

import argparse

from openminion import APIRuntime, Agent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", nargs="?", default="Explain MCP briefly.")
    parser.add_argument("--config", default=None, help="Path to agents.json")
    parser.add_argument("--agent", default=None, help="Configured agent id")
    args = parser.parse_args()

    runtime = APIRuntime.from_config_path(args.config)
    try:
        with Agent(runtime=runtime, agent_id=args.agent) as agent:
            result = agent.run(args.prompt)
        print(result.text)
    finally:
        runtime.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
