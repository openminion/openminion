from __future__ import annotations

import os
import platform
import subprocess
from pathlib import Path
from typing import Any


def flatten_query_payloads(payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for envelope in payloads:
        provider = str(envelope.get("provider", "") or "")
        layer = str(envelope.get("layer", "") or "")
        diagnostics = dict(envelope.get("diagnostics", {}) or {})
        graph_id = str(diagnostics.get("graph_id", "") or "")
        tags = list(envelope.get("tags", []) or [])
        rows.extend(_item_rows(envelope, provider, layer, graph_id, tags, diagnostics))
        rows.extend(_path_rows(envelope, provider, layer, graph_id, tags, diagnostics))
    return _dedupe_rows(rows)


def resolve_local_path(raw_path: str, *, working_dir: str) -> Path | None:
    text = str(raw_path or "").strip()
    if not text:
        return None
    candidate = Path(text).expanduser()
    if not candidate.is_absolute() and working_dir:
        candidate = Path(working_dir) / candidate
    return candidate.resolve(strict=False)


def open_path(path: Path) -> bool:
    try:
        system = platform.system()
        if system == "Darwin":
            subprocess.Popen(["open", str(path)])
            return True
        if system == "Windows":
            os.startfile(str(path))  # type: ignore[attr-defined]
            return True
        subprocess.Popen(["xdg-open", str(path)])
        return True
    except Exception:
        return False


def dom_id(prefix: str, key: str) -> str:
    normalized = [
        char.lower() if char.isalnum() else "-" for char in str(key or "").strip()
    ]
    slug = "".join(normalized).strip("-") or "row"
    return f"{prefix}-{slug}"


def format_selection_summary(selected: dict[str, Any]) -> str:
    line = selected.get("line")
    path = str(selected.get("path", "") or "—")
    line_text = f":{line}" if line else ""
    return (
        f"{selected['provider']}  ·  {selected['node_or_edge_id']}\n"
        f"{path}{line_text}\n"
        f"graph={selected.get('graph_id', '—')}  score={selected.get('score', '—')}"
    )


def format_status_row(status: dict[str, Any]) -> str:
    state = "ready" if status.get("ok") else "degraded"
    caps = ",".join(status.get("capabilities", [])[:5]) or "none"
    return f"{status.get('provider')} [{state}] caps={caps}"


def format_history_row(item: dict[str, Any]) -> str:
    target = f" -> {item['target']}" if item.get("target") else ""
    providers = ",".join(item.get("providers", [])[:3]) or "all"
    return (
        f"{item.get('ts', '')}  {item.get('mode', '')}  {item.get('query', '')}{target}\n"
        f"providers={providers}  count={item.get('count', 0)}"
    )


def format_saved_view_row(saved_view: Any) -> str:
    target = f" -> {saved_view.target}" if saved_view.target else ""
    providers = ",".join(saved_view.providers[:3]) or "all"
    return (
        f"{saved_view.name}\n"
        f"{saved_view.mode}  {saved_view.query}{target}\n"
        f"providers={providers}"
    )


def format_result_row(row: dict[str, Any]) -> str:
    score = row.get("score")
    score_text = f"{float(score):.2f}" if isinstance(score, (int, float)) else "—"
    snippet = str(row.get("snippet", "") or "").replace("\n", " ").strip()
    preview = (
        snippet[:70] if snippet else str(row.get("path", "") or row["node_or_edge_id"])
    )
    return f"{row['provider']:<12} {score_text:>4}  {row['node_or_edge_id']}\n{preview}"


def _item_rows(
    envelope: dict[str, Any],
    provider: str,
    layer: str,
    graph_id: str,
    tags: list[Any],
    diagnostics: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in envelope.get("items", []) or []:
        source_ref = dict(item.get("source_ref", {}) or {})
        node_or_edge_id = str(item.get("node_or_edge_id", "") or "")
        rows.append(
            _row(
                provider=provider,
                layer=layer,
                graph_id=graph_id,
                node_or_edge_id=node_or_edge_id,
                source_ref=source_ref,
                snippet=str(item.get("snippet", "") or ""),
                score=item.get("score"),
                metadata=dict(item.get("metadata", {}) or {}),
                tags=tags,
                diagnostics=diagnostics,
                raw_item=dict(item),
                raw_envelope=dict(envelope),
            )
        )
    return rows


def _path_rows(
    envelope: dict[str, Any],
    provider: str,
    layer: str,
    graph_id: str,
    tags: list[Any],
    diagnostics: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in envelope.get("paths", []) or []:
        for node in path.get("nodes", []) or []:
            source_ref = dict(node.get("source_ref", {}) or {})
            node_or_edge_id = str(node.get("node_or_edge_id", "") or "")
            snippet = str(node.get("snippet", "") or path.get("explanation", "") or "")
            rows.append(
                _row(
                    provider=provider,
                    layer=layer,
                    graph_id=graph_id,
                    node_or_edge_id=node_or_edge_id,
                    source_ref=source_ref,
                    snippet=snippet,
                    score=node.get("score", path.get("score")),
                    metadata=dict(node.get("metadata", {}) or {}),
                    tags=tags,
                    diagnostics=diagnostics,
                    raw_item=dict(node),
                    raw_envelope=dict(envelope),
                )
            )
    return rows


def _row(
    *,
    provider: str,
    layer: str,
    graph_id: str,
    node_or_edge_id: str,
    source_ref: dict[str, Any],
    snippet: str,
    score: Any,
    metadata: dict[str, Any],
    tags: list[Any],
    diagnostics: dict[str, Any],
    raw_item: dict[str, Any],
    raw_envelope: dict[str, Any],
) -> dict[str, Any]:
    return {
        "key": f"{provider}:{node_or_edge_id}",
        "provider": provider,
        "layer": layer,
        "graph_id": graph_id,
        "node_or_edge_id": node_or_edge_id,
        "path": str(source_ref.get("path", "") or ""),
        "line": source_ref.get("line"),
        "snippet": snippet,
        "score": score,
        "metadata": metadata,
        "tags": tags,
        "diagnostics": diagnostics,
        "raw_item": raw_item,
        "raw_envelope": raw_envelope,
    }


def _dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    for row in rows:
        if row["key"] in seen:
            continue
        seen.add(row["key"])
        output.append(row)
    return output


__all__ = (
    "dom_id",
    "flatten_query_payloads",
    "format_history_row",
    "format_result_row",
    "format_saved_view_row",
    "format_selection_summary",
    "format_status_row",
    "open_path",
    "resolve_local_path",
)
