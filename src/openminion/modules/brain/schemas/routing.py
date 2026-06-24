"""Brain schema routing helpers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..constants import DELEGATION_ARTIFACT_REF_LIMIT, DELEGATION_TEXT_MAX_CHARS

_DEFAULT_ROUTE_DESCRIPTIONS: dict[str, str] = {
    "respond": (
        "answer directly when no meaningful execution workflow is needed. Use "
        "`respond_kind='clarify'` when required inputs are missing."
    ),
    "act": (
        "execute work now through the shared act loop. You may include "
        "`act_profile`, `execution_target`, or `subtasks` when they are already "
        "semantically clear, but ordinary local act work may rely on runtime "
        "route resolution. Use `execution_target.kind='delegated'` only when the "
        "user explicitly asks another agent to execute the work."
    ),
}
_SESSION_WORK_SUMMARY_MAX_CHARS = 800


def _registry_route_names() -> list[str]:
    try:
        from ..bootstrap.route_catalog import decision_visible_routes

        names = list(decision_visible_routes())
    except Exception:
        names = []
    if not names:
        return list(_DEFAULT_ROUTE_DESCRIPTIONS)
    ordered_defaults = [name for name in _DEFAULT_ROUTE_DESCRIPTIONS if name in names]
    extras = sorted(name for name in names if name not in _DEFAULT_ROUTE_DESCRIPTIONS)
    return ordered_defaults + extras


def _registry_route_descriptions() -> dict[str, str]:
    descriptions = dict(_DEFAULT_ROUTE_DESCRIPTIONS)
    try:
        from ..bootstrap.route_catalog import decision_route_descriptions

        descriptions.update(decision_route_descriptions(None))
    except Exception:
        pass
    ordered_names = _registry_route_names()
    return {
        name: descriptions.get(name, f"use the registered {name} route")
        for name in ordered_names
    }


def _route_field_description() -> str:
    parts = ["Routing decision."]
    for name, description in _registry_route_descriptions().items():
        parts.append(f"'{name}' — {description}")
    return " ".join(parts)


def _normalize_route_name(value: Any) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError("route is required")
    available = set(_registry_route_names())
    if available and normalized not in available:
        raise ValueError(
            f"route must be one of the registered routes: {', '.join(sorted(available))}"
        )
    return normalized


def _normalize_stripped_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_sub_intents(value: Any) -> Any:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        normalized: list[str] = []
        for item in value:
            if isinstance(item, Mapping):
                raise ValueError(
                    "Decision.sub_intents must remain list[str] until structured cutover is approved"
                )
            text = str(item or "").strip()
            if text:
                normalized.append(text)
        return normalized
    return value


def normalize_decomposed_subtasks(value: Any) -> Any:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        return value
    normalized: list[Any] = []
    for item in value:
        if not isinstance(item, Mapping):
            normalized.append(item)
            continue
        payload = dict(item)
        if "subtasks" in payload:
            payload.pop("subtasks", None)
        if not str(payload.get("subtask_id") or "").strip():
            for alias in ("id", "intent_id"):
                legacy_id = str(payload.get(alias) or "").strip()
                if legacy_id:
                    payload["subtask_id"] = legacy_id
                    break
        if not str(payload.get("goal") or "").strip():
            for alias in (
                "subtask_goal",
                "description",
                "content",
                "question",
                "topic",
            ):
                legacy_goal = str(payload.get(alias) or "").strip()
                if legacy_goal:
                    payload["goal"] = legacy_goal
                    break
        for legacy_field in (
            "content",
            "description",
            "id",
            "intent",
            "intent_id",
            "kind",
            "question",
            "status",
            "subtask_goal",
            "subtasks",
            "topic",
        ):
            payload.pop(legacy_field, None)
        normalized.append(payload)
    return normalized


def _flatten_branch_payloads(value: Any) -> Any:
    if not isinstance(value, Mapping):
        return value
    normalized = dict(value)
    if "route" not in normalized and "mode" in normalized:
        normalized["route"] = normalized.get("mode")
    route = str(normalized.get("route") or normalized.get("mode") or "").strip()
    if route == "respond" and isinstance(normalized.get("respond"), Mapping):
        payload = dict(normalized.get("respond") or {})
        normalized.setdefault("respond_kind", payload.get("respond_kind"))
        normalized.setdefault("answer", payload.get("answer"))
        normalized.setdefault("question", payload.get("question"))
        normalized.setdefault("clarify_context", payload.get("clarify_context"))
        normalized.setdefault(
            "pending_turn_context", payload.get("pending_turn_context")
        )
    if route == "act" and isinstance(normalized.get("act"), Mapping):
        payload = dict(normalized.get("act") or {})
        normalized.setdefault("act_profile", payload.get("act_profile"))
        normalized.setdefault("execution_target", payload.get("execution_target"))
        normalized.setdefault("max_steps_hint", payload.get("max_steps_hint"))
        normalized.setdefault("rationale", payload.get("rationale"))
    if route == "plan" and isinstance(normalized.get("plan"), Mapping):
        payload = dict(normalized.get("plan") or {})
        normalized.setdefault("plan_strategy", payload.get("plan_strategy"))
        normalized.setdefault("plan_hint", payload.get("plan_hint"))
        normalized.setdefault("plan_outline", payload.get("plan_outline"))
        normalized.setdefault("tasks", payload.get("tasks"))
        normalized.setdefault("subtasks", payload.get("subtasks"))
    normalized.pop("respond", None)
    normalized.pop("act", None)
    normalized.pop("plan", None)
    if str(normalized.get("route") or normalized.get("mode") or "").strip() == "plan":
        import logging as _logging

        _logging.getLogger(__name__).debug(
            "compat bridge: rewriting route='plan' -> route='act' "
            "act_profile='orchestrate'"
        )
        normalized["route"] = "act"
        has_subtasks = bool(normalized.get("subtasks"))
        normalized.setdefault(
            "act_profile", "orchestrate" if has_subtasks else "general"
        )
        if not normalized.get("execution_target"):
            normalized["execution_target"] = {"kind": "local"}
        plan_hint = str(normalized.pop("plan_hint", "") or "").strip()
        if plan_hint and not normalized.get("rationale"):
            normalized["rationale"] = plan_hint
        normalized.pop("plan_strategy", None)
        normalized.pop("plan_outline", None)
        normalized.pop("tasks", None)
    normalized.pop("mode", None)
    return normalized


def _truncate_at_word_boundary(text: str, *, limit: int) -> str:
    normalized = str(text or "").strip()
    if len(normalized) <= limit:
        return normalized
    candidate = normalized[:limit].rstrip()
    boundary = candidate.rfind(" ")
    if boundary > 0:
        candidate = candidate[:boundary].rstrip()
    return candidate


def normalize_session_work_summary(value: Any) -> str:
    normalized = str(value or "").strip()
    return _truncate_at_word_boundary(
        normalized,
        limit=_SESSION_WORK_SUMMARY_MAX_CHARS,
    )


def normalize_delegation_summary(value: Any) -> str:
    return _truncate_at_word_boundary(
        str(value or "").strip(),
        limit=DELEGATION_TEXT_MAX_CHARS,
    )


def normalize_artifact_refs(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        raw_items = [item.strip() for item in value.split(",")]
    elif isinstance(value, list | tuple):
        raw_items = [str(item or "").strip() for item in value]
    else:
        raise ValueError("artifact_refs must be a sequence")
    normalized: list[str] = []
    for item in raw_items:
        if item and item not in normalized:
            normalized.append(item)
        if len(normalized) >= DELEGATION_ARTIFACT_REF_LIMIT:
            break
    return normalized
