import json
from typing import Mapping

from openminion.modules.context.schemas import (
    SessionSlice,
    SessionToolEvent,
    SessionTurn,
)
from openminion.modules.storage.runtime.session_store import SessionStore
from openminion.base.constants import STATE_KEY_ACTIVE

RUNTIME_SESSION_SLICE_BRIDGE_VERSION = "runtime-bridge:v1"
SUMMARY_SHORT_SOURCE = "session_context.summary_short"


def build_session_slice_from_runtime_store(
    *,
    store: SessionStore,
    session_id: str,
    limits: Mapping[str, int],
    slice_version: str = "runtime-map:v1",
) -> SessionSlice:
    session = store.get_session(session_id)
    if session is None:
        return SessionSlice(
            session_id=session_id,
            slice_version=slice_version,
            last_event_id=None,
            summary_short="",
            summary_long=None,
            conversation_summary="",
            active_task_plan=None,
            total_turn_count=0,
            recent_turns=[],
            open_tasks=[],
            active_state={},
            recent_tool_events=[],
            prompt_context_id=None,
            checkpoint_id=None,
            seed_bundle_id=None,
            archive_refs=[],
        )

    recent_turn_limit = max(1, int(limits.get("recent_turn_limit", 8)))
    tool_events_limit = max(0, int(limits.get("tool_events_limit", 3)))
    recent = store.list_recent_messages(session_id=session_id, limit=recent_turn_limit)
    turns = [
        _map_message_to_turn(item.id, item.role, item.body, item.created_at)
        for item in recent
    ]
    total_turn_count = len(
        store.list_recent_messages(session_id=session_id, limit=10_000)
    )

    last_event = store.list_events(session_id=session_id, limit=1, newest_first=True)
    last_event_id = str(last_event[0].id) if last_event else None

    context = store.get_session_context(session_id=session_id)
    summary_long = (
        context.rolling_summary.strip() if context and context.rolling_summary else None
    )
    summary_short = (
        context.summary_short.strip() if context and context.summary_short else ""
    )
    archive_refs = map_archive_refs(store=store, session_id=session_id)
    tool_events = map_tool_events(
        store=store, session_id=session_id, limit=tool_events_limit
    )
    open_tasks = []
    active_state = {}
    if isinstance(session.metadata, dict):
        maybe_tasks = session.metadata.get("open_tasks")
        if isinstance(maybe_tasks, list):
            open_tasks = [str(item) for item in maybe_tasks if str(item).strip()]
        maybe_state = session.metadata.get(STATE_KEY_ACTIVE)
        if isinstance(maybe_state, dict):
            active_state = dict(maybe_state)

    return SessionSlice(
        session_id=session_id,
        slice_version=slice_version,
        last_event_id=last_event_id,
        summary_short=summary_short,
        summary_long=summary_long,
        conversation_summary="",
        active_task_plan=None,
        total_turn_count=total_turn_count,
        recent_turns=turns,
        open_tasks=open_tasks,
        active_state=active_state,
        recent_tool_events=tool_events,
        prompt_context_id=None,
        checkpoint_id=None,
        seed_bundle_id=None,
        archive_refs=archive_refs,
    )


def map_archive_refs(*, store: SessionStore, session_id: str) -> list[str]:
    events = store.list_events(
        session_id=session_id,
        limit=5,
        newest_first=True,
        event_type_prefix="session.compaction.archive",
    )
    refs: list[str] = []
    for event in reversed(events):
        payload = event.payload if isinstance(event.payload, dict) else {}
        ref = str(payload.get("relative_path") or payload.get("path") or "").strip()
        if ref and ref not in refs:
            refs.append(ref)
    return refs


def map_tool_events(
    *, store: SessionStore, session_id: str, limit: int
) -> list[SessionToolEvent]:
    if limit <= 0:
        return []
    events = store.list_events(
        session_id=session_id, limit=max(limit * 4, 20), newest_first=True
    )
    collected: list[SessionToolEvent] = []
    for event in reversed(events):
        payload = event.payload if isinstance(event.payload, dict) else {}
        tool_name = str(payload.get("tool_name") or payload.get("tool") or "").strip()
        if not tool_name and not str(event.event_type).startswith("tool."):
            continue
        if not tool_name:
            tool_name = str(event.event_type)
        excerpt = str(
            payload.get("summary")
            or payload.get("message")
            or payload.get("error")
            or ""
        ).strip()
        if not excerpt:
            excerpt = (
                json.dumps(payload, sort_keys=True)[:240]
                if payload
                else str(event.event_type)
            )
        raw_refs = payload.get("artifact_refs", [])
        artifact_refs = (
            [str(item) for item in raw_refs] if isinstance(raw_refs, list) else []
        )
        collected.append(
            SessionToolEvent(
                event_id=str(event.id),
                tool_name=tool_name,
                excerpt=excerpt[:240],
                artifact_refs=artifact_refs,
            )
        )
        if len(collected) >= limit:
            break
    return collected


def _map_message_to_turn(message_id: str, role: str, body: str, ts: str) -> SessionTurn:
    normalized_role = str(role or "").strip().lower()
    if normalized_role in {"inbound", "user"}:
        mapped_role = "user"
    elif normalized_role in {"outbound", "assistant"}:
        mapped_role = "assistant"
    else:
        mapped_role = normalized_role or "user"
    return SessionTurn(
        turn_id=message_id,
        role=mapped_role,
        content=str(body or ""),
        ts=str(ts or "") or None,
        is_error=False,
    )
