from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class ThirdBrainRunSnapshot:
    mode: str
    query: str
    target: str
    providers: tuple[str, ...]
    result_ids: tuple[str, ...]
    omitted_ids: tuple[str, ...]
    captured_at: str


@dataclass(frozen=True)
class ThirdBrainChangeSummary:
    mode: str
    query: str
    target: str
    providers: tuple[str, ...]
    previous_query: str
    previous_target: str
    previous_count: int
    current_count: int
    added_result_ids: tuple[str, ...]
    removed_result_ids: tuple[str, ...]
    added_omitted_ids: tuple[str, ...]
    removed_omitted_ids: tuple[str, ...]
    changed_at: str
    had_previous_run: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "query": self.query,
            "target": self.target,
            "providers": list(self.providers),
            "previous_query": self.previous_query,
            "previous_target": self.previous_target,
            "previous_count": self.previous_count,
            "current_count": self.current_count,
            "added_result_ids": list(self.added_result_ids),
            "removed_result_ids": list(self.removed_result_ids),
            "added_omitted_ids": list(self.added_omitted_ids),
            "removed_omitted_ids": list(self.removed_omitted_ids),
            "changed_at": self.changed_at,
            "had_previous_run": self.had_previous_run,
        }


@dataclass(frozen=True)
class ThirdBrainRefreshDeltaSummary:
    providers: tuple[dict[str, Any], ...]
    total_changed_paths: int
    total_removed_paths: int
    total_added_nodes: int
    total_removed_nodes: int
    total_added_edges: int
    total_removed_edges: int
    refreshed_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "providers": [dict(provider) for provider in self.providers],
            "total_changed_paths": self.total_changed_paths,
            "total_removed_paths": self.total_removed_paths,
            "total_added_nodes": self.total_added_nodes,
            "total_removed_nodes": self.total_removed_nodes,
            "total_added_edges": self.total_added_edges,
            "total_removed_edges": self.total_removed_edges,
            "refreshed_at": self.refreshed_at,
        }


def build_run_snapshot(
    *,
    payloads: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    mode: str,
    query: str,
    target: str,
    providers: tuple[str, ...],
) -> ThirdBrainRunSnapshot:
    result_ids = tuple(sorted(_result_identity(row) for row in rows))
    omitted_ids = tuple(
        sorted(_omitted_identity(item) for item in _flatten_omitted(payloads))
    )
    return ThirdBrainRunSnapshot(
        mode=str(mode or ""),
        query=str(query or ""),
        target=str(target or ""),
        providers=tuple(
            sorted(str(name or "") for name in providers if str(name or "").strip())
        ),
        result_ids=result_ids,
        omitted_ids=omitted_ids,
        captured_at=datetime.now().strftime("%H:%M:%S"),
    )


def build_refresh_delta_summary(
    payloads: list[dict[str, Any]],
) -> ThirdBrainRefreshDeltaSummary | None:
    if not payloads:
        return None
    provider_summaries = tuple(
        _provider_refresh_summary(payload) for payload in payloads
    )
    return ThirdBrainRefreshDeltaSummary(
        providers=provider_summaries,
        total_changed_paths=_sum_provider_count(
            provider_summaries, "changed_path_count"
        ),
        total_removed_paths=_sum_provider_count(
            provider_summaries, "removed_path_count"
        ),
        total_added_nodes=_sum_provider_count(provider_summaries, "added_node_count"),
        total_removed_nodes=_sum_provider_count(
            provider_summaries, "removed_node_count"
        ),
        total_added_edges=_sum_provider_count(provider_summaries, "added_edge_count"),
        total_removed_edges=_sum_provider_count(
            provider_summaries, "removed_edge_count"
        ),
        refreshed_at=max(
            (str(item.get("refreshed_at", "") or "") for item in provider_summaries),
            default="",
        ),
    )


