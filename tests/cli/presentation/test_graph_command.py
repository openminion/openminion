from __future__ import annotations

from openminion.cli.presentation.graph import render_graph_command


def test_graph_command_lists_user_entrypoints() -> None:
    body = render_graph_command("")

    assert "openminion graph status" in body
    assert "openminion graph view --current" in body
    assert "openminion graph view --brain third --provider <name>" in body


def test_graph_command_builds_current_and_third_brain_commands() -> None:
    current = render_graph_command("current --node-kind fact")
    third = render_graph_command("third repo_graph --html-out viewer.html")

    assert current == (
        "Graph viewer command:\n  openminion graph view --current --node-kind fact"
    )
    assert third == (
        "Graph viewer command:\n"
        "  openminion graph view --brain third --provider repo_graph --html-out "
        "viewer.html"
    )


def test_graph_command_builds_static_html_command() -> None:
    body = render_graph_command("html memory.html --node-kind decision")

    assert body == (
        "Graph viewer command:\n"
        "  openminion graph view --current --html-out memory.html --node-kind decision"
    )
