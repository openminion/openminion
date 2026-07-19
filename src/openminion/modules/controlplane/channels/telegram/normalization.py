from typing import Any

from openminion.modules.controlplane.contracts.inbound import (
    canonicalize_inbound_message,
)
from openminion.modules.controlplane.contracts.models import InboundMessage

from openminion.modules.controlplane.channels.telegram.models import (
    ControlEvent,
    TelegramInboundEnvelope,
    TelegramReplyTarget,
    TelegramUser,
)


def extract_envelope(update: dict[str, Any]) -> TelegramInboundEnvelope | None:
    if not isinstance(update, dict):
        return None
    try:
        update_id = int(update.get("update_id"))
    except (TypeError, ValueError):
        return None

    if isinstance(update.get("message"), dict):
        return _from_message_update(update_id, "message", update["message"], update)
    if isinstance(update.get("edited_message"), dict):
        return _from_message_update(
            update_id, "edited_message", update["edited_message"], update
        )
    if isinstance(update.get("callback_query"), dict):
        return _from_callback_query(update_id, update["callback_query"], update)
    return None


def to_control_event(envelope: TelegramInboundEnvelope) -> ControlEvent:
    return ControlEvent(
        channel="telegram",
        conversation_id=str(envelope.chat_id),
        thread_id=str(envelope.topic_id) if envelope.topic_id is not None else None,
        message_id=str(envelope.message_id),
        from_user={
            "id": str(envelope.from_user.id),
            "username": envelope.from_user.username,
            "display": envelope.from_user.display,
        },
        text=envelope.text,
        attachments=list(envelope.attachments),
        metadata={
            "update_id": envelope.update_id,
            "is_group": envelope.is_group,
            "is_topic": envelope.is_topic,
            "raw_type": envelope.raw_type,
            "chat_id": str(envelope.chat_id),
            "topic_id": str(envelope.topic_id)
            if envelope.topic_id is not None
            else None,
        },
    )


def to_inbound_message(
    envelope: TelegramInboundEnvelope,
    *,
    normalized_text: str,
    control_event: ControlEvent,
    extra_meta: dict[str, Any] | None = None,
) -> InboundMessage:
    chat_key = session_scope_key(envelope.chat_id, envelope.topic_id)
    merged_extra = dict(extra_meta or {})
    canonical_metadata = {
        "telegram": {
            "chat_id": envelope.chat_id,
            "message_id": envelope.message_id,
            "topic_id": envelope.topic_id,
            "from_user_id": envelope.from_user.id,
            "from_username": envelope.from_user.username,
            "update_id": envelope.update_id,
            "raw_type": envelope.raw_type,
            "chat_type": envelope.chat_type,
            "attachments": list(envelope.attachments),
        },
        "control_event": {
            "conversation_id": control_event.conversation_id,
            "thread_id": control_event.thread_id,
            "message_id": control_event.message_id,
        },
        **merged_extra,
    }
    inbound = InboundMessage(
        user_key=f"telegram:{envelope.from_user.id}",
        chat_key=chat_key,
        text=normalized_text,
        channel="telegram",
        thread_key=f"telegram-topic:{envelope.topic_id}"
        if envelope.topic_id is not None
        else None,
        chat_id=str(envelope.chat_id),
        user_id=str(envelope.from_user.id),
        thread_id=str(envelope.topic_id) if envelope.topic_id is not None else None,
        metadata=canonical_metadata,
        meta=dict(canonical_metadata),
    )
    return canonicalize_inbound_message(inbound)


def to_reply_target(envelope: TelegramInboundEnvelope) -> TelegramReplyTarget:
    return TelegramReplyTarget(
        chat_id=envelope.chat_id,
        message_id=envelope.message_id,
        topic_id=envelope.topic_id,
    )


def session_scope_key(chat_id: int, topic_id: int | None) -> str:
    if topic_id is None:
        return f"telegram:{chat_id}"
    return f"telegram:{chat_id}:{topic_id}"


