from __future__ import annotations

import shlex


def render_graph_command(args: str) -> str:
    try:
        tokens = shlex.split(str(args or ""))
    except ValueError as exc:
        return f"Graph viewer: {exc}"
    if not tokens:
        return _usage()
    action = tokens[0].lower()
    rest = tokens[1:]
    if action in {"help", "?"}:
        return _usage()
    if action == "status":
        return _command("openminion", "graph", "status", *rest)
    if action == "current":
        return _command("openminion", "graph", "view", "--current", *rest)
    if action in {"dry-run", "json"}:
        return _command(
            "openminion",
            "graph",
            "view",
            "--current",
            "--dry-run",
            "--json",
            *rest,
        )
    if action == "html":
        target = "viewer.html"
        if rest and not rest[0].startswith("-"):
            target = rest[0]
            rest = rest[1:]
        return _command(
            "openminion",
            "graph",
            "view",
            "--current",
            "--html-out",
            target,
            *rest,
        )
    if action == "third" and rest:
        return _command(
            "openminion",
            "graph",
            "view",
            "--brain",
            "third",
            "--provider",
            rest[0],
            *rest[1:],
        )
    return (
        "Graph viewer: use /graph, /graph status, /graph current, "
        "/graph dry-run, /graph html [path], or /graph third <provider>."
    )


def _command(*parts: str) -> str:
    return f"Graph viewer command:\n  {shlex.join(parts)}"


def _usage() -> str:
    return "\n".join(
        (
            "Graph viewer:",
            "  status   openminion graph status",
            "  current  openminion graph view --current",
            "  dry-run  openminion graph view --current --dry-run --json",
            "  json     openminion graph view --current --dry-run --json",
            "  html     openminion graph view --current --html-out viewer.html",
            "  third    openminion graph view --brain third --provider <name>",
        )
    )


__all__ = ["render_graph_command"]
