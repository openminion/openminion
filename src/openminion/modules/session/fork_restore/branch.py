"""Structural session branch diff and selective carry-forward."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
from typing import Any
from uuid import uuid4

from openminion.base.constants import STATE_KEY_WORKING
from openminion.modules.session.interfaces import (
    SESSION_BRANCH_CARRY_FORWARD_SCHEMA_VERSION,
    SESSION_BRANCH_DIFF_SCHEMA_VERSION,
)

_ALLOWED_CARRY_FIELDS = {"summary", STATE_KEY_WORKING, "message_refs"}
_FORBIDDEN_CARRY_FIELDS = {"raw_transcript", "secret", "encrypted_content"}


class SessionBranchError(RuntimeError):
    code = "SESSION_BRANCH_ERROR"


class SessionBranchCarryForwardDeniedError(SessionBranchError):
    code = "SESSION_BRANCH_CARRY_FORWARD_DENIED"


@dataclass(frozen=True)
class BranchDiffItem:
    field: str
    status: str
    left_ref: str | None
    right_ref: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "status": self.status,
            "left_ref": self.left_ref,
            "right_ref": self.right_ref,
        }


@dataclass(frozen=True)
class BranchDiffResult:
    left_session_id: str
    right_session_id: str
    items: tuple[BranchDiffItem, ...]
    schema_version: str = SESSION_BRANCH_DIFF_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "left_session_id": self.left_session_id,
            "right_session_id": self.right_session_id,
            "items": [item.to_dict() for item in self.items],
        }


def diff_session_branches(
    store: Any, *, left_session_id: str, right_session_id: str
) -> BranchDiffResult:
    _require_session(store, left_session_id)
    _require_session(store, right_session_id)
    items = (
        _diff_summary(store, left_session_id, right_session_id),
        _diff_working_state(store, left_session_id, right_session_id),
        _diff_message_refs(store, left_session_id, right_session_id),
    )
    return BranchDiffResult(
        left_session_id=left_session_id,
        right_session_id=right_session_id,
        items=items,
    )


def carry_forward_branch_fields(
    store: Any,
    *,
    source_session_id: str,
    target_parent_session_id: str,
    fields: Sequence[str],
    title: str | None = None,
) -> dict[str, Any]:
    ordered_fields = _normalize_carry_fields(fields)
    _require_session(store, source_session_id)
    _require_session(store, target_parent_session_id)
    child_session_id = store.create_session(
        session_id=f"branch-{uuid4().hex[:12]}",
        title=title or f"Carry-forward from {source_session_id}",
        meta={
            "schema_version": SESSION_BRANCH_CARRY_FORWARD_SCHEMA_VERSION,
            "source_session_id": source_session_id,
            "target_parent_session_id": target_parent_session_id,
            "carried_fields": ordered_fields,
        },
    )
    if "summary" in ordered_fields:
        _carry_summary(store, source_session_id, child_session_id)
    if STATE_KEY_WORKING in ordered_fields:
        _carry_working_state(store, source_session_id, child_session_id)
    if "message_refs" in ordered_fields:
        _carry_message_refs(store, source_session_id, child_session_id)
    store.append_event(
        child_session_id,
        event_type="session.branch.carry_forward.created",
        payload={
            "schema_version": SESSION_BRANCH_CARRY_FORWARD_SCHEMA_VERSION,
            "source_session_id": source_session_id,
            "target_parent_session_id": target_parent_session_id,
            "fields": ordered_fields,
        },
        actor_type="system",
    )
    return {
        "schema_version": SESSION_BRANCH_CARRY_FORWARD_SCHEMA_VERSION,
        "child_session_id": child_session_id,
        "source_session_id": source_session_id,
        "target_parent_session_id": target_parent_session_id,
        "fields": ordered_fields,
    }


def _require_session(store: Any, session_id: str) -> None:
    sid = str(session_id or "").strip()
    if not sid or store.get_session(sid) is None:
        raise SessionBranchError(f"session not found: {session_id}")


def _normalize_carry_fields(fields: Any) -> list[str]:
    if isinstance(fields, (str, bytes, bytearray)) or not isinstance(fields, Sequence):
        raise SessionBranchCarryForwardDeniedError(
            "branch carry-forward requires an explicit structural allowlist"
        )
    if any(not isinstance(field, str) for field in fields):
        raise SessionBranchCarryForwardDeniedError(
            "branch carry-forward requires an explicit structural allowlist"
        )
    normalized = {field.strip() for field in fields if field.strip()}
    forbidden = normalized & _FORBIDDEN_CARRY_FIELDS
    unknown = normalized - _ALLOWED_CARRY_FIELDS - _FORBIDDEN_CARRY_FIELDS
    if not normalized or forbidden or unknown:
        raise SessionBranchCarryForwardDeniedError(
            "branch carry-forward requires an explicit structural allowlist"
        )
    return sorted(normalized)


def _summary_projection(store: Any, session_id: str) -> dict[str, Any]:
    summary_short = store.get_summary(session_id, variant="short")
    summary_long = store.get_summary(session_id, variant="long")
    summaries = store.get_summaries(session_id)
    base_ref = summaries.get("base_ref") if isinstance(summaries, Mapping) else None
    delta_refs = (
        [str(item) for item in (summaries.get("delta_refs") or [])]
        if isinstance(summaries, Mapping)
        else []
    )
    if not summary_short and not summary_long and not base_ref and not delta_refs:
        return {}
    return {
        "summary_short": summary_short,
        "summary_long": summary_long,
        "base_ref": base_ref,
        "delta_refs": delta_refs,
    }


def _working_state_projection(store: Any, session_id: str) -> dict[str, Any]:
    state = store.get_latest_working_state(session_id)
    if not isinstance(state, Mapping):
        return {}
    state_ref = state.get("state_ref")
    state_inline = state.get("state_inline")
    if state_ref is None and state_inline is None:
        return {}
    return {
        "state_ref": state_ref,
        "state_inline": state_inline,
    }


def _diff_summary(store: Any, left: str, right: str) -> BranchDiffItem:
    return _diff_item(
        "summary",
        _stable_ref(_summary_projection(store, left)),
        _stable_ref(_summary_projection(store, right)),
    )


def _diff_working_state(store: Any, left: str, right: str) -> BranchDiffItem:
    return _diff_item(
        STATE_KEY_WORKING,
        _stable_ref(_working_state_projection(store, left)),
        _stable_ref(_working_state_projection(store, right)),
    )


def _diff_message_refs(store: Any, left: str, right: str) -> BranchDiffItem:
    record_store = getattr(store, "_record_store", None)
    left_refs = _message_ref_refs(record_store, left)
    right_refs = _message_ref_refs(record_store, right)
    return _diff_item("message_refs", _stable_ref(left_refs), _stable_ref(right_refs))


def _diff_item(field: str, left_ref: str, right_ref: str) -> BranchDiffItem:
    if left_ref == right_ref:
        status = "same"
    elif left_ref == "empty" or right_ref == "empty":
        status = "added_or_removed"
    else:
        status = "changed"
    return BranchDiffItem(
        field=field, status=status, left_ref=left_ref, right_ref=right_ref
    )


def _carry_summary(store: Any, source: str, child: str) -> None:
    projection = _summary_projection(store, source)
    if not projection:
        return
    if projection["base_ref"] is not None:
        store.set_summary_base(child, str(projection["base_ref"]))
    for delta_ref in projection["delta_refs"]:
        store.append_summary_delta(child, delta_ref)
    summary_short = projection["summary_short"]
    summary_long = projection["summary_long"]
    if summary_short or summary_long:
        store.update_summary(
            child,
            summary_short,
            summary_long=summary_long or None,
            based_on_seq=0,
        )


def _carry_working_state(store: Any, source: str, child: str) -> None:
    projection = _working_state_projection(store, source)
    if not projection:
        return
    inline = projection["state_inline"]
    store.put_working_state(
        child,
        state_ref=projection["state_ref"],
        state_inline=dict(inline) if isinstance(inline, Mapping) else None,
    )


def _carry_message_refs(store: Any, source: str, child: str) -> None:
    record_store = getattr(store, "_record_store", None)
    if record_store is None:
        return
    for row in _message_ref_rows(record_store, source):
        store.add_message_ref(
            session_id=child,
            run_id=row.get("run_id"),
            event_id=row.get("event_id"),
            role=str(row.get("role") or "assistant"),
            content_ref=row.get("content_ref"),
            content_inline=None,
            meta={"source_session_id": source, "source_ref_id": row.get("ref_id")},
        )


def _message_ref_rows(record_store: Any, session_id: str) -> list[dict[str, Any]]:
    if record_store is None:
        return []
    return record_store.query_rows(
        "message_refs", where={"session_id": session_id}, order="seq ASC"
    )


def _message_ref_refs(record_store: Any, session_id: str) -> list[dict[str, Any]]:
    return [
        {"role": row.get("role"), "content_ref": row.get("content_ref")}
        for row in _message_ref_rows(record_store, session_id)
    ]


def _stable_ref(value: Mapping[str, Any] | list[dict[str, Any]]) -> str:
    if not value:
        return "empty"
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode("utf-8")).hexdigest()
