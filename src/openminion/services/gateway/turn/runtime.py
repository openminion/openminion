"""Gateway turn runtime helpers for cache, memory context, and tool evidence."""

import asyncio
import hashlib
import json
import sys
from datetime import datetime, timezone
from typing import Any, Optional

from openminion.base.config.env import resolve_environment_config
from openminion.base.constants import (
    OPENMINION_COLOR_ENV,
    OPENMINION_TURN_TIMEOUT_SECONDS_ENV,
)
from openminion.base.types import Message
from openminion.base.user_io import UserIO

_USER_IO = UserIO()


def _resolve_turn_timeout_seconds(
    *,
    inbound_metadata: dict[str, str],
    default_timeout: int = 600,
) -> int:
    env_timeout = (
        resolve_environment_config()
        .get(
            OPENMINION_TURN_TIMEOUT_SECONDS_ENV,
            "",
        )
        .strip()
    )
    if env_timeout:
        try:
            parsed = int(env_timeout)
            if parsed >= 30:
                return min(parsed, 1800)
        except ValueError:
            pass

    metadata_timeout = inbound_metadata.get("turn_timeout_seconds", "").strip()
    if metadata_timeout:
        try:
            parsed = int(metadata_timeout)
            if parsed >= 30:
                return min(parsed, 1800)
        except ValueError:
            pass

    return default_timeout


def _message_to_cache(message: Message) -> dict[str, str]:
    return {
        "id": message.id,
        "channel": message.channel,
        "target": message.target,
        "body": message.body,
        "metadata": json.dumps(message.metadata, sort_keys=True),
    }


def _message_from_cache(payload: dict[str, object]) -> Message:
    metadata_raw = payload.get("metadata", "{}")
    metadata: dict[str, str] = {}
    if isinstance(metadata_raw, str):
        try:
            parsed = json.loads(metadata_raw)
            if isinstance(parsed, dict):
                metadata = {str(key): str(value) for key, value in parsed.items()}
        except json.JSONDecodeError:
            metadata = {}
    return Message(
        channel=str(payload.get("channel", "console")),
        target=str(payload.get("target", "local-user")),
        body=str(payload.get("body", "")),
        metadata=metadata,
        id=str(payload.get("id", "")),
    )


def _inject_memory_context(
    *,
    history: list[Message],
    channel: str,
    target: str,
    session_id: str,
    memory_context: str,
) -> list[Message]:
    context_text = str(memory_context or "").strip()
    if not context_text:
        return history

    if history:
        first = history[0]
        if str(first.metadata.get("role", "")).strip().lower() == "system":
            merged_metadata = dict(first.metadata)
            merged_metadata["memory_scope"] = "agent_canonical"
            merged_metadata["context_memory_merged"] = "true"
            history[0] = Message(
                channel=first.channel,
                target=first.target,
                body=(context_text + "\n\n" + str(first.body or "").strip()).strip(),
                metadata=merged_metadata,
                id=first.id,
                timestamp=first.timestamp,
            )
            return history

    history.insert(
        0,
        Message(
            channel=channel,
            target=target,
            body=context_text,
            metadata={
                "role": "system",
                "session_id": session_id,
                "memory_scope": "agent_canonical",
            },
        ),
    )
    return history


def _append_memory_retrieval_context(
    *,
    history: list[Message],
    channel: str,
    target: str,
    session_id: str,
    memory_context: str,
) -> list[Message]:
    context_text = str(memory_context or "").strip()
    if not context_text:
        return history
    history.append(
        Message(
            channel=channel,
            target=target,
            body=context_text,
            metadata={
                "role": "system",
                "session_id": session_id,
                "memory_scope": "agent_retrieval",
                "context_memory_dynamic": "true",
            },
        )
    )
    return history


def _append_knowledge_graph_context(
    *,
    history: list[Message],
    channel: str,
    target: str,
    session_id: str,
    graph_context: str,
) -> list[Message]:
    context_text = str(graph_context or "").strip()
    if not context_text:
        return history
    history.append(
        Message(
            channel=channel,
            target=target,
            body=context_text,
            metadata={
                "role": "system",
                "session_id": session_id,
                "graph_scope": "provider",
                "context_knowledge_graph": "true",
            },
        )
    )
    return history


