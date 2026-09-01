"""Recover durable terminal capture intents through the foreground bundle path."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from openminion.modules.memory.errors import (
    CaptureIdentityMismatchError,
    CaptureRecoveryUnauthorizedError,
    CaptureSourceUnavailableError,
)
from openminion.modules.session.capture import build_capture_identity


@dataclass(frozen=True)
class CaptureRecoveryResult:
    scanned: int
    recovered: int
    pending: int


def recover_pending_capture_bundles(
    *,
    sessions: Any,
    memctl: Any,
    agent_id: str,
    extract_candidates: Callable[[Any, str, str, str], list[dict[str, Any]]],
    authorize: Callable[[str, str, str, str], bool],
    limit: int = 32,
) -> CaptureRecoveryResult:
    selected = _pending_capture_events(
        sessions=sessions,
        agent_id=agent_id,
        limit=limit,
    )
    recovered = pending = 0
    for session, event in selected:
        try:
            _recover_capture(
                sessions=sessions,
                memctl=memctl,
                agent_id=agent_id,
                session=session,
                event=event,
                extract_candidates=extract_candidates,
                authorize=authorize,
            )
        except RuntimeError:
            pending += 1
            continue
        recovered += 1
    return CaptureRecoveryResult(
        scanned=len(selected),
        recovered=recovered,
        pending=pending,
    )


def _pending_capture_events(
    *,
    sessions: Any,
    agent_id: str,
    limit: int,
) -> list[tuple[Any, Any]]:
    candidates: list[tuple[str, str, Any, Any]] = []
    session_count = sessions.count_sessions()
    for session in sessions.list_sessions(
        limit=max(1, int(session_count)),
        newest_first=False,
    ):
        current_agent_id = str(session.owner_agent_id or "").strip()
        if current_agent_id != agent_id:
            continue
        event_count = sessions.count_events(
            session_id=session.id,
            event_type_prefix="turn.outcome",
        )
        for event in sessions.list_events(
            session_id=session.id,
            limit=max(1, int(event_count)),
            event_type_prefix="turn.outcome",
        ):
            payload = event.payload
            if str(payload.get("agent_id", "") or "").strip() != agent_id:
                continue
            capture_id = str(payload.get("capture_id", "") or "").strip()
            root_turn_id = str(payload.get("root_turn_id", "") or "").strip()
            event_id = str(event.canonical_event_id or "").strip()
            if (
                str(payload.get("capture_state", "") or "").strip() != "pending"
                or not capture_id
                or not root_turn_id
                or not event_id
            ):
                continue
            if (
                sessions.get_event_by_canonical_id(
                    f"memory.capture.result:{capture_id}"
                )
                is not None
            ):
                continue
            candidates.append((event.created_at, session.id, session, event))

    return [
        (session, event)
        for _created_at, _session_id, session, event in sorted(
            candidates,
            key=lambda item: (item[0], item[1], str(item[3].canonical_event_id or "")),
        )[: max(1, int(limit))]
    ]


def _recover_capture(
    *,
    sessions: Any,
    memctl: Any,
    agent_id: str,
    session: Any,
    event: Any,
    extract_candidates: Callable[[Any, str, str, str], list[dict[str, Any]]],
    authorize: Callable[[str, str, str, str], bool],
) -> None:
    payload = event.payload
    capture_id = str(payload.get("capture_id", "") or "").strip()
    root_turn_id = str(payload.get("root_turn_id", "") or "").strip()
    identity = build_capture_identity(
        runtime_session_id=session.id,
        root_turn_id=root_turn_id,
    )
    if identity.event_id != str(event.canonical_event_id or "").strip() or (
        identity.capture_id != capture_id
    ):
        raise CaptureIdentityMismatchError("capture identity is not canonical")
    if not authorize(session.id, root_turn_id, identity.event_id, identity.capture_id):
        raise CaptureRecoveryUnauthorizedError("capture recovery is not authorized")
    source_body = _capture_source_body(
        sessions=sessions,
        session_id=session.id,
        root_turn_id=root_turn_id,
    )
    items = extract_candidates(
        sessions,
        session.id,
        root_turn_id,
        source_body,
    )
    result = memctl.apply_capture_bundle(
        capture_id=capture_id,
        root_turn_id=root_turn_id,
        session_id=session.id,
        agent_id=agent_id,
        candidates=items,
    )
    sessions.commit_capture_result_and_release_hold(
        session_id=identity.runtime_session_id,
        canonical_event_id=f"memory.capture.result:{identity.capture_id}",
        capture_id=identity.capture_id,
        payload={
            "schema_version": identity.schema_version,
            "runtime_session_id": identity.runtime_session_id,
            "root_turn_id": identity.root_turn_id,
            "capture_id": identity.capture_id,
            **result,
        },
    )


def _capture_source_body(
    *,
    sessions: Any,
    session_id: str,
    root_turn_id: str,
) -> str:
    message_count = sessions.count_messages(session_id=session_id)
    source = next(
        (
            message
            for message in sessions.list_messages(
                session_id=session_id,
                limit=max(1, int(message_count)),
            )
            if message.role == "inbound"
            and str(message.metadata.get("run_id", "") or "").strip() == root_turn_id
        ),
        None,
    )
    if source is None:
        raise CaptureSourceUnavailableError("capture source message is unavailable")
    return str(source.body)
