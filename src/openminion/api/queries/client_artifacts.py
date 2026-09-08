from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Iterable

from openminion.api.runtime import APIRuntime
from openminion.base.redaction import redact_sensitive_text
from openminion.services.brain.session_artifacts import (
    SessionArtifactFacade,
    SessionArtifactUnavailable,
)


@dataclass
class ArtifactQueryError(RuntimeError):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True)
class ToolOutputProjection:
    tool_name: str
    tool_status: str
    summary: str
    created_at: str
    content_kind: str
    content_text: str | None


_PATH_TOKEN = re.compile(
    r"(?:(?<=^)|(?<=[\s\"'=\(\[\{]))(?:/|[A-Za-z]:[\\/]|\\\\)[^\s\"',\)\]\}]+"
)


def resolve_session_artifact_facade(
    runtime: APIRuntime,
    session_id: str,
) -> SessionArtifactFacade:
    record = runtime.sessions.get_session(session_id)
    if record is None:
        raise ArtifactQueryError("session_not_found", "Session is unavailable.")
    if getattr(record, "status", None) != "active":
        raise ArtifactQueryError("session_closed", "Session is not active.")
    try:
        facade = runtime.session_artifact_facade(session_id)
    except SessionArtifactUnavailable as exc:
        raise ArtifactQueryError(
            "artifact_unsupported",
            "Artifact operations are unavailable for this session.",
        ) from exc
    if not isinstance(facade, SessionArtifactFacade):
        raise ArtifactQueryError(
            "artifact_unsupported",
            "Artifact operations are unavailable for this session.",
        )
    return facade


def artifact_event_refs(event: dict[str, Any]) -> list[str]:
    if event.get("event_type") != "turn.user":
        return []
    refs = event.get("refs")
    values = refs.get("artifact_refs") if isinstance(refs, dict) else None
    if not isinstance(values, list):
        return []
    return [str(value) for value in values]


def project_tool_output(
    event: dict[str, Any], *, session_id: str
) -> ToolOutputProjection | None:
    event_type = event.get("event_type")
    payload = event.get("payload")
    if (
        event_type not in {"tool.call.completed", "tool.call.blocked"}
        or not isinstance(payload, dict)
        or payload.get("schema_version") != 1
    ):
        return None
    parent = event.get("tool_parent")
    parent_payload = parent.get("payload") if isinstance(parent, dict) else None
    call_id = payload.get("call_id")
    turn_scope_id = payload.get("turn_scope_id")
    status = payload.get("status")
    status_valid = (
        status == "success"
        if event_type.endswith("completed")
        else status
        in {
            "error",
            "blocked",
            "timeout",
        }
    )
    valid = (
        isinstance(event.get("event_id"), str)
        and bool(event["event_id"].strip())
        and event.get("session_id") == session_id
        and isinstance(event.get("timestamp"), str)
        and bool(event["timestamp"].strip())
        and isinstance(call_id, str)
        and bool(call_id.strip())
        and isinstance(turn_scope_id, str)
        and bool(turn_scope_id.strip())
        and status_valid
        and isinstance(parent, dict)
        and set(parent) == {"event_id", "session_id", "event_type", "payload"}
        and parent.get("event_id") == event.get("parent_event_id")
        and parent.get("session_id") == session_id
        and parent.get("event_type") == "tool.call.requested"
        and isinstance(parent_payload, dict)
        and parent_payload.get("schema_version") == 1
        and parent_payload.get("call_id") == call_id
        and parent_payload.get("turn_scope_id") == turn_scope_id
        and isinstance(parent_payload.get("canonical_name"), str)
        and bool(parent_payload["canonical_name"].strip())
    )
    if not valid:
        raise ArtifactQueryError("event_invalid", "Tool event is invalid.")
    assert isinstance(parent_payload, dict)
    assert isinstance(status, str)
    tool_name = project_text(str(parent_payload["canonical_name"]))
    tool_status = project_text(status)
    created_at = project_text(str(event["timestamp"]))
    if event_type == "tool.call.completed":
        output = payload.get("output")
        if not isinstance(output, dict):
            return _unavailable(tool_name, tool_status, created_at)
        summary = output.get("summary")
        outputs = output.get("outputs")
        if not isinstance(summary, str) or not isinstance(outputs, dict):
            return _unavailable(tool_name, tool_status, created_at)
        projected_summary = project_text(summary)
        projection = _project_completed_outputs(outputs)
    else:
        error = payload.get("error")
        message = error.get("message") if isinstance(error, dict) else None
        if not isinstance(message, str):
            return _unavailable(tool_name, tool_status, created_at)
        assert isinstance(error, dict)
        projected_summary = project_text(message)
        projection = _project_blocked_error(error)
    if projection is None or _contains_disallowed_controls(projected_summary):
        return _unavailable(tool_name, tool_status, created_at)
    content_kind, content_text = projection
    if len(content_text.encode("utf-8")) > 256 * 1024:
        raise ArtifactQueryError("content_too_large", "Tool output is too large.")
    return ToolOutputProjection(
        tool_name,
        tool_status,
        projected_summary,
        created_at,
        content_kind,
        content_text,
    )


