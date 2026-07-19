from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from openminion.base.types import Message
from openminion.cli.presentation.models import ChatMessage, MessageKind, ToolEvent
from openminion.cli.presentation.tool.blocks import tool_call_body

TARGET_KIND_FOCUS: str = "focus"
_TIMESTAMPED_SENDER_PREFIX_RE = re.compile(
    r"^\[(?P<timestamp>\d{2}:\d{2}:\d{2}Z)\]\s+(?P<sender>[^:]{1,64}):\s*(?P<content>.*)$"
)


class RuntimeMessageMixin:
    _agent_id: str | None
    _target: str
    _working_dir: str | None

    def _record_to_chat_messages(self, record: object) -> list[ChatMessage]:
        role = str(getattr(record, "role", "") or "").strip().lower()
        body = str(getattr(record, "body", "") or "")
        metadata = getattr(record, "metadata", None)
        if not isinstance(metadata, dict):
            metadata = {}
        created_at = str(getattr(record, "created_at", "") or "")
        msg_id = str(getattr(record, "id", "") or "")

        if self._target == TARGET_KIND_FOCUS:
            tool_results = self._tool_results(metadata)
            if tool_results:
                messages: list[ChatMessage] = []
                for index, item in enumerate(tool_results, start=1):
                    tool_event = self._tool_event_from_payload(item)
                    if tool_event is None:
                        continue
                    messages.append(
                        ChatMessage(
                            kind=MessageKind.TOOL,
                            sender=f"tool:{tool_event.tool_name or 'unknown'}",
                            body=tool_call_body(tool_event),
                            tool_result=tool_event.content,
                            tool_event=tool_event,
                            created_at=created_at,
                            msg_id=f"{msg_id}-tool-{index}",
                        )
                    )
                if body.strip() and role in {"assistant", "agent", "outbound"}:
                    messages.append(
                        ChatMessage(
                            kind=MessageKind.AGENT,
                            sender=self._role_to_sender(role, metadata),
                            body=self._strip_sender_prefix(body),
                            created_at=created_at,
                            msg_id=msg_id,
                        )
                    )
                if messages:
                    return messages

        kind = self._role_to_kind(role)
        sender = self._role_to_sender(role, metadata)
        tool_result = self._tool_result(metadata)
        tool_event = self._tool_event_from_metadata(metadata)
        if kind == MessageKind.AGENT:
            body = self._strip_sender_prefix(body)

        return [
            ChatMessage(
                kind=kind,
                sender=sender,
                body=body,
                tool_result=tool_result,
                tool_event=tool_event,
                created_at=created_at,
                msg_id=msg_id,
            )
        ]

    @staticmethod
    def _role_to_kind(role: str) -> MessageKind:
        if role in {"assistant", "agent", "outbound"}:
            return MessageKind.AGENT
        if role == "tool":
            return MessageKind.TOOL
        if role == "system":
            return MessageKind.SYSTEM
        if role == "error":
            return MessageKind.ERROR
        if role in {"user", "inbound"}:
            return MessageKind.USER
        return MessageKind.USER

    def _role_to_sender(self, role: str, metadata: dict[str, object]) -> str:
        if role in {"assistant", "agent", "outbound"}:
            return str(self._agent_id or "")
        if role == "tool":
            tool_name = str(
                metadata.get("tool_name")
                or metadata.get("tool")
                or metadata.get("name")
                or ""
            ).strip()
            if tool_name:
                return f"tool:{tool_name}"
            return "tool"
        if role == "system":
            return "system"
        if role == "error":
            return "error"
        return "you"

    @staticmethod
    def _tool_result(metadata: dict[str, object]) -> str | None:
        for key in ("tool_result", "result"):
            value = metadata.get(key)
            if value is not None:
                text = str(value).strip()
                if text:
                    return text
        return None

    def _tool_event_from_metadata(
        self, metadata: dict[str, object]
    ) -> ToolEvent | None:
        direct_name = str(
            metadata.get("tool_name")
            or metadata.get("tool")
            or metadata.get("name")
            or ""
        ).strip()
        if direct_name:
            return ToolEvent(
                tool_name=direct_name,
                args=self._tool_args_from_payload(metadata),
                content=self._tool_result(metadata) or "",
                content_type=self._infer_content_type(
                    self._tool_result(metadata) or ""
                ),
                call_id=str(
                    metadata.get("call_id") or metadata.get("id") or ""
                ).strip(),
                state=str(metadata.get("state", "") or "").strip(),
                model_tool_name=str(metadata.get("model_tool_name", "") or "").strip(),
                runtime_tool_name=str(
                    metadata.get("runtime_tool_name", "") or ""
                ).strip(),
                runtime_binding_id=str(
                    metadata.get("runtime_binding_id", "") or ""
                ).strip(),
                runtime_fallback_used=bool(
                    metadata.get("runtime_fallback_used", False)
                ),
                runtime_fallback_chain=self._fallback_chain(
                    metadata.get("runtime_fallback_chain")
                ),
                runtime_resolution_source=str(
                    metadata.get("runtime_resolution_source", "") or ""
                ).strip(),
                fallback_index=self._coerce_int(metadata.get("fallback_index")),
            )
        tool_results = self._tool_results(metadata)
        if len(tool_results) == 1:
            return self._tool_event_from_payload(tool_results[0])
        return None

    def _tool_event_from_payload(self, payload: Mapping[str, Any]) -> ToolEvent | None:
        tool_name = str(
            payload.get("tool_name") or payload.get("name") or payload.get("tool") or ""
        ).strip()
        if not tool_name:
            return None
        args = self._tool_args_from_payload(payload)
        content = str(
            payload.get("content") or payload.get("result") or payload.get("data") or ""
        )
        full_content = str(payload.get("full_content") or content or "")
        duration_ms = self._coerce_int(payload.get("duration_ms"))
        exit_code = self._coerce_int(
            payload.get("exit_code")
            if payload.get("exit_code") is not None
            else payload.get("returncode")
        )
        content_type = self._infer_content_type(content)
        return ToolEvent(
            tool_name=tool_name,
            args=args,
            content=content,
            content_type=content_type,
            duration_ms=duration_ms,
            exit_code=exit_code,
            truncated=bool(payload.get("truncated", False)),
            full_content=full_content,
            call_id=str(payload.get("call_id") or payload.get("id") or "").strip(),
            state=str(payload.get("state", "") or "").strip(),
            model_tool_name=str(payload.get("model_tool_name", "") or "").strip(),
            runtime_tool_name=str(payload.get("runtime_tool_name", "") or "").strip(),
            runtime_binding_id=str(payload.get("runtime_binding_id", "") or "").strip(),
            runtime_fallback_used=bool(payload.get("runtime_fallback_used", False)),
            runtime_fallback_chain=self._fallback_chain(
                payload.get("runtime_fallback_chain")
            ),
            runtime_resolution_source=str(
                payload.get("runtime_resolution_source", "") or ""
            ).strip(),
            fallback_index=self._coerce_int(payload.get("fallback_index")),
        )

    @staticmethod
    def _fallback_chain(value: object) -> list[str] | None:
        if not isinstance(value, (list, tuple)):
            return None
        return [
            str(item or "").strip() for item in value if str(item or "").strip()
        ] or None

    def _tool_args_from_payload(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        raw_args = payload.get("args")
        if isinstance(raw_args, Mapping):
            args = dict(raw_args)
        else:
            raw_args = payload.get("arguments")
            args = dict(raw_args) if isinstance(raw_args, Mapping) else {}
        path_value = str(args.get("path", "") or "").strip()
        if path_value:
            args["path"] = self._display_path(path_value)
        return args

    def _tool_results(self, metadata: dict[str, object]) -> list[dict[str, Any]]:
        raw_value = metadata.get("tool_results")
        decoded = self._decode_json(raw_value)
        if isinstance(decoded, dict):
            decoded = [decoded]
        if not isinstance(decoded, list):
            return []
        results: list[dict[str, Any]] = []
        for item in decoded:
            if isinstance(item, Mapping):
                results.append(dict(item))
        return results

    def _display_path(self, value: str) -> str:
        raw = str(value or "").strip()
        if not raw:
            return raw
        if not self._working_dir:
            return raw
        try:
            path = Path(raw)
            working_dir = Path(self._working_dir)
            resolved_path = path.resolve(strict=False)
            resolved_root = working_dir.resolve(strict=False)
            relative = resolved_path.relative_to(resolved_root)
            return str(relative)
        except (OSError, RuntimeError, ValueError):
            return raw

    @staticmethod
    def _decode_json(value: object) -> object:
        if not isinstance(value, str):
            return value
        raw = value.strip()
        if not raw or raw[0] not in {"[", "{"}:
            return value
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return value

    @staticmethod
    def _infer_content_type(content: str) -> str:
        text = str(content or "").lstrip()
        if text.startswith("diff --git") or text.startswith("@@"):
            return "diff"
        if text.startswith("{") or text.startswith("["):
            return "json"
        if "\n" in text:
            return "code"
        return "text"

    @staticmethod
    def _coerce_int(value: object) -> int | None:
        if value in (None, ""):
            return None
        try:
            if isinstance(value, (int, float, str, bytes, bytearray)):
                return int(value)
        except (TypeError, ValueError):
            return None
        return None

    @staticmethod
    def _strip_sender_prefix(body: str) -> str:
        stripped = body.strip()
        if not stripped:
            return body
        lines = stripped.splitlines()
        first_line = lines[0].strip()
        timestamped_match = _TIMESTAMPED_SENDER_PREFIX_RE.match(first_line)
        if timestamped_match is not None:
            first_content = str(timestamped_match.group("content") or "").strip()
            remaining_lines = [line.strip() for line in lines[1:] if line.strip()]
            if not remaining_lines:
                return first_content
            remaining_text = "\n".join(remaining_lines).strip()
            if remaining_text == first_content:
                return first_content
            return "\n".join([first_content, remaining_text]).strip()
        if ":" not in stripped:
            return body
        left, right = stripped.split(":", 1)
        candidate = left.strip()
        if candidate and " " not in candidate and len(candidate) <= 64:
            return right.strip()
        return body

    def _message_text(self, message: Message) -> str:
        body = str(getattr(message, "body", "") or "")
        if body:
            return self._strip_sender_prefix(body)
        metadata = getattr(message, "metadata", None)
        if isinstance(metadata, dict):
            text = str(metadata.get("text") or "").strip()
            if text:
                return text
        return ""
