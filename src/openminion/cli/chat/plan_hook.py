from __future__ import annotations

import json
from typing import Any, Mapping

from openminion.cli.presentation import styles
from openminion.cli.presentation.plan_render import render_plan, render_plan_envelope
from openminion.modules.session.todo import Todo, get_default_todo_store


def _decode_tool_results(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, str):
        token = raw.strip()
        if not token:
            return []
        try:
            parsed = json.loads(token)
        except json.JSONDecodeError:
            return []
    else:
        parsed = raw
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]


def extract_plan_envelope(metadata: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(metadata, Mapping):
        return None
    last_match: dict[str, Any] | None = None
    for item in _decode_tool_results(metadata.get("tool_results")):
        tool_name = str(item.get("tool_name", "") or item.get("name", "")).strip()
        if tool_name and not (tool_name == "plan" or tool_name.startswith("plan.")):
            continue
        data = item.get("data")
        if not isinstance(data, dict):
            continue
        if not isinstance(data.get("plan"), dict):
            continue
        last_match = data
    return last_match


def maybe_print_plan_render(metadata: Mapping[str, Any] | None) -> bool:
    envelope = extract_plan_envelope(metadata)
    if envelope is None:
        return False
    print(
        styles.style(
            styles.StyleToken.INFO,
            render_plan_envelope(envelope),
        )
    )
    return True


def capture_plan_snapshot(session_id: str) -> str | None:
    plan = get_default_todo_store().get_plan(session_id)
    if plan is None:
        return None
    return _plan_snapshot_payload(plan)


def maybe_print_plan_render_for_session_change(
    *, session_id: str, previous_snapshot: str | None
) -> bool:
    store = get_default_todo_store()
    plan = store.get_plan(session_id)
    current_snapshot = None if plan is None else _plan_snapshot_payload(plan)
    if current_snapshot == previous_snapshot:
        return False
    payload = _serialize_plan_for_render(plan, session_id=session_id)
    print(styles.style(styles.StyleToken.INFO, render_plan(payload)))
    return True


def evict_plan_for_session(session_id: str) -> None:
    get_default_todo_store().evict(session_id)


def _serialize_plan_for_render(
    plan: Todo | None, *, session_id: str
) -> dict[str, Any] | None:
    if plan is None:
        return {
            "session_id": session_id,
            "items": [],
            "summary": "0/0 done, 0 in progress",
        }
    return {
        "session_id": plan.session_id,
        "items": [
            {
                "index": item.index,
                "text": item.text,
                "status": item.status,
            }
            for item in plan.items
        ],
        "summary": plan.summary(),
    }


def _plan_snapshot_payload(plan: Todo) -> str:
    payload = {
        "items": [
            {
                "index": item.index,
                "text": item.text,
                "status": item.status,
            }
            for item in plan.items
        ],
        "summary": plan.summary(),
    }
    return json.dumps(payload, sort_keys=True)


__all__ = [
    "capture_plan_snapshot",
    "evict_plan_for_session",
    "extract_plan_envelope",
    "maybe_print_plan_render_for_session_change",
    "maybe_print_plan_render",
]
