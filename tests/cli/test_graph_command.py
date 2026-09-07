"""CLI graph query, neighborhood, and refresh contracts."""

from __future__ import annotations

import json

import pytest

from openminion.cli.commands import graph
from openminion.cli.parser import build_parser
from openminion.modules.context.knowledge import (
    GraphContextItem,
    GraphQueryResult,
    GraphRefreshResult,
    GraphSourceRef,
    LAYER_THIRD_BRAIN,
    TAG_DOCUMENT_GRAPH,
)


class _GraphService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object, tuple[str, ...]]] = []

    def query(self, request, *, provider_names=()):
        self.calls.append(("query", request, tuple(provider_names)))
        return (self._query_result(),)

    def neighborhood(self, request, *, provider_names=()):
        self.calls.append(("neighborhood", request, tuple(provider_names)))
        return (self._query_result(),)

    def refresh(self, request, *, provider_names=()):
        self.calls.append(("refresh", request, tuple(provider_names)))
        return (
            GraphRefreshResult(
                provider="vault_graph",
                layer=LAYER_THIRD_BRAIN,
                ok=True,
                counts={"changed": 1},
            ),
        )

    @staticmethod
    def _query_result() -> GraphQueryResult:
        return GraphQueryResult(
            provider="vault_graph",
            layer=LAYER_THIRD_BRAIN,
            tags=(TAG_DOCUMENT_GRAPH,),
            items=(
                GraphContextItem(
                    provider="vault_graph",
                    source_graph_id="ovga-vault",
                    node_or_edge_id="note-hub",
                    source_ref=GraphSourceRef(path="Hub.md"),
                    snippet="OVGA-MARKER",
                ),
            ),
        )


@pytest.mark.parametrize(
    ("argv", "operation"),
    [
        (["graph", "query", "marker", "--provider", "vault_graph"], "query"),
        (
            [
                "graph",
                "neighborhood",
                "note-hub",
                "--provider",
                "vault_graph",
            ],
            "neighborhood",
        ),
        (["graph", "refresh", "--provider", "vault_graph"], "refresh"),
    ],
)
def test_graph_commands_require_one_configured_source(
    argv: list[str],
    operation: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    service = _GraphService()
    monkeypatch.setattr(graph, "_load_graph_service", lambda _args: service)
    args = build_parser().parse_args([*argv, "--json"])

    assert args.handler(args) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["operation"] == operation
    assert payload["source"] == "vault_graph"
    assert service.calls == [(operation, service.calls[0][1], ("vault_graph",))]


def test_graph_query_human_output_names_layer_tags_and_citation(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(graph, "_load_graph_service", lambda _args: _GraphService())
    args = build_parser().parse_args(
        ["graph", "query", "marker", "--provider", "vault_graph"]
    )

    assert args.handler(args) == 0

    output = capsys.readouterr().out
    assert "Source: vault_graph" in output
    assert "Layer: provider" in output
    assert "Tags: document_graph" in output
    assert "Citation: Hub.md" in output


def test_graph_status_names_source_and_adapter(
    capsys: pytest.CaptureFixture[str],
) -> None:
    graph._print_status_result(
        {
            "graphfakos": {"installed": False},
            "second_brain": {
                "provider": "memory_graph",
                "visual_ready": False,
                "active": False,
            },
            "third_brain": [
                {
                    "provider": "vault_graph",
                    "adapter": "sophiagraph_workspace",
                    "visual_ready": True,
                    "active": True,
                }
            ],
        }
    )

    output = capsys.readouterr().out
    assert "Configured graph sources:" in output
    assert "Source: vault_graph [viewer ready, active]" in output
    assert "Adapter: sophiagraph_workspace" in output


def test_graph_command_rejects_missing_provider() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["graph", "query", "marker"])