def _request_hash(
    *,
    channel: str,
    target: str,
    body: str,
    session_id: Optional[str],
    inbound_metadata: Optional[dict[str, str]],
    typed_turn_intent: object | None = None,
) -> str:
    serialized_typed_turn_intent: object | None = None
    if typed_turn_intent is not None:
        if hasattr(typed_turn_intent, "model_dump"):
            try:
                serialized_typed_turn_intent = typed_turn_intent.model_dump(mode="json")
            except TypeError:
                serialized_typed_turn_intent = typed_turn_intent.model_dump()
        else:
            serialized_typed_turn_intent = typed_turn_intent
    payload = {
        "channel": channel,
        "target": target,
        "body": body,
        "session_id": session_id or "",
        "inbound_metadata": _normalize_metadata(inbound_metadata),
        "typed_turn_intent": serialized_typed_turn_intent,
    }
    encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _response_has_tool_activity(metadata: dict[str, str]) -> bool:
    raw_tool_calls = str(metadata.get("tool_calls_count", "")).strip()
    raw_tool_exec = str(metadata.get("tool_execution_count", "")).strip()

    tool_calls = int(raw_tool_calls) if raw_tool_calls.isdigit() else 0
    tool_exec = int(raw_tool_exec) if raw_tool_exec.isdigit() else 0
    return tool_calls > 0 or tool_exec > 0


def _correlation_payload(
    *,
    request_id: str,
    payload: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    merged = dict(payload or {})
    if request_id:
        merged["request_id"] = request_id
    return merged


def _normalize_metadata(metadata: Optional[dict[str, str]]) -> dict[str, str]:
    if not isinstance(metadata, dict):
        return {}
    normalized: dict[str, str] = {}
    for raw_key, raw_value in metadata.items():
        key = str(raw_key or "").strip()
        if not key:
            continue
        normalized[key] = str(raw_value or "").strip()
    return dict(sorted(normalized.items()))


def _extract_ephemeral_prompt_metadata(
    inbound_metadata: dict[str, str],
) -> dict[str, str]:
    """Return inbound metadata keys safe for prompt-time only hints."""
    if not isinstance(inbound_metadata, dict):
        return {}
    allowed_keys = {
        "cwd",
        "workspace_root",
        "project_context_body",
        "project_context_name",
        "project_context_path",
        "project_context_truncated",
        "permission_mode",
        "permission_overrides",
        # scheduled turns expose cron execution context via inbound metadata.
        "cron_job_id",
        "cron_run_id",
        "scheduled_for",
    }
    extracted: dict[str, str] = {}
    for key in allowed_keys:
        value = str(inbound_metadata.get(key, "") or "").strip()
        if value:
            extracted[key] = value
    return extracted


async def _await_with_progress_indicator(
    task: asyncio.Task[Message],
    *,
    label: str = "openminion",
    interval_seconds: float = 0.12,
) -> None:
    if interval_seconds <= 0:
        await asyncio.shield(task)
        return

    frames = ("|", "/", "-", "\\")
    loop = asyncio.get_running_loop()
    started = loop.time()
    rendered = False
    frame_index = 0

    while not task.done():
        elapsed_seconds = int(max(0, loop.time() - started))
        frame = frames[frame_index % len(frames)]
        frame_index += 1
        timestamp = _chat_timestamp()
        if _terminal_supports_color():
            styled_timestamp = _ansi(timestamp, "2")
            styled_label = _ansi(label, "1;36")
            styled_waiting = _ansi("thinking", "2;33")
            styled_frame = _ansi(frame, "1;33")
            styled_seconds = _ansi(f"{elapsed_seconds:>3}s", "2")
            styled_colon = _ansi(":", "2")
            line = (
                f"\r{styled_timestamp} {styled_label}{styled_colon} "
                f"{styled_waiting} {styled_frame} {styled_seconds}"
            )
        else:
            line = f"\r{timestamp} {label}: thinking {frame} {elapsed_seconds:>3}s"
        _USER_IO.out(line, end="", flush=True)
        rendered = True
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=interval_seconds)
        except asyncio.TimeoutError:
            continue
        except Exception:
            break

    if rendered:
        clear_width = max(
            48, len(f"{_chat_timestamp()} {label}: thinking {frames[0]} 999s")
        )
        _USER_IO.out("\r" + (" " * clear_width) + "\r", end="", flush=True)


def _interactive_user_prompt() -> str:
    timestamp = _chat_timestamp()
    if _terminal_supports_color():
        return f"{_ansi(timestamp, '2')} {_ansi('you', '1;34')}{_ansi(':', '2')} "
    return f"{timestamp} you: "


def _terminal_supports_color() -> bool:
    env = resolve_environment_config()
    no_color = env.get("NO_COLOR", "").strip()
    if no_color:
        return False
    forced = env.get(OPENMINION_COLOR_ENV, "").strip().lower()
    if forced in {"0", "false", "off", "no"}:
        return False
    if forced in {"1", "true", "on", "yes", "always"}:
        return True
    return bool(getattr(sys.stdout, "isatty", lambda: False)())


def _ansi(value: str, code: str) -> str:
    return f"\033[{code}m{value}\033[0m"


def _chat_timestamp() -> str:
    return "[" + datetime.now(timezone.utc).strftime("%H:%M:%SZ") + "]"
