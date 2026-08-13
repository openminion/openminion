from typing import Any

from openminion.modules.storage.runtime.session_store import SessionStore


def parse_metadata_bool(metadata: dict[str, str], key: str) -> bool:
    return metadata.get(key, "").strip().lower() in {"1", "true", "yes", "on"}


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
    return {
        **({"conversation_id": conversation_id} if conversation_id else {}),
        **({"thread_id": thread_id} if thread_id else {}),
        **({"attach_id": attach_id} if attach_id else {}),
        "thread_decision_action": routing_action,
        "thread_decision_reason": routing_reason,
        "thread_state_before": thread_state,
        "thread_state_qualifier": qualifier,
    }