def _from_message_update(
    update_id: int,
    raw_type: str,
    message: dict[str, Any],
    raw_update: dict[str, Any],
) -> TelegramInboundEnvelope | None:
    chat = message.get("chat")
    from_user = message.get("from")
    if not isinstance(chat, dict) or not isinstance(from_user, dict):
        return None

    try:
        chat_id = int(chat.get("id"))
        message_id = int(message.get("message_id"))
        user_id = int(from_user.get("id"))
    except (TypeError, ValueError):
        return None

    text = str(message.get("text") or message.get("caption") or "")
    topic_id = _as_int_or_none(message.get("message_thread_id"))
    attachments = _extract_attachments(message)

    return TelegramInboundEnvelope(
        update_id=update_id,
        raw_type=raw_type,
        chat_id=chat_id,
        message_id=message_id,
        text=text,
        from_user=TelegramUser(
            id=user_id,
            username=_as_str_or_none(from_user.get("username")),
            display=_display_name(from_user),
        ),
        chat_type=str(chat.get("type") or ""),
        topic_id=topic_id,
        attachments=attachments,
        raw_update=raw_update,
    )


def _from_callback_query(
    update_id: int,
    callback_query: dict[str, Any],
    raw_update: dict[str, Any],
) -> TelegramInboundEnvelope | None:
    message = callback_query.get("message")
    from_user = callback_query.get("from")
    if not isinstance(message, dict) or not isinstance(from_user, dict):
        return None

    chat = message.get("chat")
    if not isinstance(chat, dict):
        return None

    try:
        chat_id = int(chat.get("id"))
        message_id = int(message.get("message_id"))
        user_id = int(from_user.get("id"))
    except (TypeError, ValueError):
        return None

    topic_id = _as_int_or_none(message.get("message_thread_id"))
    text = str(callback_query.get("data") or "")

    return TelegramInboundEnvelope(
        update_id=update_id,
        raw_type="callback_query",
        chat_id=chat_id,
        message_id=message_id,
        text=text,
        from_user=TelegramUser(
            id=user_id,
            username=_as_str_or_none(from_user.get("username")),
            display=_display_name(from_user),
        ),
        chat_type=str(chat.get("type") or ""),
        topic_id=topic_id,
        callback_query_id=_as_str_or_none(callback_query.get("id")),
        raw_update=raw_update,
    )


def _display_name(from_user: dict[str, Any]) -> str | None:
    first = _as_str_or_none(from_user.get("first_name"))
    last = _as_str_or_none(from_user.get("last_name"))
    full = " ".join([part for part in [first, last] if part])
    if full:
        return full
    return _as_str_or_none(from_user.get("username"))


def _as_str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _as_int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _extract_attachments(message: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    # For photos Telegram provides multiple sizes; keep the largest element.
    photos = message.get("photo")
    if isinstance(photos, list) and photos:
        largest = None
        largest_area = -1
        for item in photos:
            if not isinstance(item, dict):
                continue
            fid = _as_str_or_none(item.get("file_id"))
            if not fid:
                continue
            width = _as_int_or_none(item.get("width")) or 0
            height = _as_int_or_none(item.get("height")) or 0
            area = width * height
            if area >= largest_area:
                largest = fid
                largest_area = area
        if largest:
            out.append(
                {
                    "kind": "photo",
                    "source": "upload",
                    "ref": f"tgfile:{largest}",
                    "file_id": largest,
                }
            )

    for kind in ("document", "audio", "voice", "video"):
        value = message.get(kind)
        if not isinstance(value, dict):
            continue
        file_id = _as_str_or_none(value.get("file_id"))
        if not file_id:
            continue
        out.append(
            {
                "kind": kind,
                "source": "upload",
                "ref": f"tgfile:{file_id}",
                "file_id": file_id,
            }
        )

    return out
