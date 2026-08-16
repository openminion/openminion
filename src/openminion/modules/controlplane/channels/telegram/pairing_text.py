from __future__ import annotations


def extract_start_token(text: str, *, bot_username: str | None) -> str | None:
    stripped = (text or "").strip()
    if not stripped:
        return None

    parts = stripped.split(maxsplit=1)
    head = parts[0]
    if not head.startswith("/"):
        return None

    cmd = head[1:]
    if "@" in cmd:
        name, target_bot = cmd.split("@", 1)
        if bot_username and target_bot.lower() != bot_username.lower():
            return None
        cmd = name

    if cmd.lower() != "start" or len(parts) < 2:
        return None
    return parts[1].split()[0]


def format_pairing_status(
    *, paired: bool, chat_id: int, user_id: int, topic_id: int | None
) -> str:
    if not paired:
        return (
            "This chat is not paired. Ask the OpenMinion owner to run:\n"
            "openminion channel telegram pair --config <profile.json> "
            f"--user-id {user_id} --chat-id {chat_id}\n"
            "Then send the /start link they provide."
        )
    session_scope = str(chat_id)
    if topic_id is not None:
        session_scope = f"{session_scope}:{topic_id}"
    return (
        "Paired ✅\n"
        f"chat_id={chat_id}\n"
        f"user_id={user_id}\n"
        f"session_scope={session_scope}\n"
        "To disconnect this chat, send /pair revoke."
    )


__all__ = ["extract_start_token", "format_pairing_status"]
