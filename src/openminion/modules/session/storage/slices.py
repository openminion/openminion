"""Session storage slice assembly."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol, runtime_checkable

from .json_utils import deep_copy, to_json
from openminion.base.constants import STATE_KEY_ACTIVE
from openminion.modules.session.constants import MAX_TASK_ANCHOR_SCAN_TURNS

_TASK_ANCHOR_PURPOSES = {"act", "entry", "judge", "summarize", "validate"}


@runtime_checkable
class SessionSliceSource(Protocol):
    def get_session(self, session_id: str) -> dict[str, Any] | None: ...

    def latest_event_seq(self, session_id: str) -> int: ...

    def get_summary(self, session_id: str, *, variant: str = "short") -> str: ...

    def get_recent_turns(
        self, session_id: str, limit_messages: int
    ) -> list[dict[str, Any]]: ...

    def get_total_turn_count(self, session_id: str) -> int: ...

    def get_conversation_summary(
        self, session_id: str, *, limit_records: int = 24
    ) -> str: ...

    def get_active_task_plan(self, session_id: str) -> dict[str, Any] | None: ...

    def get_pending_trailer_feedback(
        self, session_id: str
    ) -> dict[str, Any] | None: ...

    def derive_open_tasks(
        self, *, session_id: str, upto_seq: int | None = None
    ) -> list[dict[str, Any]]: ...

    def get_active_state(self, session_id: str) -> dict[str, Any]: ...

    def get_recent_tool_events(
        self, session_id: str, limit: int
    ) -> list[dict[str, Any]]: ...

    def get_active_prompt_context(self, session_id: str) -> dict[str, Any] | None: ...

    def get_latest_checkpoint(self, session_id: str) -> dict[str, Any] | None: ...

    def get_latest_seed_bundle(self, session_id: str) -> dict[str, Any] | None: ...

    def list_recent_archive_ref_lines(
        self, *, session_id: str, limit: int
    ) -> list[str]: ...


@dataclass(frozen=True)
class SessionSliceSourceAdapter:
    session_getter: Callable[[str], dict[str, Any] | None]
    latest_event_seq_getter: Callable[[str], int]
    summary_getter: Callable[[str], str]
    recent_turns_getter: Callable[[str, int], list[dict[str, Any]]]
    total_turn_count_getter: Callable[[str], int]
    conversation_summary_getter: Callable[..., str]
    active_task_plan_getter: Callable[[str], dict[str, Any] | None]
    pending_trailer_feedback_getter: Callable[[str], dict[str, Any] | None]
    open_tasks_getter: Callable[..., list[dict[str, Any]]]
    active_state_getter: Callable[[str], dict[str, Any]]
    recent_tool_events_getter: Callable[[str, int], list[dict[str, Any]]]
    prompt_context_getter: Callable[[str], dict[str, Any] | None]
    checkpoint_getter: Callable[[str], dict[str, Any] | None]
    seed_bundle_getter: Callable[[str], dict[str, Any] | None]
    archive_refs_getter: Callable[..., list[str]]

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        return self.session_getter(session_id)

    def latest_event_seq(self, session_id: str) -> int:
        return self.latest_event_seq_getter(session_id)

    def get_summary(self, session_id: str, *, variant: str = "short") -> str:
        return self.summary_getter(session_id, variant=variant)

    def get_recent_turns(
        self, session_id: str, limit_messages: int
    ) -> list[dict[str, Any]]:
        return self.recent_turns_getter(session_id, limit_messages)

    def get_total_turn_count(self, session_id: str) -> int:
        return self.total_turn_count_getter(session_id)

    def get_conversation_summary(
        self, session_id: str, *, limit_records: int = 24
    ) -> str:
        return self.conversation_summary_getter(
            session_id,
            limit_records=limit_records,
        )

    def get_active_task_plan(self, session_id: str) -> dict[str, Any] | None:
        return self.active_task_plan_getter(session_id)

    def get_pending_trailer_feedback(self, session_id: str) -> dict[str, Any] | None:
        return self.pending_trailer_feedback_getter(session_id)

    def derive_open_tasks(
        self, *, session_id: str, upto_seq: int | None = None
    ) -> list[dict[str, Any]]:
        return self.open_tasks_getter(session_id=session_id, upto_seq=upto_seq)

    def get_active_state(self, session_id: str) -> dict[str, Any]:
        return self.active_state_getter(session_id)

    def get_recent_tool_events(
        self, session_id: str, limit: int
    ) -> list[dict[str, Any]]:
        return self.recent_tool_events_getter(session_id, limit)

    def get_active_prompt_context(self, session_id: str) -> dict[str, Any] | None:
        return self.prompt_context_getter(session_id)

    def get_latest_checkpoint(self, session_id: str) -> dict[str, Any] | None:
        return self.checkpoint_getter(session_id)

    def get_latest_seed_bundle(self, session_id: str) -> dict[str, Any] | None:
        return self.seed_bundle_getter(session_id)

    def list_recent_archive_ref_lines(
        self, *, session_id: str, limit: int
    ) -> list[str]:
        return self.archive_refs_getter(session_id=session_id, limit=limit)


class SliceStore:
    def __init__(
        self,
        source: SessionSliceSource,
        *,
        lock,
        slice_cache: dict[tuple[str, str, str, int], dict[str, Any]],
        normalize_limits: Callable[[Any], dict[str, Any]],
        stable_hash: Callable[[Any], str],
    ) -> None:
        self._source = source
        self._lock = lock
        self._slice_cache = slice_cache
        self._normalize_limits = normalize_limits
        self._stable_hash = stable_hash
        self._validate_source(source)

    @staticmethod
    def _validate_source(source: SessionSliceSource) -> None:
        required = (
            "get_session",
            "latest_event_seq",
            "get_summary",
            "get_recent_turns",
            "get_total_turn_count",
            "get_conversation_summary",
            "get_active_task_plan",
            "get_pending_trailer_feedback",
            "derive_open_tasks",
            "get_active_state",
            "get_recent_tool_events",
            "get_active_prompt_context",
            "get_latest_checkpoint",
            "get_latest_seed_bundle",
            "list_recent_archive_ref_lines",
        )
        missing = [
            name for name in required if not callable(getattr(source, name, None))
        ]
        if missing:
            raise TypeError(
                "SliceStore source is missing required callables: "
                + ", ".join(sorted(missing))
            )

    def _get_slice_base(
        self, session_id: str, purpose: str, limits: Any
    ) -> dict[str, Any]:
        normalized = self._normalize_limits(limits)
        with self._lock:
            session = self._source.get_session(session_id)
            if session is None:
                raise ValueError(f"session not found: {session_id}")
            last_event_seq = self._source.latest_event_seq(session_id)

            cache_key = (
                session_id,
                str(purpose),
                to_json(normalized),
                last_event_seq,
            )
            cached = self._slice_cache.get(cache_key)
            if cached is not None:
                return deep_copy(cached)

        summary = self._source.get_summary(
            session_id, variant=str(normalized["summary_variant"])
        )
        recent_turns = self._source.get_recent_turns(
            session_id, int(normalized["max_turns"])
        )
        total_turn_count = self._source.get_total_turn_count(session_id)
        recent_turns = self._with_task_anchor_turn(
            session_id=session_id,
            purpose=purpose,
            recent_turns=recent_turns,
            total_turn_count=total_turn_count,
        )
        conversation_summary = self._source.get_conversation_summary(session_id)
        active_task_plan = self._source.get_active_task_plan(session_id)
        pending_trailer_feedback = self._source.get_pending_trailer_feedback(session_id)
        open_tasks = (
            self._source.derive_open_tasks(
                session_id=session_id, upto_seq=last_event_seq
            )
            if bool(normalized["include_open_tasks"])
            else []
        )
        active_state = (
            self._source.get_active_state(session_id)
            if bool(normalized["include_active_state"])
            else {}
        )
        recent_tool_events = self._source.get_recent_tool_events(
            session_id, int(normalized["max_tool_events"])
        )

        base_payload = {
            "session_id": session_id,
            "summary": summary,
            "conversation_summary": conversation_summary,
            "active_task_plan": active_task_plan,
            "pending_trailer_feedback": pending_trailer_feedback,
            "total_turn_count": total_turn_count,
            "recent_turns": recent_turns,
            "open_tasks": open_tasks,
            STATE_KEY_ACTIVE: active_state,
            "recent_tool_events": recent_tool_events,
            "last_event_seq": last_event_seq,
            "active_agent_id": session.get("active_agent_id"),
            "active_profile_version": session.get("active_profile_version"),
            "purpose": str(purpose),
            "limits": normalized,
        }
        slice_version = self._stable_hash(base_payload)
        result = {
            "session_id": session_id,
            "slice_version": slice_version,
            "summary": summary,
            "conversation_summary": conversation_summary,
            "active_task_plan": active_task_plan,
            "pending_trailer_feedback": pending_trailer_feedback,
            "total_turn_count": total_turn_count,
            "recent_turns": recent_turns,
            "open_tasks": open_tasks,
            STATE_KEY_ACTIVE: active_state,
            "recent_tool_events": recent_tool_events,
            "last_event_seq": last_event_seq,
            "active_agent_id": session.get("active_agent_id"),
            "active_profile_version": session.get("active_profile_version"),
        }

        with self._lock:
            self._slice_cache[cache_key] = deep_copy(result)
        return result

    def _with_task_anchor_turn(
        self,
        *,
        session_id: str,
        purpose: str,
        recent_turns: list[dict[str, Any]],
        total_turn_count: int,
    ) -> list[dict[str, Any]]:
        """Carry the first user task into non-decide continuation slices."""
        normalized_purpose = str(purpose or "").strip().lower()
        if normalized_purpose not in _TASK_ANCHOR_PURPOSES:
            return recent_turns
        recent_ids = {_turn_identity(turn) for turn in recent_turns}
        scan_limit = min(
            max(len(recent_turns) * 2, int(total_turn_count), 8),
            MAX_TASK_ANCHOR_SCAN_TURNS,
        )
        while True:
            scanned_turns = self._source.get_recent_turns(session_id, scan_limit)
            anchor = next(
                (turn for turn in scanned_turns if _turn_role(turn) == "user"),
                None,
            )
            if anchor is not None and _turn_identity(anchor) not in recent_ids:
                return [anchor, *recent_turns]
            if len(scanned_turns) < scan_limit:
                return recent_turns
            if scan_limit >= MAX_TASK_ANCHOR_SCAN_TURNS:
                return recent_turns
            scan_limit = min(scan_limit * 2, MAX_TASK_ANCHOR_SCAN_TURNS)
        return recent_turns

    def get_slice(
        self,
        session_id: str,
        purpose: str,
        limits: Any | None = None,
    ) -> dict[str, Any]:
        if limits is None:
            limits = {}
        normalized_limits = self._normalize_limits(limits)
        base_slice = self._get_slice_base(session_id, purpose, limits)
        prompt_ctx = self._source.get_active_prompt_context(session_id)
        latest_cp = self._source.get_latest_checkpoint(session_id)
        latest_seed = self._source.get_latest_seed_bundle(session_id)
        archive_refs = self._source.list_recent_archive_ref_lines(
            session_id=session_id,
            limit=int(normalized_limits["archive_ref_limit"]),
        )
        base_slice["prompt_context_id"] = (
            prompt_ctx["prompt_context_id"] if prompt_ctx else None
        )
        base_slice["checkpoint_id"] = latest_cp["checkpoint_id"] if latest_cp else None
        base_slice["seed_bundle_id"] = latest_seed["seed_id"] if latest_seed else None
        base_slice["archive_refs"] = archive_refs
        return base_slice


def _turn_role(turn: dict[str, Any]) -> str:
    role = str(turn.get("role") or turn.get("turn_type") or "").strip().lower()
    if role == "inbound":
        return "user"
    if role == "outbound":
        return "assistant"
    return role


def _turn_identity(turn: dict[str, Any]) -> str:
    for key in ("turn_id", "id", "event_id"):
        value = str(turn.get(key) or "").strip()
        if value:
            return f"{key}:{value}"
    return "content:" + str(turn.get("text") or turn.get("content") or "")
