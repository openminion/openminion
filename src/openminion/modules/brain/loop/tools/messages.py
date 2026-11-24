"""Convert brain action results into bounded model-facing tool messages."""

from __future__ import annotations

from collections.abc import Mapping
import json
import mimetypes
from typing import Any

from openminion.base.redaction import redact_sensitive_text
from openminion.modules.artifact.models import (
    ARTIFACT_REF_PREFIX,
    parse_ref_or_sha,
    sha_to_ref,
)
from openminion.modules.brain.constants import (
    TOOL_MESSAGE_DEPTH_LIMIT,
    TOOL_MESSAGE_SEQUENCE_LIMIT,
    TOOL_MESSAGE_STRING_LIMIT,
)
from openminion.modules.brain.schemas import ActionResult
from openminion.modules.context.input_boundaries import (
    emit_boundary_event as _pidf_emit_boundary_event,
)
from openminion.modules.llm.schemas import ImageContentPart, Message, TextContentPart

_EXEC_ARTIFACT_KEYS = (
    "stdout_artifact",
    "stderr_artifact",
    "evidence_artifact",
    "new_stdout_artifact",
    "new_stderr_artifact",
)
_REF_LIST_KEYS = ("artifact_refs", "evidence_refs")


def _child_path(parent: str, segment: str) -> str:
    safe_segment, _ = redact_sensitive_text(segment)
    safe_segment = safe_segment.replace("\r", " ").replace("\n", " ").strip()
    if len(safe_segment) > 64:
        safe_segment = f"{safe_segment[:64]}..."
    return f"{parent}.{safe_segment}" if parent else safe_segment


def _record_truncation(
    stats: dict[str, Any] | None,
    *,
    path: str,
    kind: str,
    kept: int,
    total: int,
) -> None:
    if stats is None:
        return
    stats["count"] += 1
    fields: list[dict[str, Any]] = stats["fields"]
    if len(fields) < TOOL_MESSAGE_SEQUENCE_LIMIT:
        fields.append({"path": path, "kind": kind, "kept": kept, "total": total})


def _truncate_tool_message_text(
    value: Any,
    *,
    limit: int = TOOL_MESSAGE_STRING_LIMIT,
    path: str = "",
    stats: dict[str, Any] | None = None,
) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    _record_truncation(stats, path=path, kind="string", kept=limit, total=len(text))
    return f"{text[:limit]}...[truncated]"


def _compact_tool_message_value(
    value: Any,
    *,
    depth: int = 0,
    path: str = "",
    stats: dict[str, Any] | None = None,
) -> Any:
    if depth >= TOOL_MESSAGE_DEPTH_LIMIT:
        if isinstance(value, (dict, list, tuple)):
            _record_truncation(stats, path=path, kind="depth", kept=0, total=len(value))
            return "[truncated]"
        return _truncate_tool_message_text(value, path=path, stats=stats)
    if isinstance(value, str):
        return _truncate_tool_message_text(value, path=path, stats=stats)
    if isinstance(value, dict):
        compacted: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= TOOL_MESSAGE_SEQUENCE_LIMIT:
                compacted["__truncated__"] = f"{len(value) - index} more key(s)"
                _record_truncation(
                    stats,
                    path=path,
                    kind="mapping",
                    kept=TOOL_MESSAGE_SEQUENCE_LIMIT,
                    total=len(value),
                )
                break
            key_text = str(key)
            compacted[key_text] = _compact_tool_message_value(
                item,
                depth=depth + 1,
                path=_child_path(path, key_text),
                stats=stats,
            )
        return compacted
    if isinstance(value, (list, tuple)):
        items = [
            _compact_tool_message_value(
                item,
                depth=depth + 1,
                path=f"{path}[{index}]" if path else f"[{index}]",
                stats=stats,
            )
            for index, item in enumerate(value[:TOOL_MESSAGE_SEQUENCE_LIMIT])
        ]
        if len(value) > TOOL_MESSAGE_SEQUENCE_LIMIT:
            items.append(
                f"...[{len(value) - TOOL_MESSAGE_SEQUENCE_LIMIT} more item(s)]"
            )
            _record_truncation(
                stats,
                path=path,
                kind="sequence",
                kept=TOOL_MESSAGE_SEQUENCE_LIMIT,
                total=len(value),
            )
        return items
    return value


def _canonical_artifact_ref(value: str) -> str:
    # Restrict reference hints to exact canonical CAS URIs; parse_ref_or_sha
    # alone also accepts bare 64-hex digests and uppercase hex.
    ref = value.strip()
    if not ref.startswith(ARTIFACT_REF_PREFIX):
        return ""
    try:
        canonical = sha_to_ref(parse_ref_or_sha(ref))
    except ValueError:
        return ""
    return canonical if ref == canonical else ""


