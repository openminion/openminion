from openminion.base.time import utc_now_iso  # noqa: F401
from urllib.parse import quote, unquote

VALID_SESSION_STATUSES = {"active", "idle", "paused", "stale", "closed"}
VALID_PARTICIPANT_TYPES = {"agent", "human"}
ROOM_SESSION_KEY_PREFIX = "room:"


def normalize_identity(value: str) -> str:
    compact = " ".join(value.strip().split())
    return compact.lower()


def normalize_session_status(raw: str) -> str:
    value = str(raw or "").strip().lower()
    if value not in VALID_SESSION_STATUSES:
        raise ValueError(f"Invalid session status: {raw!r}")
    return value


def normalize_participant_type(raw: str) -> str:
    value = str(raw or "").strip().lower()
    if value not in VALID_PARTICIPANT_TYPES:
        raise ValueError(f"Invalid participant type: {raw!r}")
    return value


def normalize_participant_role(raw: str) -> str:
    value = str(raw or "").strip().lower()
    return value or "participant"


def is_room_session_key(session_key: str) -> bool:
    return str(session_key or "").strip().lower().startswith(ROOM_SESSION_KEY_PREFIX)


def agent_id_from_session_key(session_key: str) -> str:
    if is_room_session_key(session_key):
        return ""
    for part in (session_key or "").split("|"):
        if part.startswith("agent:"):
            return unquote(part[len("agent:") :])
    return ""


def build_session_key(*, agent_id: str, channel: str, target: str) -> str:
    normalized_agent = quote(normalize_identity(agent_id), safe="")
    normalized_channel = quote(normalize_identity(channel), safe="")
    normalized_target = quote(normalize_identity(target), safe="")
    return f"agent:{normalized_agent}|channel:{normalized_channel}|target:{normalized_target}"


def build_explicit_session_key(
    *,
    agent_id: str,
    channel: str,
    target: str,
    session_id: str,
) -> str:
    normalized_session_id = quote(str(session_id or "").strip(), safe="")
    return (
        f"{build_session_key(agent_id=agent_id, channel=channel, target=target)}"
        f"|session:{normalized_session_id}"
    )


def build_room_session_key(*, session_id: str) -> str:
    normalized_session_id = quote(str(session_id or "").strip(), safe="")
    if not normalized_session_id:
        raise ValueError("room session_id is required")
    return f"{ROOM_SESSION_KEY_PREFIX}{normalized_session_id}"
