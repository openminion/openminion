"""Stream one submitted turn through the public runtime handle."""

from __future__ import annotations

import argparse

from openminion.api import APIRuntime, TurnChunk, TurnResponse


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", nargs="?", default="Explain MCP briefly.")
    parser.add_argument("--config", default=None, help="Path to agents.json")
    parser.add_argument("--agent", default=None, help="Configured agent id")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument(
        "--approval",
        choices=("deny", "allow_once"),
        default="deny",
        help="Decision for any streamed approval request",
    )
    args = parser.parse_args()

    payload: dict[str, object] = {
        "message": args.prompt,
        "session_id": "sdk-submitted-stream",
        "timeout_seconds": args.timeout,
        "deliver": False,
    }
    if args.agent:
        payload["agent_id"] = args.agent

    runtime = APIRuntime.from_config_path(args.config)
    try:
        handle = runtime.submit_turn(payload=payload)
        for chunk in handle.stream():
            _print_chunk(chunk)
            if chunk.kind == "approval" and chunk.data.get("phase") == "requested":
                handle.resolve_approval(
                    approval_id=str(chunk.data["approval_id"]),
                    decision=args.approval,
                )
        response: TurnResponse = handle.result(timeout_s=args.timeout)
        print(f"final: {response.final_text}")
    finally:
        runtime.close()
    return 0


def _print_chunk(chunk: TurnChunk) -> None:
    print(f"[{chunk.kind}] sequence={chunk.sequence}")


if __name__ == "__main__":
    raise SystemExit(main())
