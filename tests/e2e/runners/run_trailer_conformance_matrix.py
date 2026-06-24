#!/usr/bin/env python3.11

from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from pathlib import Path
from typing import Any


_TASK_PLAN_EVENT_TYPES = {
    "task_plan.declared",
    "task_plan.step_completed",
    "task_plan.step_blocked",
    "task_plan.revised",
    "task_plan.abandoned",
    "task_plan.completed",
}


def _collect_trailer_events(session_id: str, data_root: Path) -> list[dict[str, Any]]:
    import json as _json
    import sqlite3

    candidates = [
        data_root / "state" / "brain" / "sessions.db",
        data_root / "brain" / "sessions.db",
    ]
    db_path = next((path for path in candidates if path.exists()), None)
    if db_path is None:
        return []

    try:
        conn = sqlite3.connect(str(db_path))
    except sqlite3.Error:
        return []
    events: list[dict[str, Any]] = []
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT event_type, payload_json FROM session_events
            WHERE session_id = ?
              AND event_type IN (
                'trailer.expected',
                'trailer.emitted',
                'trailer.feedback_pending',
                'trailer.feedback_surfaced',
                'task_plan.declared',
                'task_plan.step_completed',
                'task_plan.step_blocked',
                'task_plan.revised',
                'task_plan.abandoned',
                'task_plan.completed',
                'task_plan.invalid_trailer'
              )
            ORDER BY seq ASC
            """,
            (session_id,),
        )
        for event_type, payload_json in cur.fetchall():
            try:
                payload = _json.loads(payload_json) if payload_json else {}
            except (TypeError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue
            events.append(
                {
                    "event_type": str(event_type or ""),
                    "lanes": list(payload.get("lanes") or []),
                    "route": str(payload.get("route") or ""),
                    "source": str(payload.get("source") or ""),
                    "sources": payload.get("sources")
                    if isinstance(payload, dict)
                    else {},
                }
            )
    finally:
        conn.close()
    return events


def _summarize_session(
    session_id: str,
    events: list[dict[str, Any]],
    provider: str,
) -> dict[str, Any]:
    lane_stats: dict[str, dict[str, int]] = defaultdict(
        lambda: {"expected": 0, "emitted": 0}
    )
    source_stats: dict[str, dict[str, int]] = defaultdict(
        lambda: {"trailer": 0, "structured_field": 0, "plan_tool": 0, "unknown": 0}
    )
    runs_by_route: dict[str, int] = defaultdict(int)
    expected_runs = 0
    emitted_runs = 0
    feedback_pending_runs = 0
    feedback_surfaced_runs = 0
    for event in events:
        etype = event["event_type"]
        if etype == "trailer.expected":
            expected_runs += 1
        elif etype == "trailer.emitted":
            emitted_runs += 1
            runs_by_route[event.get("route") or "unknown"] += 1
        elif etype == "trailer.feedback_pending":
            feedback_pending_runs += 1
        elif etype == "trailer.feedback_surfaced":
            feedback_surfaced_runs += 1
        elif etype in _TASK_PLAN_EVENT_TYPES:
            lane_stats["apd"]["emitted"] += 1
            source = event.get("source") or "unknown"
            source_stats["apd"][
                source if source in source_stats["apd"] else "unknown"
            ] += 1
            continue
        for lane in event["lanes"]:
            if etype == "trailer.expected":
                lane_stats[lane]["expected"] += 1
            elif etype == "trailer.emitted":
                lane_stats[lane]["emitted"] += 1
                sources = _sources_for_lane(event, lane)
                for source in sources:
                    source_stats[lane][
                        source if source in source_stats[lane] else "unknown"
                    ] += 1
    return {
        "session_id": session_id,
        "provider": provider,
        "event_count": len(events),
        "runs": {
            "trailer.expected": expected_runs,
            "trailer.emitted": emitted_runs,
            "trailer.feedback_pending": feedback_pending_runs,
            "trailer.feedback_surfaced": feedback_surfaced_runs,
        },
        "runs_by_route": dict(runs_by_route),
        "lane_stats": {lane: dict(stats) for lane, stats in lane_stats.items()},
        "source_stats": {lane: dict(stats) for lane, stats in source_stats.items()},
    }


def _sources_for_lane(event: dict[str, Any], lane: str) -> list[str]:
    sources = event.get("sources")
    if isinstance(sources, dict):
        lane_sources = sources.get(lane)
        if isinstance(lane_sources, list | tuple | set):
            normalized = [
                str(item or "").strip()
                for item in lane_sources
                if str(item or "").strip()
            ]
            if normalized:
                return normalized
        if isinstance(lane_sources, str) and lane_sources.strip():
            return [lane_sources.strip()]
    source = str(event.get("source") or "").strip()
    return [source or "unknown"]


def _aggregate_by_provider(
    sessions: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    provider_lane: dict[str, dict[str, dict[str, int]]] = defaultdict(
        lambda: defaultdict(lambda: {"expected": 0, "emitted": 0})
    )
    provider_runs: dict[str, dict[str, int]] = defaultdict(
        lambda: {
            "trailer.expected": 0,
            "trailer.emitted": 0,
            "trailer.feedback_pending": 0,
            "trailer.feedback_surfaced": 0,
        }
    )
    provider_sources: dict[str, dict[str, dict[str, int]]] = defaultdict(
        lambda: defaultdict(
            lambda: {
                "trailer": 0,
                "structured_field": 0,
                "plan_tool": 0,
                "unknown": 0,
            }
        )
    )
    for session in sessions:
        provider = str(session.get("provider") or "unknown")
        for lane, stats in (session.get("lane_stats") or {}).items():
            provider_lane[provider][lane]["expected"] += stats.get("expected", 0)
            provider_lane[provider][lane]["emitted"] += stats.get("emitted", 0)
        for etype, count in (session.get("runs") or {}).items():
            provider_runs[provider][etype] += count
        for lane, stats in (session.get("source_stats") or {}).items():
            for source, count in stats.items():
                key = (
                    source if source in provider_sources[provider][lane] else "unknown"
                )
                provider_sources[provider][lane][key] += int(count or 0)

    summary: dict[str, dict[str, Any]] = {}
    for provider in set(list(provider_lane.keys()) + list(provider_runs.keys())):
        lane_rates: dict[str, dict[str, Any]] = {}
        for lane, counts in provider_lane.get(provider, {}).items():
            expected = counts["expected"]
            emitted = counts["emitted"]
            rate = (emitted / expected) if expected > 0 else None
            lane_rates[lane] = {
                "expected": expected,
                "emitted": emitted,
                "rate": rate,
            }
        summary[provider] = {
            "lane_rates": lane_rates,
            "source_rates": {
                lane: dict(stats)
                for lane, stats in provider_sources.get(provider, {}).items()
            },
            "runs": dict(provider_runs.get(provider, {})),
        }
    return summary


def _parse_session_spec(raw: str) -> tuple[str, str]:
    if "@" in raw:
        sid, provider = raw.rsplit("@", 1)
        return sid.strip(), provider.strip() or "unknown"
    return raw.strip(), "unknown"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sessions",
        required=True,
        help="Comma-separated list of session specs: `session_id:provider` or `session_id`.",
    )
    parser.add_argument(
        "--data-root",
        default=os.environ.get("OPENMINION_DATA_ROOT", ".openminion"),
        help="OpenMinion data root (default: $OPENMINION_DATA_ROOT or .openminion).",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Path to write the baseline report JSON.",
    )
    args = parser.parse_args(argv)

    data_root = Path(args.data_root).resolve()
    specs = [_parse_session_spec(s) for s in args.sessions.split(",") if s.strip()]

    session_reports: list[dict[str, Any]] = []
    for session_id, provider in specs:
        events = _collect_trailer_events(session_id, data_root)
        session_reports.append(_summarize_session(session_id, events, provider))

    report = {
        "tool": "run_trailer_conformance_matrix",
        "data_root": str(data_root),
        "sessions": session_reports,
        "provider_lane_summary": _aggregate_by_provider(session_reports),
    }

    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"wrote trailer conformance matrix to {output_path}")
    return 0
