from __future__ import annotations

import argparse
import sys

from openminion.cli.config import load_cli_config, resolve_cli_roots
from openminion.cli.parser.flags import add_json_output_flag
from openminion.cli.presentation.json_output import print_json_payload
from openminion.modules.context.knowledge import KnowledgeGraphError
from openminion.modules.context.knowledge.viewer import (
    GraphViewerRequest,
    inspect_graph_viewer_status,
    launch_graph_viewer,
)


def run_graph(args: argparse.Namespace) -> int:
    try:
        if args.graph_command == "status":
            return _run_graph_status(args)
        if args.graph_command != "view":
            raise RuntimeError("Unknown graph command")
        return _run_graph_view(args)
    except KnowledgeGraphError as exc:
        return _handle_graph_error(exc, as_json=bool(getattr(args, "json", False)))


def _run_graph_view(args: argparse.Namespace) -> int:
    roots = resolve_cli_roots(
        config_path=getattr(args, "config", None),
        home_root=getattr(args, "home_root", None),
        data_root=getattr(args, "data_root", None),
    )
    config = load_cli_config(
        getattr(args, "config", None),
        home_root=roots.home_root,
        data_root=roots.data_root,
    )
    result = launch_graph_viewer(
        config=config,
        roots=roots,
        request=GraphViewerRequest(
            brain=args.brain,
            provider=args.provider or "",
            screen=args.screen,
            query=args.query or "",
            focus_node_id=args.focus_node_id or "",
            source_node_id=args.source_node_id or "",
            target_node_id=args.target_node_id or "",
            max_depth=args.max_depth,
            limit=args.limit,
            render_limit=args.render_limit,
            render_engine=args.render_engine,
            theme=args.theme,
            layout=args.layout,
            host=args.host,
            port=args.port,
            open_browser=not bool(args.no_open),
            dry_run=bool(args.dry_run),
            html_out=args.html_out or "",
            memory_db=args.memory_db or "",
        ),
    )
    return _print_success(result.to_dict(), as_json=bool(getattr(args, "json", False)))


def _run_graph_status(args: argparse.Namespace) -> int:
    roots = resolve_cli_roots(
        config_path=getattr(args, "config", None),
        home_root=getattr(args, "home_root", None),
        data_root=getattr(args, "data_root", None),
    )
    config = load_cli_config(
        getattr(args, "config", None),
        home_root=roots.home_root,
        data_root=roots.data_root,
    )
    report = inspect_graph_viewer_status(
        config=config,
        roots=roots,
        provider=args.provider or "",
        memory_db=args.memory_db or "",
    )
    return _print_success(report.to_dict(), as_json=bool(getattr(args, "json", False)))


def _print_success(payload: dict[str, object], *, as_json: bool) -> int:
    if as_json:
        print_json_payload(payload)
        return 0
    if "graphfakos" in payload:
        _print_status_result(payload)
    else:
        _print_view_result(payload)
    return 0


def _handle_graph_error(exc: KnowledgeGraphError, *, as_json: bool) -> int:
    payload = {"ok": False, **exc.to_dict()}
    if as_json:
        print_json_payload(payload, stream=sys.stderr)
    else:
        print(f"Graph viewer unavailable: {exc.message}", file=sys.stderr)
        details = dict(exc.details)
        suggestions = details.get("suggested_commands") or details.get("suggested_command")
        if isinstance(suggestions, str) and suggestions:
            print(f"Next: {suggestions}", file=sys.stderr)
        elif isinstance(suggestions, list):
            for command in suggestions:
                if str(command).strip():
                    print(f"Next: {command}", file=sys.stderr)
        else:
            print("Next: openminion graph status", file=sys.stderr)
    return 2


def _print_view_result(payload: dict[str, object]) -> None:
    mode = payload.get("mode")
    if mode == "server":
        print(f"Graph viewer: {payload.get('url')}")
        return
    if mode == "static_html":
        print(f"Graph viewer HTML: {payload.get('html_path')}")
        return
    print(
        "Graph viewer dry run: "
        f"provider={payload.get('provider')} "
        f"role={payload.get('graph_role')} "
        f"diagnostics={payload.get('diagnostics')}"
    )


