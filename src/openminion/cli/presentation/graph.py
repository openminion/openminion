from __future__ import annotations

import shlex


def render_graph_command(args: str) -> str:
    try:
        tokens = shlex.split(str(args or ""))
    except ValueError as exc:
        return f"Graph viewer: {exc}"
    if not tokens:
        return "\n".join(
            (
                "Graph viewer:",
                "  status   openminion graph status",
                "  current  openminion graph view --current",
                "  dry-run  openminion graph view --current --dry-run --json",
                "  html     openminion graph view --current --html-out viewer.html",
                "  third    openminion graph view --brain third --provider <name>",
            )
        )
    action = tokens[0].lower()
    rest = tokens[1:]
    if action == "status":
        return _command("openminion", "graph", "status", *rest)
    if action == "current":
        return _command("openminion", "graph", "view", "--current", *rest)
    if action == "dry-run":
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
        target = rest[0] if rest else "viewer.html"
        return _command(
            "openminion",
            "graph",
            "view",
            "--current",
            "--html-out",
            target,
            *rest[1:],
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


__all__ = ["render_graph_command"]