def _supported_artifact_refs(action_result: ActionResult) -> list[str]:
    refs: list[str] = []
    seen: set[str] = set()

    def _add(raw_ref: str) -> None:
        if len(refs) >= TOOL_MESSAGE_SEQUENCE_LIMIT:
            return
        ref = _canonical_artifact_ref(raw_ref)
        if ref and ref not in seen:
            seen.add(ref)
            refs.append(ref)

    for item in action_result.artifact_refs:
        if not item.meta.get("opaque"):
            _add(item.ref)
    outputs = action_result.outputs
    if isinstance(outputs.get("artifact_ref"), str):
        _add(outputs["artifact_ref"])
    for key in _REF_LIST_KEYS:
        raw_list = outputs.get(key)
        if isinstance(raw_list, (list, tuple)):
            for entry in raw_list:
                if isinstance(entry, str):
                    _add(entry)
    for key in _EXEC_ARTIFACT_KEYS:
        raw_art = outputs.get(key)
        if (
            isinstance(raw_art, Mapping)
            and not (
                isinstance(raw_art.get("meta"), Mapping)
                and raw_art["meta"].get("opaque")
            )
            and isinstance(raw_art.get("ref"), str)
        ):
            _add(raw_art["ref"])
    web_artifacts = outputs.get("artifacts")
    if isinstance(web_artifacts, Mapping):
        for entry in web_artifacts.values():
            if isinstance(entry, str):
                _add(entry)
    return refs


def _build_truncation_payload(
    action_result: ActionResult,
    stats: dict[str, Any],
) -> dict[str, Any] | None:
    count = int(stats["count"])
    if count <= 0:
        return None
    fields: list[dict[str, Any]] = stats["fields"]
    truncation: dict[str, Any] = {
        "truncated": True,
        "field_count": count,
        "fields": fields,
    }
    if count > len(fields):
        truncation["omitted_fields"] = count - len(fields)
    artifact_refs = _supported_artifact_refs(action_result)
    if artifact_refs:
        truncation["artifact_refs"] = artifact_refs
    outputs = action_result.outputs
    if outputs.get("source") == "file_module" and isinstance(outputs.get("path"), str):
        file_path, _ = redact_sensitive_text(outputs["path"].strip())
        if file_path and len(file_path) <= TOOL_MESSAGE_STRING_LIMIT:
            truncation["path"] = file_path
    return truncation


def _browser_screenshot_parts(
    tool_name: str,
    outputs: Mapping[str, Any],
    body: str,
) -> list[TextContentPart | ImageContentPart]:
    artifact = outputs.get("artifact")
    if tool_name != "browser" or not isinstance(artifact, dict):
        return []
    path = str(artifact.get("path", "") or "").strip()
    data_base64 = str(artifact.get("content_base64", "") or "").strip()
    mime = str(artifact.get("mime", "") or "").strip().lower()
    mime = mime or str(mimetypes.guess_type(path)[0] or "").lower()
    if not mime and artifact.get("kind") == "screenshot":
        mime = "image/png"
    if (
        artifact.get("kind") != "screenshot"
        or not mime.startswith("image/")
        or not (path or data_base64)
    ):
        return []
    image = (
        ImageContentPart(source="path", path=path, mime_type=mime, refs=[path])
        if path
        else ImageContentPart(source="base64", data_base64=data_base64, mime_type=mime)
    )
    return [TextContentPart(text=body), image]


def action_result_to_tool_message(
    tool_call_id: str | None,
    tool_name: str,
    action_result: ActionResult,
) -> Message:
    stats: dict[str, Any] = {"count": 0, "fields": []}
    payload: dict[str, Any] = {
        "status": action_result.status,
        "summary": _truncate_tool_message_text(
            action_result.summary or "", path="summary", stats=stats
        ),
    }
    if action_result.outputs:
        payload["outputs"] = _compact_tool_message_value(
            action_result.outputs, path="outputs", stats=stats
        )
    if action_result.error:
        payload["error"] = {
            "code": action_result.error.code,
            "message": _truncate_tool_message_text(
                action_result.error.message, path="error.message", stats=stats
            ),
            "details": _compact_tool_message_value(
                action_result.error.details, path="error.details", stats=stats
            ),
        }
    truncation = _build_truncation_payload(action_result, stats)
    if truncation is not None:
        payload["truncation"] = truncation

    meta: dict[str, Any] = {}
    if tool_call_id:
        meta["tool_call_id"] = tool_call_id
    else:
        meta["tool_name"] = tool_name
    body = json.dumps(payload, ensure_ascii=False)
    _pidf_emit_boundary_event(
        "tool_output",
        body,
        seam_id="modules.brain.loop.tools.messages.action_result_to_tool_message",
        provenance_ref=tool_call_id,
    )
    status = {
        "success": "success",
        "blocked": "blocked",
        "needs_user": "blocked",
        "timeout": "timeout",
        "failed": "error",
        "retry": "error",
    }[action_result.status]
    content_parts = _browser_screenshot_parts(tool_name, action_result.outputs, body)
    if content_parts:
        meta["auto_vision_artifact"] = True
    return Message(
        role="tool",
        content=body,
        content_parts=content_parts,
        tool_call_id=tool_call_id,
        tool_status=status,
        tool_output=payload.get("outputs"),
        tool_error=payload.get("error"),
        meta=meta,
    )


def format_blocking_tool_message(
    *,
    tool_name: str,
    reason: str,
    termination_reason: str,
) -> Message:
    return Message(
        role="system",
        content=json.dumps(
            {
                "status": "blocked",
                "summary": reason,
                "termination_reason": termination_reason,
            },
            ensure_ascii=False,
        ),
        meta={"tool_name": tool_name},
    )