def compare_run_snapshots(
    previous: ThirdBrainRunSnapshot | None,
    current: ThirdBrainRunSnapshot,
) -> ThirdBrainChangeSummary:
    if previous is None:
        return ThirdBrainChangeSummary(
            mode=current.mode,
            query=current.query,
            target=current.target,
            providers=current.providers,
            previous_query="",
            previous_target="",
            previous_count=0,
            current_count=len(current.result_ids),
            added_result_ids=current.result_ids,
            removed_result_ids=(),
            added_omitted_ids=current.omitted_ids,
            removed_omitted_ids=(),
            changed_at=current.captured_at,
            had_previous_run=False,
        )
    previous_results = set(previous.result_ids)
    current_results = set(current.result_ids)
    previous_omitted = set(previous.omitted_ids)
    current_omitted = set(current.omitted_ids)
    return ThirdBrainChangeSummary(
        mode=current.mode,
        query=current.query,
        target=current.target,
        providers=current.providers,
        previous_query=previous.query,
        previous_target=previous.target,
        previous_count=len(previous.result_ids),
        current_count=len(current.result_ids),
        added_result_ids=tuple(sorted(current_results - previous_results)),
        removed_result_ids=tuple(sorted(previous_results - current_results)),
        added_omitted_ids=tuple(sorted(current_omitted - previous_omitted)),
        removed_omitted_ids=tuple(sorted(previous_omitted - current_omitted)),
        changed_at=current.captured_at,
        had_previous_run=True,
    )


def summarize_refresh_delta_summary(
    summary: ThirdBrainRefreshDeltaSummary | None,
) -> str:
    if summary is None:
        return "No provider refresh delta yet"
    return (
        f"{summary.refreshed_at or 'latest'}  "
        f"paths +{summary.total_changed_paths} / -{summary.total_removed_paths}\n"
        f"nodes +{summary.total_added_nodes} / -{summary.total_removed_nodes}  "
        f"edges +{summary.total_added_edges} / -{summary.total_removed_edges}"
    )


def summarize_change_summary(summary: ThirdBrainChangeSummary | None) -> str:
    if summary is None:
        return "No change set yet"
    if not summary.had_previous_run:
        return (
            f"{summary.changed_at}  first {summary.mode} run\n"
            f"results={summary.current_count}"
        )
    return (
        f"{summary.changed_at}  {summary.mode}  "
        f"+{len(summary.added_result_ids)} / -{len(summary.removed_result_ids)}\n"
        f"results={summary.previous_count}->{summary.current_count}"
    )


def _flatten_omitted(payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    omitted: list[dict[str, Any]] = []
    for payload in payloads:
        for item in payload.get("omitted", []) or []:
            omitted.append(dict(item or {}))
    return omitted


def _result_identity(row: dict[str, Any]) -> str:
    provider = str(row.get("provider", "") or "")
    node_or_edge_id = str(row.get("node_or_edge_id", "") or "")
    path = str(row.get("path", "") or "")
    line = row.get("line")
    return f"{provider}|{node_or_edge_id}|{path}|{line}"


def _omitted_identity(item: dict[str, Any]) -> str:
    provider = str(item.get("provider", "") or "")
    node_or_edge_id = str(item.get("node_or_edge_id", "") or "")
    reason = str(item.get("reason", "") or "")
    return f"{provider}|{node_or_edge_id}|{reason}"


def _provider_refresh_summary(payload: dict[str, Any]) -> dict[str, Any]:
    counts = dict(payload.get("counts", {}) or {})
    diagnostics = dict(payload.get("diagnostics", {}) or {})
    return {
        "provider": str(payload.get("provider", "") or ""),
        "ok": bool(payload.get("ok", False)),
        "refreshed_at": str(payload.get("refreshed_at", "") or ""),
        "changed_path_count": _count_value(counts, "changed_path_count"),
        "removed_path_count": _count_value(counts, "removed_path_count"),
        "added_node_count": _count_value(counts, "added_node_count"),
        "removed_node_count": _count_value(counts, "removed_node_count"),
        "added_edge_count": _count_value(counts, "added_edge_count"),
        "removed_edge_count": _count_value(counts, "removed_edge_count"),
        "omitted_reason_counts": dict(
            counts.get("omitted_reason_counts", {})
            or diagnostics.get("omitted_reason_counts", {})
            or {}
        ),
        "counts": counts,
        "diagnostics": diagnostics,
    }


def _count_value(counts: dict[str, Any], key: str) -> int:
    return int(counts.get(key, 0) or 0)


def _sum_provider_count(providers: tuple[dict[str, Any], ...], key: str) -> int:
    return sum(int(item.get(key, 0) or 0) for item in providers)


__all__ = [
    "ThirdBrainChangeSummary",
    "ThirdBrainRefreshDeltaSummary",
    "ThirdBrainRunSnapshot",
    "build_refresh_delta_summary",
    "build_run_snapshot",
    "compare_run_snapshots",
    "summarize_change_summary",
    "summarize_refresh_delta_summary",
]
