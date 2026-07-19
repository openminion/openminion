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
    token = parts[1].strip().split()[0]
    return token or None


__all__ = ["extract_start_token"]
