"""Run one APIRuntime turn with the configured agent."""

from __future__ import annotations

import sys

from openminion import APIRuntime, __version__


def main() -> int:
    prompt = " ".join(sys.argv[1:]).strip() or "Say hello in one short sentence."
    print(f"[openminion {__version__}] quickstart turn")
    print(f"  prompt: {prompt}")

    runtime = APIRuntime.from_config_path(None)
    try:
        result = runtime.run_turn(payload={"message": prompt, "deliver": False})
        print(f"  reply: {result['body']}")
        return 0
    finally:
        runtime.close()


if __name__ == "__main__":
    raise SystemExit(main())
