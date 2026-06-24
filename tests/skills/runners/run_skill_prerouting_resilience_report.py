#!/usr/bin/env python3.11
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from math import ceil, floor
from pathlib import Path
from typing import Any, Iterable

_EVENT_TYPE = "skill.prerouting"
_SUPPORTED_DB_SUFFIXES = {".db", ".sqlite", ".sqlite3"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Summarize skill.prerouting resilience telemetry from session event stores."
        )
    )
    parser.add_argument(
        "--input",
        action="append",
        required=True,
        help=(
            "Input path. Supports events.jsonl, SQLite session DBs, or directories "
            "containing either format. Repeatable."
        ),
    )
    parser.add_argument(
        "--output",
        help="Optional JSON output path. Defaults to stdout only when omitted.",
    )
    return parser.parse_args()


def _collect_input_files(raw_inputs: Iterable[str]) -> list[Path]:
    discovered: list[Path] = []
    seen: set[str] = set()
    for raw_input in raw_inputs:
        root = Path(raw_input).expanduser().resolve()
        if not root.exists():
            raise FileNotFoundError(f"input path does not exist: {root}")
        candidates = _discover_candidates(root)
        if not candidates:
            raise RuntimeError(f"no supported session event inputs found under: {root}")
        for candidate in candidates:
            key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            discovered.append(candidate)
    return discovered


def _discover_candidates(root: Path) -> list[Path]:
    if root.is_file():
        return [root] if _is_supported_input(root) else []

    candidates: list[Path] = []
    for path in root.rglob("*"):
        if path.is_file() and _is_supported_input(path):
            candidates.append(path)
    candidates.sort()
    return candidates


def _is_supported_input(path: Path) -> bool:
    if path.name == "events.jsonl":
        return True
    return path.suffix.lower() in _SUPPORTED_DB_SUFFIXES


def _load_events(paths: Iterable[Path]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for path in paths:
        if path.name == "events.jsonl":
            events.extend(_load_events_from_jsonl(path))
        else:
            events.extend(_load_events_from_sqlite(path))
    return events


def _load_events_from_jsonl(path: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if str(payload.get("type", "")).strip() != _EVENT_TYPE:
            continue
        event_payload = payload.get("payload")
        if not isinstance(event_payload, dict):
            event_payload = {}
        items.append(
            {
                "source_path": str(path),
                "session_id": str(payload.get("session_id", "") or ""),
                "timestamp": str(payload.get("ts", "") or ""),
                "payload": event_payload,
            }
        )
    return items


def _load_events_from_sqlite(path: Path) -> list[dict[str, Any]]:
    try:
        with sqlite3.connect(str(path)) as conn:
            conn.row_factory = sqlite3.Row
            if not _has_session_events_table(conn):
                return []
            rows = conn.execute(
                """
                SELECT session_id, timestamp, payload_json
                FROM session_events
                WHERE event_type = ?
                ORDER BY timestamp ASC
                """,
                (_EVENT_TYPE,),
            ).fetchall()
    except sqlite3.Error:
        return []

    items: list[dict[str, Any]] = []
    for row in rows:
        raw_payload = str(row["payload_json"] or "{}")
        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        items.append(
            {
                "source_path": str(path),
                "session_id": str(row["session_id"] or ""),
                "timestamp": str(row["timestamp"] or ""),
                "payload": payload,
            }
        )
    return items


def _has_session_events_table(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='session_events'"
    ).fetchone()
    return row is not None


def _percentile(values: list[int], fraction: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    if len(ordered) == 1:
        return int(ordered[0])
    position = (len(ordered) - 1) * fraction
    low_index = floor(position)
    high_index = ceil(position)
    if low_index == high_index:
        return int(ordered[low_index])
    low_value = ordered[low_index]
    high_value = ordered[high_index]
    blended = low_value + (high_value - low_value) * (position - low_index)
    return int(round(blended))


def _build_summary(events: list[dict[str, Any]]) -> dict[str, Any]:
    latencies: list[int] = []
    failure_reason_counts: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()
    session_ids: set[str] = set()
    timestamps = [
        str(item.get("timestamp", "") or "")
        for item in events
        if str(item.get("timestamp", "") or "").strip()
    ]

    fail_closed_count = 0
    for item in events:
        payload = item.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        latency_value = payload.get("latency_ms", 0)
        try:
            latencies.append(max(0, int(latency_value)))
        except (TypeError, ValueError):
            latencies.append(0)

        fail_closed_reason = str(payload.get("fail_closed_reason", "") or "").strip()
        if fail_closed_reason:
            fail_closed_count += 1
            failure_reason_counts[fail_closed_reason] += 1

        source_path = str(item.get("source_path", "") or "")
        if source_path:
            source_counts[source_path] += 1
        session_id = str(item.get("session_id", "") or "")
        if session_id:
            session_ids.add(session_id)

    total_events = len(events)
    return {
        "event_type": _EVENT_TYPE,
        "total_events": total_events,
        "session_count": len(session_ids),
        "source_count": len(source_counts),
        "fail_closed_count": fail_closed_count,
        "fail_closed_rate": round(
            (float(fail_closed_count) / float(total_events)) if total_events else 0.0,
            6,
        ),
        "fail_closed_reason_counts": dict(sorted(failure_reason_counts.items())),
        "latency_ms": {
            "min": min(latencies) if latencies else 0,
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
            "max": max(latencies) if latencies else 0,
        },
        "time_window": {
            "first_timestamp": min(timestamps) if timestamps else None,
            "last_timestamp": max(timestamps) if timestamps else None,
        },
        "sources": [
            {"path": path, "event_count": count}
            for path, count in sorted(source_counts.items())
        ],
    }


def main() -> int:
    args = parse_args()
    input_files = _collect_input_files(args.input)
    events = _load_events(input_files)
    summary = _build_summary(events)
    payload = json.dumps(summary, indent=2, ensure_ascii=True) + "\n"

    if args.output:
        output_path = Path(args.output).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(payload, encoding="utf-8")

    print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
