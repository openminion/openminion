from __future__ import annotations

from typing import Any, Mapping

STATUS_MARK: dict[str, str] = {
    "todo": " ",
    "in_progress": "→",
    "done": "x",
    "blocked": "!",
}

UNKNOWN_STATUS_MARK = "?"

EMPTY_PLAN_TEXT = "Plan: (empty)"


def _item_line(item: Mapping[str, Any]) -> str:
    status = str(item.get("status", "") or "")
    mark = STATUS_MARK.get(status, UNKNOWN_STATUS_MARK)
    text = str(item.get("text", "") or "")
    return f"  [{mark}] {text}"


def render_plan(plan: Mapping[str, Any] | None) -> str:
    if not plan:
        return EMPTY_PLAN_TEXT

    items = plan.get("items") or []
    if not items:
        return EMPTY_PLAN_TEXT

    summary = str(plan.get("summary", "") or "").strip()
    header = f"Plan ({summary}):" if summary else "Plan:"
    body = "\n".join(_item_line(item) for item in items)
    return f"{header}\n{body}"


def render_plan_envelope(envelope: Mapping[str, Any] | None) -> str:
    return render_plan(None if not envelope else envelope.get("plan"))
