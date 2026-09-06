import asyncio
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Coroutine, Mapping, cast
from uuid import uuid4

from openminion.base.types import Message
from openminion.cli.presentation.models import ChatMessage, MessageKind, ToolEvent
from openminion.cli.presentation.tool.formatting import tool_call_body
from openminion.modules.context.trace_inspection import (
    ContextTraceLookupError,
    list_context_traces,
)
from openminion.modules.storage import (
    is_room_session_key,
    normalize_identity,
    normalize_participant_role,
    normalize_participant_type,
)
from openminion.modules.telemetry.trace import phase_timing
from openminion.services.gateway.constants import (
    CALLER_HANDLES_DELIVERY_METADATA_KEY,
)
from openminion.services.gateway.routing import parse_metadata_bool

TARGET_KIND_FOCUS: str = "focus"
_TIMESTAMPED_SENDER_PREFIX_RE = re.compile(
    r"^\[(?P<timestamp>\d{2}:\d{2}:\d{2}Z)\]\s+(?P<sender>[^:]{1,64}):\s*(?P<content>.*)$"
)


class RuntimeMessageMixin:
    _agent_id: str | None
    _channel: str
    _rt: Any
    _target: str
    _working_dir: str | None

    if TYPE_CHECKING:

        @property
        def agent_id(self) -> str: ...

        @property
        def session_id(self) -> str: ...

        def _begin_turn_usage_tracking(self) -> None: ...

        def _finalize_turn_usage(
            self, metadata: Mapping[str, Any] | None, *, succeeded: bool
        ) -> None: ...

        def _record_chat_phase_timing(
            self, timer: phase_timing.ChatPhaseTimer, *, turn_id: str
        ) -> None: ...

        def _turn_inbound_metadata(
            self, inbound_metadata: dict[str, str] | None
        ) -> dict[str, str] | None: ...

        def _wrap_progress_callback(
            self, callback: Callable[[dict[str, Any]], None] | None
        ) -> Callable[[dict[str, Any]], None]: ...

    def is_room_session(self) -> bool:
        session = self._rt.sessions.get_session(self.session_id)
        return session is not None and is_room_session_key(
            str(getattr(session, "session_key", "") or "")
        )

    def context_trace_payload(self, *, session_id: str) -> dict[str, Any]:
        try:
            return cast(
                dict[str, Any],
                list_context_traces(self._rt.sessions, session_id=session_id),
            )
        except ContextTraceLookupError as exc:
            return {
                "session_id": session_id,
                "traces": [],
                "count": 0,
                "degraded": exc.code,
            }

    def _room_session_and_actor(self) -> tuple[Any, Any]:
        session = self._rt.sessions.get_session(self.session_id)
        if session is None or not is_room_session_key(
            str(getattr(session, "session_key", "") or "")
        ):
            raise RuntimeError("interactive runtime is not bound to a room session")
        local_human_id = str(
            (getattr(session, "metadata", {}) or {}).get("local_human_id", "") or ""
        ).strip()
        if not local_human_id:
            raise RuntimeError("room session has no local human identity")
        actor = self._rt.sessions.get_participant(
            session.id,
            "human",
            local_human_id,
        )
        if actor is None:
            raise RuntimeError("local human is not an active room participant")
        if actor.role not in {"owner", "participant", "observer"}:
            raise RuntimeError(f"unsupported room participant role: {actor.role}")
        return session, actor

    def _room_owner(self) -> tuple[Any, Any]:
        session, actor = self._room_session_and_actor()
        if actor.role != "owner":
            raise RuntimeError("room mutations require the local human owner")
        return session, actor

    def room_participants_report(self) -> str:
        session, actor = self._room_session_and_actor()
        participants = list(self._rt.sessions.list_participants(session.id))
        metadata = dict(getattr(session, "metadata", {}) or {})
        routing = str(metadata.get("room_routing_mode", "") or "addressed")
        room_name = str(metadata.get("name") or session.id)
        lines = [
            f"Room: {room_name}",
            f"  key: {session.session_key}",
            f"  routing: {routing}",
            f"  local human: {actor.participant_id} ({actor.role})",
            f"  active agent: {session.active_agent_id or '(none)'}",
            f"  participants: {len(participants)}",
        ]
        for participant in participants:
            active = (
                " *active"
                if participant.participant_type == "agent"
                and participant.participant_id == session.active_agent_id
                else ""
            )
            lines.append(
                f"    {participant.participant_type} "
                f"{participant.participant_id} [{participant.role}]{active}"
            )
        return "\n".join(lines)

    def room_invite_agent(self, agent_id: str) -> Any:
        session, _actor = self._room_owner()
        normalized_agent = normalize_identity(agent_id)
        if normalized_agent not in self._rt.config.agents:
            raise ValueError(f"Unknown configured agent: {normalized_agent}")
        return self._rt.sessions.add_participant(
            session_id=session.id,
            participant_type="agent",
            participant_id=normalized_agent,
            channel=session.channel,
            role="participant",
            display_name=normalized_agent,
        )

    def room_invite_human(self, human_id: str, *, role: str = "participant") -> Any:
        session, _actor = self._room_owner()
        normalized_human = normalize_identity(human_id)
        normalized_role = normalize_participant_role(role)
        return self._rt.sessions.add_participant(
            session_id=session.id,
            participant_type="human",
            participant_id=normalized_human,
            channel=session.channel,
            role=normalized_role,
            display_name=normalized_human,
        )

    def room_kick(self, participant_type: str, participant_id: str) -> bool:
        session, actor = self._room_owner()
        normalized_type = normalize_participant_type(participant_type)
        normalized_id = normalize_identity(participant_id)
        if normalized_type == "human" and normalized_id == actor.participant_id:
            raise ValueError("the acting room owner cannot remove themself")
        return bool(
            self._rt.sessions.remove_participant(
                session_id=session.id,
                participant_type=normalized_type,
                participant_id=normalized_id,
            )
        )

    def room_activate(self, agent_id: str) -> Any:
        session, _actor = self._room_owner()
        normalized_agent = normalize_identity(agent_id)
        if normalized_agent not in self._rt.config.agents:
            raise ValueError(f"Unknown configured agent: {normalized_agent}")
        return self._rt.sessions.set_active_agent(
            session_id=session.id,
            agent_id=normalized_agent,
        )

    def room_set_routing(self, mode: str) -> Any:
        session, _actor = self._room_owner()
        normalized_mode = str(mode or "").strip().lower()
        if normalized_mode not in {"addressed", "broadcast", "sequential"}:
            raise ValueError("routing mode must be addressed, broadcast, or sequential")
        return self._rt.sessions.update_session_metadata(
            session_id=session.id,
            patch={"room_routing_mode": normalized_mode},
        )

    async def run_room_turn(
        self,
        text: str,
        *,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        inbound_metadata: dict[str, str] | None = None,
        approval_callback: Callable[[str, dict[str, Any], Any], Awaitable[bool]]
        | None = None,
        cancel_event: Any,
    ) -> dict[str, object]:
        session, actor = self._room_session_and_actor()
        if actor.role not in {"owner", "participant"}:
            raise RuntimeError("local room observer cannot post messages")

        loop = asyncio.get_running_loop()
        wrapped_progress = self._wrap_progress_callback(progress_callback)

        def progress_from_worker(payload: object) -> None:
            if isinstance(payload, Mapping):
                mapped = dict(payload)
            else:
                model_dump = getattr(payload, "model_dump", None)
                mapped = dict(model_dump(mode="json")) if callable(model_dump) else {}
            if mapped:
                loop.call_soon_threadsafe(wrapped_progress, mapped)

        approval_from_worker = None
        if approval_callback is not None:

            def approval_from_worker(
                tool_name: str, args: dict[str, Any], call_id: Any
            ) -> bool:
                return bool(
                    asyncio.run_coroutine_threadsafe(
                        cast(
                            Coroutine[Any, Any, bool],
                            approval_callback(tool_name, args, call_id),
                        ),
                        loop,
                    ).result()
                )

        merged_metadata = self._turn_inbound_metadata(inbound_metadata) or {}
        merged_metadata[CALLER_HANDLES_DELIVERY_METADATA_KEY] = "true"
        merged_metadata["participant_id"] = actor.participant_id
        payload: dict[str, object] = {
            "message": text,
            "agent_id": self.agent_id,
            "session_id": session.id,
            "channel": self._channel,
            "target": self._target,
            "inbound_metadata": merged_metadata,
            "deliver": False,
        }
        timer = phase_timing.ChatPhaseTimer(cold_start=False)
        turn_id = uuid4().hex
        result: dict[str, object] | None = None
        succeeded = False
        self._begin_turn_usage_tracking()
        try:
            with phase_timing.use_chat_phase_timer(timer):
                result = cast(
                    dict[str, object],
                    await asyncio.to_thread(
                        self._rt.run_turn,
                        payload=payload,
                        progress_callback=progress_from_worker,
                        approval_callback=approval_from_worker,
                        cancel_event=cancel_event,
                    ),
                )
            succeeded = True
            if str(result.get("body", "") or "").strip():
                phase_timing.mark_active_chat_first_text()
            return result
        finally:
            metadata = result.get("metadata") if result is not None else None
            self._finalize_turn_usage(
                metadata if isinstance(metadata, Mapping) else None,
                succeeded=succeeded,
            )
            self._record_chat_phase_timing(timer, turn_id=turn_id)

    @staticmethod
    def _apply_focus_turn_metadata(metadata: dict[str, str]) -> None:
        if not str(
            metadata.get(CALLER_HANDLES_DELIVERY_METADATA_KEY, "") or ""
        ).strip():
            metadata[CALLER_HANDLES_DELIVERY_METADATA_KEY] = "true"
        if (
            "resume" not in metadata
            and not str(metadata.get("thread_id", "") or "").strip()
            and not parse_metadata_bool(metadata, "reset")
            and not parse_metadata_bool(metadata, "reset_session")
        ):
            metadata["resume"] = "true"

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
                            show_header=self.is_room_session(),
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
                show_header=kind == MessageKind.AGENT and self.is_room_session(),
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
            return str(
                metadata.get("display_name")
                or metadata.get("participant_id")
                or self._agent_id
                or ""
            )
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
            content = self._tool_result(metadata) or ""
            return ToolEvent(
                tool_name=direct_name,
                args=self._tool_args_from_payload(metadata),
                content=content,
                content_type=self._infer_content_type(content),
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
        return [dict(item) for item in decoded if isinstance(item, Mapping)]

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


def room_result_chat_messages(payload: Mapping[str, Any]) -> list[ChatMessage]:
    metadata = payload.get("metadata")
    response_items = (
        metadata.get("room_responses") if isinstance(metadata, Mapping) else None
    )
    if isinstance(response_items, list):
        return [
            ChatMessage(
                kind=MessageKind.AGENT,
                sender=str(item.get("agent_id", "") or ""),
                body=str(item.get("body", "") or ""),
                show_header=True,
                msg_id=str(item.get("persisted_outbound_message_id", "") or ""),
            )
            for item in response_items
            if isinstance(item, Mapping)
        ]
    persisted_id = (
        str(metadata.get("persisted_outbound_message_id", "") or "")
        if isinstance(metadata, Mapping)
        else ""
    )
    return [
        ChatMessage(
            kind=MessageKind.AGENT,
            sender=str(payload.get("agent_id", "") or ""),
            body=str(payload.get("body", "") or ""),
            show_header=True,
            msg_id=persisted_id,
        )
    ]
