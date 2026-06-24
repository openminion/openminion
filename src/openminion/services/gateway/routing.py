from typing import Any

from openminion.modules.storage.runtime.session_store import SessionStore


def parse_metadata_bool(metadata: dict[str, str], key: str) -> bool:
    value = str(metadata.get(key, "") or "").strip().lower()
    if not value:
        return False
    return value in {"1", "true", "yes", "on"}


def find_pending_outbound(
    sessions: SessionStore,
    *,
    session_id: str,
    conversation_id: str | None,
    thread_id: str | None,
) -> Any | None:
    records = sessions.list_recent_messages(
        session_id=session_id,
        limit=50,
        conversation_id=conversation_id or None,
        thread_id=thread_id or None,
    )
    for record in reversed(records):
        if record.role == "outbound":
            return record
    return None


def build_lifecycle_payload(
    *,
    conversation_id: str,
    thread_id: str,
    attach_id: str,
    routing_action: str,
    routing_reason: str,
    thread_state: str,
    qualifier: str,
) -> dict[str, str]:
    payload: dict[str, str] = {}
    if conversation_id:
        payload["conversation_id"] = conversation_id
    if thread_id:
        payload["thread_id"] = thread_id
    if attach_id:
        payload["attach_id"] = attach_id
    payload["thread_decision_action"] = routing_action
    payload["thread_decision_reason"] = routing_reason
    payload["thread_state_before"] = thread_state
    payload["thread_state_qualifier"] = qualifier
    return payload
