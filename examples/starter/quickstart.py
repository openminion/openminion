"""Run one APIRuntime turn from the command line."""

from __future__ import annotations

import sys

from openminion import APIRuntime, __version__


def main() -> int:
    prompt = " ".join(sys.argv[1:]).strip() or "Say hello in one short sentence."
    print(f"[openminion {__version__}] quickstart turn")
    print(f"  prompt: {prompt}")

    runtime = APIRuntime.from_config_path(None)
    try:
        result = runtime.run_turn(payload={"message": prompt})
        reply = result
        if isinstance(result, dict):
            reply = (
                result.get("body")
                or result.get("text")
                or result.get("reply")
                or result
            )
        print(f"  reply: {reply}")
        return 0
    finally:
        close = getattr(runtime, "close", None)
        if callable(close):
            close()


if __name__ == "__main__":
    raise SystemExit(main())