def project_content(value: str, *, mime: str) -> str:
    if mime == "application/json":
        try:
            projected = _project_json_value(json.loads(value))
            return json.dumps(projected, ensure_ascii=False, separators=(",", ":"))
        except (json.JSONDecodeError, RecursionError, TypeError, ValueError) as exc:
            raise ArtifactQueryError(
                "artifact_unsupported", "Artifact content is unsupported."
            ) from exc
    if _contains_disallowed_controls(value, allow_carriage_return=True):
        raise ArtifactQueryError(
            "artifact_unsupported", "Artifact content is unsupported."
        )
    return project_text(value)


def project_text(value: str) -> str:
    return redact_paths(redact_sensitive_text(value)[0])


def redact_paths(value: str) -> str:
    lines = value.splitlines(keepends=True)
    is_diff = (
        any(line.startswith("--- ") for line in lines)
        and any(line.startswith("+++ ") for line in lines)
        and any(line.startswith("@@ ") for line in lines)
    )
    if not is_diff:
        return "[PATH REDACTED]" if _PATH_TOKEN.search(value) else value
    projected: list[str] = []
    for line in lines:
        ending = "\n" if line.endswith("\n") else ""
        content = line[:-1] if ending else line
        marker = next(
            (
                candidate
                for candidate in ("--- ", "+++ ", "@@ ", "+", "-", " ")
                if content.startswith(candidate)
            ),
            "",
        )
        projected.append(
            f"{marker}[PATH REDACTED]{ending}"
            if marker and _PATH_TOKEN.search(content[len(marker) :])
            else line
        )
    return "".join(projected)


def utf8_page_end(text: str, start: int, limit_bytes: int) -> int:
    used = 0
    end = start
    while end < len(text):
        width = len(text[end].encode("utf-8"))
        if used + width > limit_bytes:
            break
        used += width
        end += 1
    if end == start and start < len(text):
        raise ArtifactQueryError("content_too_large", "Content page is too large.")
    return end


def scan_state_size(
    entries: Iterable[tuple[str, str, int, int, ToolOutputProjection | None]],
) -> int:
    serialized = [
        {
            "source_kind": source_kind,
            "source_id": source_id,
            "first_seq": first_seq,
            "occurrence_count": occurrence_count,
            "tool_output": vars(tool_output) if tool_output is not None else None,
        }
        for source_kind, source_id, first_seq, occurrence_count, tool_output in entries
    ]
    return len(
        json.dumps(serialized, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
    )


def _project_completed_outputs(outputs: dict[str, Any]) -> tuple[str, str] | None:
    if len(outputs) == 1:
        key, value = next(iter(outputs.items()))
        if key == "diff" and isinstance(value, str):
            projected = project_text(value)
            if _contains_disallowed_controls(projected):
                return None
            return ("diff" if _is_unified_diff(projected) else "text", projected)
        if key in {"text", "output"} and isinstance(value, str):
            projected = project_text(value)
            if _contains_disallowed_controls(projected):
                return None
            return "text", projected
    try:
        projected = _project_json_value(outputs)
        return "json", json.dumps(
            projected, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    except (RecursionError, TypeError, ValueError):
        return None


def _project_blocked_error(error: dict[str, Any]) -> tuple[str, str] | None:
    try:
        projected: dict[str, Any] = {"message": project_text(str(error["message"]))}
        if isinstance(error.get("code"), str):
            projected["code"] = project_text(error["code"])
        if isinstance(error.get("details"), dict):
            projected["details"] = _project_json_value(error["details"])
        return "json", json.dumps(
            projected, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    except (RecursionError, TypeError, ValueError):
        return None


def _project_json_value(value: Any) -> Any:
    if isinstance(value, str):
        return project_text(value)
    if isinstance(value, list):
        return [_project_json_value(item) for item in value]
    if isinstance(value, dict):
        projected: dict[str, Any] = {}
        for key, item in value.items():
            safe_key = project_text(key)
            if safe_key in projected:
                raise ValueError("projected JSON keys collide")
            projected[safe_key] = _project_json_value(item)
        return projected
    return value


def _unavailable(
    tool_name: str,
    tool_status: str,
    created_at: str,
) -> ToolOutputProjection:
    return ToolOutputProjection(
        tool_name,
        tool_status,
        "Output unavailable",
        created_at,
        "unavailable",
        None,
    )


def _is_unified_diff(value: str) -> bool:
    if len(value.encode("utf-8")) > 256 * 1024 or _contains_disallowed_controls(value):
        return False
    lines = value.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("--- ") and index + 1 < len(lines):
            if lines[index + 1].startswith("+++ ") and any(
                candidate.startswith("@@ ") for candidate in lines[index + 2 :]
            ):
                return True
    return False


def _contains_disallowed_controls(
    value: str,
    *,
    allow_carriage_return: bool = False,
) -> bool:
    allowed = {"\t", "\n", "\r"} if allow_carriage_return else {"\t", "\n"}
    return any(
        ord(character) < 32 and character not in allowed or ord(character) == 127
        for character in value
    )
