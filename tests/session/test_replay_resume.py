from __future__ import annotations

import json
from typing import Any

from openminion.modules.session.storage.sqlite_store import (
    SQLiteSessionStore,
    _CLOSED_TASK_STATUSES,
)


def _replay_open_tasks(
    base_items: list[dict[str, Any]], tail_events: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    tasks: dict[str, dict[str, Any]] = {}

    for item in base_items:
        task_id = str(item["task_id"])
        tasks[task_id] = {
            "task_id": task_id,
            "title": str(item.get("title") or task_id),
            "status": str(item.get("status") or "open"),
            "note": item.get("note"),
            "last_seq": 0,
        }

    for event in tail_events:
        payload = dict(event.get("payload") or {})
        event_type = str(event.get("event_type") or "")
        task_id = (
            event.get("task_id") or payload.get("task_id") or payload.get("job_id")
        )
        if not task_id:
            continue
        task_key = str(task_id)
        item = tasks.setdefault(
            task_key,
            {
                "task_id": task_key,
                "title": str(payload.get("title") or payload.get("method") or task_key),
                "status": str(payload.get("status") or "open"),
                "note": payload.get("note"),
                "last_seq": 0,
            },
        )
        if payload.get("title"):
            item["title"] = str(payload["title"])
        if payload.get("note") is not None:
            item["note"] = payload.get("note")
        item["last_seq"] = int(event["seq"])

        if event_type == "task.opened":
            item["status"] = str(payload.get("status") or "open")
        elif event_type == "task.updated":
            item["status"] = str(payload.get("status") or item.get("status") or "open")
        elif event_type == "job.created":
            item["status"] = str(payload.get("status") or "queued")
        elif event_type == "job.started":
            item["status"] = str(payload.get("status") or "running")
        elif event_type == "job.completed":
            item["status"] = str(payload.get("status") or "completed")
        elif event_type == "job.cancelled":
            item["status"] = str(payload.get("status") or "cancelled")

    remaining: list[tuple[int, dict[str, Any]]] = []
    for task in tasks.values():
        status_value = str(task.get("status", "")).lower()
        if status_value in _CLOSED_TASK_STATUSES:
            continue
        remaining.append(
            (
                int(task.get("last_seq", 0)),
                {
                    "task_id": task["task_id"],
                    "title": task.get("title"),
                    "status": task.get("status"),
                    "note": task.get("note"),
                },
            )
        )

    remaining.sort(key=lambda item: (item[0], item[1]["task_id"]))
    return [payload for _, payload in remaining]


def _normalize_open_tasks(
    tasks: list[dict[str, Any]],
) -> list[tuple[str, str | None, str | None]]:
    return sorted(
        (
            str(item.get("task_id")),
            str(item.get("title")),
            str(item.get("status")),
            item.get("note"),
        )
        for item in tasks
    )


def test_snapshot_plus_tail_events_matches_live_open_tasks(tmp_path) -> None:
    store = SQLiteSessionStore(tmp_path / "sessctl.db")
    try:
        session_id = store.create_session(
            initial_agent_id="agent.alpha", profile_version="pv1"
        )

        for event_type, payload in [
            ("task.opened", {"task_id": "t1", "title": "Plan", "status": "open"}),
            ("task.opened", {"task_id": "t2", "title": "Draft", "status": "open"}),
            ("task.updated", {"task_id": "t1", "status": "queued"}),
        ]:
            store.append_event(
                session_id,
                event_type=event_type,
                payload=payload,
                task_id=str(payload["task_id"]),
            )

        boundary_seq = store.get_events(session_id)[-1]["seq"]
        snapshot_id = store.create_snapshot(session_id, seq_upto=boundary_seq)

        for event_type, payload in [
            ("task.updated", {"task_id": "t1", "status": "running"}),
            ("job.started", {"task_id": "t1", "status": "running"}),
            ("task.updated", {"task_id": "t2", "status": "completed"}),
            ("task.opened", {"task_id": "t3", "title": "Review", "status": "open"}),
        ]:
            store.append_event(
                session_id,
                event_type=event_type,
                payload=payload,
                task_id=str(payload.get("task_id")),
            )

        session_slice = store.get_slice(
            session_id,
            purpose="resume",
            limits={
                "max_turns": 6,
                "max_tool_events": 6,
                "summary_variant": "short",
                "include_open_tasks": True,
                "include_active_state": True,
            },
        )
        live_open_tasks = session_slice["open_tasks"]

        snapshot_row = store._conn.execute(
            "SELECT seq_upto, open_tasks_json FROM session_snapshots WHERE snapshot_id = ?",
            (snapshot_id,),
        ).fetchone()
        base_open_tasks = json.loads(snapshot_row["open_tasks_json"])
        replay_tail_events = store.get_events(
            session_id, after_seq=int(snapshot_row["seq_upto"])
        )
    finally:
        store.close()

    assert replay_tail_events
    assert replay_tail_events[0]["seq"] == int(snapshot_row["seq_upto"]) + 1
    assert _normalize_open_tasks(live_open_tasks) == _normalize_open_tasks(
        _replay_open_tasks(base_open_tasks, replay_tail_events)
    )
    assert session_slice["last_event_seq"] == replay_tail_events[-1]["seq"]
