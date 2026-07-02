"""Slack command alias normalization."""

from __future__ import annotations

import re


_BOT_MENTION_RE = re.compile(r"^\s*<@[A-Z0-9]+>\s*")


def strip_bot_mention(text: str, *, bot_user_id: str | None = None) -> str:
    value = str(text or "")
    if bot_user_id:
        value = re.sub(rf"^\s*<@{re.escape(bot_user_id)}>\s*", "", value)
    return _BOT_MENTION_RE.sub("", value).strip()


def normalize_command_text(text: str) -> str:
    value = str(text or "").strip()
    lower = value.lower()
    aliases = {
        "help": "/help",
        "status": "/status",
        "new": "/session new",
        "session new": "/session new",
        "sessions": "/sessions",
        "profile": "/profile",
        "agent": "/profile",
    }
    if lower in aliases:
        return aliases[lower]
    if lower.startswith("profile use "):
        return "/profile use " + value.split(None, 2)[2]
    if lower.startswith("agent use "):
        return "/profile use " + value.split(None, 2)[2]
    return value


def normalize_slash_command_text(command: str, text: str) -> str:
    command_name = str(command or "").strip()
    body = str(text or "").strip()
    if command_name == "/openminion":
        return normalize_command_text(body or "help")
    return normalize_command_text((command_name + " " + body).strip())
