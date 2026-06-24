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


__all__ = [
    "ThirdBrainChangeSummary",
    "ThirdBrainRunSnapshot",
    "build_run_snapshot",
    "compare_run_snapshots",
    "summarize_change_summary",
]