def _print_status_result(payload: dict[str, object]) -> None:
    graphfakos = _dict_payload(payload.get("graphfakos"))
    installed = "yes" if graphfakos.get("installed") else "no"
    version = str(graphfakos.get("version") or "")
    suffix = f" ({version})" if version else ""
    print(f"GraphFakos installed: {installed}{suffix}")
    _print_provider_status("Second brain", _dict_payload(payload.get("second_brain")))
    third = payload.get("third_brain")
    if isinstance(third, list) and third:
        print("Third brain providers:")
        for provider in third:
            _print_provider_status("  -", _dict_payload(provider), compact=True)
    else:
        print("Third brain providers: none configured")
    commands = payload.get("next_commands")
    if isinstance(commands, list) and commands:
        print("Next commands:")
        for command in commands:
            print(f"  {command}")


def _print_provider_status(
    label: str,
    payload: dict[str, object],
    *,
    compact: bool = False,
) -> None:
    provider = str(payload.get("provider") or "")
    ready = "ready" if payload.get("visual_ready") else "needs setup"
    active = "active" if payload.get("active") else "inactive"
    adapter = str(payload.get("adapter") or "")
    prefix = f"{label} " if compact else f"{label}: "
    print(f"{prefix}{provider} [{ready}, {active}, {adapter}]")
    reason = str(payload.get("reason") or "")
    if reason:
        print(f"    {reason}")


def _dict_payload(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, dict) else {}


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    graph = subparsers.add_parser("graph", help="Visual graph inspection")
    graph_subcommands = graph.add_subparsers(dest="graph_command", required=True)
    status = graph_subcommands.add_parser(
        "status",
        help="Check graph viewer readiness",
    )
    status.add_argument(
        "--provider",
        default="",
        help="Limit third-brain status to one provider.",
    )
    status.add_argument(
        "--memory-db",
        default="",
        help=(
            "Second-brain SQLite memory database path. Defaults to "
            "data_root/memory/memory.db."
        ),
    )
    add_json_output_flag(status)
    status.set_defaults(handler=run_graph, needs_app=False)

    view = graph_subcommands.add_parser(
        "view",
        help="Open the current second- or third-brain graph in GraphFakos",
    )
    view.add_argument(
        "--brain",
        choices=("second", "third"),
        default="third",
        help="Graph layer to inspect. second=memory, third=provider graph context.",
    )
    view.add_argument(
        "--provider",
        default="",
        help="Third-brain provider name when more than one provider is active.",
    )
    view.add_argument("--screen", default="explore")
    view.add_argument("--query", default="")
    view.add_argument("--focus-node-id", default="")
    view.add_argument("--source-node-id", default="")
    view.add_argument("--target-node-id", default="")
    view.add_argument("--max-depth", type=int, default=1)
    view.add_argument("--limit", type=int, default=100)
    view.add_argument("--render-limit", type=int, default=240)
    view.add_argument("--render-engine", default="svg")
    view.add_argument("--theme", default="default")
    view.add_argument("--layout", default="force")
    view.add_argument("--host", default="127.0.0.1")
    view.add_argument("--port", type=int, default=8767)
    view.add_argument(
        "--no-open",
        action="store_true",
        help="Serve without opening a browser.",
    )
    view.add_argument(
        "--dry-run",
        action="store_true",
        help="Build the graph and print counts without starting the viewer.",
    )
    view.add_argument(
        "--html-out",
        default="",
        help="Write a static viewer HTML file instead of starting the local server.",
    )
    view.add_argument(
        "--memory-db",
        default="",
        help=(
            "Second-brain SQLite memory database path. Defaults to "
            "data_root/memory/memory.db."
        ),
    )
    add_json_output_flag(view)
    view.set_defaults(handler=run_graph, needs_app=False)


__all__ = ["register", "run_graph"]
