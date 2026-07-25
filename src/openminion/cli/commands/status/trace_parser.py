from __future__ import annotations

from openminion.cli.parser.flags import add_json_output_flag


def register_status_context_trace_subcommand(status_subcommands, *, handler) -> None:
    parser = status_subcommands.add_parser(
        "context-trace",
        help="Inspect persisted context decision traces for a session",
    )
    parser.add_argument(
        "--session",
        "--session-id",
        dest="session_id",
        required=True,
        help="Session identifier",
    )
    parser.add_argument(
        "--turn",
        "--turn-id",
        dest="turn_id",
        default="",
        help="Optional turn / LLM-call identifier",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum traces to return (default: 50)",
    )
    parser.add_argument(
        "--review",
        action="store_true",
        help="Render a memory/context review summary from typed trace evidence",
    )
    parser.add_argument(
        "--canary",
        default="",
        help="Optional memory-context-operational-canary.v1 artifact path",
    )
    parser.add_argument(
        "--calibration",
        default="",
        help="Optional context-budget-calibration.v1 artifact path",
    )
    add_json_output_flag(parser)
    parser.set_defaults(handler=handler, needs_app=False)
