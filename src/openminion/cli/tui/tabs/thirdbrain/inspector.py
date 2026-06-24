from __future__ import annotations

from typing import Any


def build_provider_comparison(
    *,
    selected: dict[str, Any],
    rows: list[dict[str, Any]],
    provider_names: tuple[str, ...],
) -> dict[str, Any]:
    comparison_key = _comparison_key(selected)
    per_provider_rows: list[dict[str, Any]] = []
    provider_counts = {
        provider: sum(
            1 for row in rows if str(row.get("provider", "") or "") == provider
        )
        for provider in provider_names
    }
    for provider in provider_names:
        provider_rows = [
            row for row in rows if str(row.get("provider", "") or "") == provider
        ]
        matched = next(
            (row for row in provider_rows if _comparison_key(row) == comparison_key),
            None,
        )
        per_provider_rows.append(
            {
                "provider": provider,
                "present": matched is not None,
                "result_count": provider_counts.get(provider, 0),
                "match": _comparison_match(matched),
            }
        )
    return {
        "comparison_kind": "side_by_side_provider_comparison",
        "selected_provider": str(selected.get("provider", "") or ""),
        "selected_identity": _comparison_match(selected),
        "provider_counts": provider_counts,
        "providers": per_provider_rows,
        "missing_providers": [
            row["provider"] for row in per_provider_rows if not row["present"]
        ],
    }


def build_local_map(
    *,
    selected: dict[str, Any],
    traversal_rows: list[dict[str, Any]],
    mode: str,
    target: str,
) -> dict[str, Any]:
    anchor = _node_ref(selected)
    if not traversal_rows:
        return {
            "map_kind": "local_graph_map",
            "mode": mode,
            "anchor": anchor,
            "target": target,
            "nodes": [anchor],
            "edges": [],
            "message": "Run Neighbors or Path to build a local map.",
        }
    if mode == "path":
        nodes = [_node_ref(row) for row in traversal_rows]
        edges = [
            {
                "source_id": nodes[index]["node_or_edge_id"],
                "target_id": nodes[index + 1]["node_or_edge_id"],
                "kind": "path_step",
            }
            for index in range(len(nodes) - 1)
        ]
        return {
            "map_kind": "local_graph_map",
            "mode": mode,
            "anchor": anchor,
            "target": target,
            "nodes": nodes,
            "edges": edges,
            "message": f"Path map with {len(nodes)} visible nodes.",
        }
    center_id = str(selected.get("node_or_edge_id", "") or "")
    nodes = [_node_ref(row) for row in traversal_rows]
    edges = [
        {
            "source_id": center_id,
            "target_id": str(row.get("node_or_edge_id", "") or ""),
            "kind": "neighbor",
        }
        for row in traversal_rows
        if str(row.get("node_or_edge_id", "") or "") != center_id
    ]
    return {
        "map_kind": "local_graph_map",
        "mode": mode,
        "anchor": anchor,
        "target": target,
        "nodes": nodes,
        "edges": edges,
        "message": f"Neighborhood map with {len(edges)} visible neighbor edges.",
    }


def _comparison_key(row: dict[str, Any]) -> tuple[str, int | None, str]:
    path = str(row.get("path", "") or "")
    line = row.get("line")
    node_or_edge_id = str(row.get("node_or_edge_id", "") or "")
    if path:
        return (path, line if isinstance(line, int) else None, "")
    return ("", None, node_or_edge_id)


def _comparison_match(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "node_or_edge_id": str(row.get("node_or_edge_id", "") or ""),
        "path": str(row.get("path", "") or ""),
        "line": row.get("line"),
        "score": row.get("score"),
        "snippet": str(row.get("snippet", "") or ""),
    }


def _node_ref(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "provider": str(row.get("provider", "") or ""),
        "node_or_edge_id": str(row.get("node_or_edge_id", "") or ""),
        "path": str(row.get("path", "") or ""),
        "line": row.get("line"),
        "snippet": str(row.get("snippet", "") or ""),
    }


__all__ = ["build_local_map", "build_provider_comparison"]
