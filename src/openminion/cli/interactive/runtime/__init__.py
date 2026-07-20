from __future__ import annotations

import inspect
import json
import time
from pathlib import Path
from sqlite3 import Error as SQLiteError
from typing import Any, AsyncIterator, Awaitable, Callable, Mapping, cast
from uuid import uuid4

from openminion.api.runtime import APIRuntime
from openminion.base.config.core import resolve_default_agent_id
from openminion.base.config.action_policy import ACTION_POLICY_SESSION_OVERRIDE_KEY
from openminion.base.config.runtime.profile import PERMISSION_MODE_DEFAULT
from openminion.base.types import Message
from openminion.cli.status import (
    TokenUsageSnapshot,
    TokenUsageTotals,
    accumulate_usage,
    build_token_usage_snapshot,
    usage_totals_from_mapping,
)
from openminion.cli.parser.contracts import CLI_INTERFACE_VERSION
from openminion.cli.presentation.models import ChatMessage
from openminion.cli.interactive.models import SidebarItem
from openminion.cli.interactive.project_context import (
    ProjectContextInfo,
    build_project_context_metadata,
)
from openminion.modules.telemetry.trace import phase_timing
from openminion.services.gateway.constants import (
    CALLER_HANDLES_DELIVERY_METADATA_KEY,
)
from openminion.base.config.settings import SettingsResolver
from openminion.modules.brain.tools.lifecycle import register_settings_lifecycle_hooks
from .controls import RuntimeControlsMixin
from .mcp import RuntimeMCPMixin
from .messages import (
    TARGET_KIND_FOCUS as _TARGET_KIND_FOCUS,
    RuntimeMessageMixin,
)

ApprovalCallback = Callable[[str, dict[str, Any], Any], Awaitable[bool]]
_LIVE_USAGE_THROTTLE_SECONDS = 0.5
_TURN_FAILURE_TEXT_MAP: tuple[tuple[str, str], ...] = (
    (
        "finalization_status contract",
        "The model ended the turn without the required completion contract. "
        "Please try again.",
    ),
    (
        "required completion contract",
        "The model ended the turn without the required completion contract. "
        "Please try again.",
    ),
)


def _retryable_turn_failure_message(text: str) -> str | None:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return None
    for marker, rendered in _TURN_FAILURE_TEXT_MAP:
        if marker in lowered:
            return rendered
    return None


def _is_retryable_turn_failure_text(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    return any(marker in lowered for marker, _ in _TURN_FAILURE_TEXT_MAP)


def _session_sort_key(session: Any) -> str:
    """Sort sessions by most-recent-activity first for candidate selection."""
    return (
        str(getattr(session, "last_activity_at", "") or "")
        or str(getattr(session, "updated_at", "") or "")
        or str(getattr(session, "created_at", "") or "")
    )


class OpenMinionRuntime(
    RuntimeControlsMixin,
    RuntimeMCPMixin,
    RuntimeMessageMixin,
):
    """ChatRuntimeAPI adapter over APIRuntime."""

    contract_version: str = CLI_INTERFACE_VERSION

    def __init__(
        self,
        rt: APIRuntime,
        *,
        channel: str | None = None,
        target: str = "tui",
        history_limit: int = 200,
        agent_id: str | None = None,
        working_dir: str | None = None,
        bind_immediately: bool = True,
        session_id: str | None = None,
        prompt_on_resume: bool = False,
    ) -> None:
        self._rt = rt
        self._agent_id_override = str(agent_id or "").strip() or None
        if channel:
            self._channel = channel
        else:
            selected_profile = self._select_channel_profile()
            self._channel = (
                str(getattr(selected_profile, "default_channel", "") or "").strip()
                or "cli"
            )
        self._target = target
        self._history_limit = max(1, int(history_limit))
        self._working_dir = self._normalize_working_dir(working_dir)
        self._agent_id: str | None = None
        self._gateway = None
        self._session_id: str | None = None
        self._conversation_id: str = ""
        self._prompt_on_resume = bool(prompt_on_resume)
        self._completed_session_usage = TokenUsageTotals()
        self._last_turn_usage = TokenUsageTotals()
        self._current_turn_usage: TokenUsageTotals | None = None
        self._current_turn_has_live_deltas = False
        self._current_turn_started_at_monotonic: float | None = None
        self._last_turn_elapsed_seconds: float | None = None
        self._usage_updated_at_monotonic: float | None = None
        self._last_live_usage_update_at: float | None = None
        self._project_context: ProjectContextInfo | None = None
        self._project_context_pending: bool = False
        self._model_override_provider: str = ""
        self._model_override_model: str = ""
        self._action_policy_mode_override: str = ""
        self._permission_mode: str = ""
        self._permission_overrides: dict[str, str] = {}
        self._read_only_mode: bool = False
        self._effort_level: str = ""
        self._statusline_command: str = ""
        register_settings_lifecycle_hooks(
            SettingsResolver(workspace_root=self._working_dir)
        )
        self._pending_candidate_session: Any | None = None

        normalized_session_id = str(session_id or "").strip() or None

        if bind_immediately and not self._prompt_on_resume:
            self._ensure_agent_resolved()
            session = rt.sessions.resolve_session(
                agent_id=self.agent_id,
                channel=self._channel,
                target=self._target,
                session_id=normalized_session_id,
            )
            self._session_id = session.id
            self._sync_conversation_id()
        elif self._prompt_on_resume:
            self._ensure_agent_resolved()
            if normalized_session_id:
                self._resolve_startup_session(normalized_session_id)
            else:
                self._refresh_pending_candidate()
        elif normalized_session_id:
            self._resolve_startup_session(normalized_session_id)

    def _select_channel_profile(self) -> object | None:
        config = getattr(self._rt, "config", None)
        agents = getattr(config, "agents", None)
        if isinstance(agents, Mapping):
            selected_agent_id = self._selected_agent_id_for_config(config)
            if selected_agent_id:
                selected_profile = agents.get(selected_agent_id)
                if selected_profile is not None:
                    return selected_profile
        return None

    def _selected_agent_id_for_config(self, config: object) -> str:
        selected_agent_id = self._agent_id_override or ""
        if selected_agent_id:
            return selected_agent_id
        try:
            return resolve_default_agent_id(config)
        except (AttributeError, TypeError, ValueError):
            agents = getattr(config, "agents", None)
            if isinstance(agents, Mapping) and len(agents) == 1:
                return str(next(iter(agents)))
        legacy_agent = getattr(config, "agent", None)
        if legacy_agent is not None:
            legacy_name = str(getattr(legacy_agent, "name", "") or "").strip()
            if legacy_name:
                return legacy_name
        return ""

    @property
    def agent_id(self) -> str:
        self._ensure_agent_resolved()
        return str(self._agent_id or "")

    @property
    def session_id(self) -> str:
        return str(self._session_id or "")

    @property
    def transport(self) -> str:
        return "gateway"

    @property
    def api_runtime(self) -> APIRuntime:
        return self._rt

    @property
    def is_bound(self) -> bool:
        return bool(str(self._session_id or "").strip())

    @property
    def working_dir(self) -> str:
        return str(self._working_dir or "")

    @property
    def project_context(self) -> ProjectContextInfo | None:
        return self._project_context

    def execute_goal_command(self, line: str) -> tuple[str, str]:
        if not self.is_bound:
            return ("error", "No active session for /goal.")
        from openminion.cli.commands.goal import execute_goal_cli_command

        return cast(
            tuple[str, str],
            execute_goal_cli_command(
                line,
                session_id=self.session_id,
                db_path=self._goal_database_path(),
            ),
        )

    def goal_statusline_label(self) -> str:
        if not self.is_bound:
            return ""
        from openminion.cli.commands.goal import goal_statusline_label

        return cast(
            str,
            goal_statusline_label(
                session_id=self.session_id,
                db_path=self._goal_database_path(),
            ),
        )

    def _goal_database_path(self) -> Path:
        from openminion.modules.brain.paths import resolve_brain_sessions_db_path

        return cast(
            Path,
            resolve_brain_sessions_db_path(storage_path=self._rt.storage_path),
        )

    def token_usage_snapshot(self) -> TokenUsageSnapshot:
        turn_usage = self._current_turn_usage or self._last_turn_usage
        session_usage = self._completed_session_usage
        if self._current_turn_usage is not None:
            session_usage = (
                accumulate_usage(session_usage, self._current_turn_usage)
                or TokenUsageTotals()
            )
        turn_elapsed_seconds = self._last_turn_elapsed_seconds
        if self._current_turn_started_at_monotonic is not None:
            turn_elapsed_seconds = max(
                0.0,
                time.monotonic() - self._current_turn_started_at_monotonic,
            )
        context_limit = self._context_limit_tokens()
        context_used = getattr(session_usage, "total_tokens", None)
        if context_limit is None:
            context_used = None
        return build_token_usage_snapshot(
            turn=turn_usage,
            session=session_usage,
            context_used_tokens=context_used,
            context_limit_tokens=context_limit,
            has_live_deltas=self._current_turn_has_live_deltas,
            turn_elapsed_seconds=turn_elapsed_seconds,
            updated_at_monotonic=self._usage_updated_at_monotonic,
        )

    def get_current_history(self) -> list[ChatMessage]:
        if not self.is_bound:
            return []
        records = self._rt.sessions.list_messages(
            session_id=self.session_id,
            limit=self._history_limit,
        )
        messages: list[ChatMessage] = []
        for record in records:
            messages.extend(self._record_to_chat_messages(record))
        return messages

    def list_sessions(self, *, scope: str = "all") -> list[SidebarItem]:
        sessions = self._rt.sessions.list_sessions(limit=self._history_limit)
        if str(scope or "").strip().lower() == "current_agent":
            sessions = [
                session
                for session in sessions
                if self._session_matches_current_surface(session)
            ]
        items: list[SidebarItem] = []
        for session in sessions:
            preview_records = self._rt.sessions.list_messages(
                session_id=session.id,
                limit=3,
            )
            preview_lines = [
                f"{self._role_to_sender(str(getattr(record, 'role', '') or '').strip().lower(), getattr(record, 'metadata', {}) or {})}: "
                f"{str(getattr(record, 'body', '') or '')[:40]}"
                for record in preview_records
                if str(getattr(record, "body", "") or "").strip()
            ]
            items.append(
                SidebarItem(
                    id=session.id,
                    label=session.id[:12],
                    active=(session.id == self._session_id),
                    meta={
                        "channel": session.channel,
                        "target": session.target,
                        "status": session.status,
                        "updated_at": session.updated_at,
                        "preview_lines": preview_lines,
                        "session_type": self._classify_session_type(session),
                    },
                )
            )
        return items

    @property
    def prompt_on_resume(self) -> bool:
        return self._prompt_on_resume

    @property
    def pending_candidate_session(self) -> Any | None:
        return self._pending_candidate_session

    def consume_pending_candidate_session(self) -> Any | None:
        candidate = self._pending_candidate_session
        self._pending_candidate_session = None
        return candidate

    def _refresh_pending_candidate(self) -> None:
        self._pending_candidate_session = None
        try:
            candidates = self._rt.sessions.list_sessions(limit=self._history_limit)
        except Exception:
            return
        best: Any | None = None
        for session in candidates:
            if not self._session_matches_current_surface(session):
                continue
            if best is None or _session_sort_key(session) > _session_sort_key(best):
                best = session
        self._pending_candidate_session = best

    def _session_matches_current_surface(self, session: Any) -> bool:
        target = str(getattr(session, "target", "") or "").strip()
        channel = str(getattr(session, "channel", "") or "").strip()
        if target != self._target:
            return False
        if channel and self._channel and channel != self._channel:
            return False
        agent_id = self._agent_id or self._agent_id_override or ""
        if not agent_id:
            return True
        session_key = str(getattr(session, "session_key", "") or "")
        if not session_key:
            return True
        agent_fragment = f"agent:{agent_id}|"
        return agent_fragment in session_key

    def _classify_session_type(self, session: Any) -> str:
        session_id = str(getattr(session, "id", "") or "")
        session_key = str(getattr(session, "session_key", "") or "")
        target = str(getattr(session, "target", "") or "").strip()
        channel = str(getattr(session, "channel", "") or "").strip()
        agent_id = self._agent_id or self._agent_id_override or ""
        if session_key and agent_id and target and channel:
            expected_fragment = f"agent:{agent_id}|channel:{channel}|target:{target}"
            if session_key == expected_fragment:
                return "default"
        if session_id.startswith("focus-"):
            return _TARGET_KIND_FOCUS
        if session_id.startswith("room-"):
            return "room"
        if session_id.startswith("sess-"):
            return "named"
        return "other"

    def list_agents(self) -> list[SidebarItem]:
        configured_agents = self._rt.list_registered_agents()
        return [
            SidebarItem(agent_id, agent_id, active=(agent_id == self._agent_id))
            for agent_id in configured_agents
        ]

    def list_tools(self) -> list[tuple[str, bool]]:
        tools = self._rt.tools.list()
        pairs = [
            (name, bool(getattr(tool_spec, "enabled", True)))
            for name, tool_spec in tools.items()
        ]
        pairs.sort(key=lambda item: item[0])
        return pairs

    def tool_exposure_status(self) -> dict[str, Any]:
        return self._rt.tool_exposure_status(session_id=self.session_id)

    def activate_tool_profile(
        self,
        profile_id: str,
        *,
        target_id: str = "",
        target_kind: str = "",
        credential_scopes: tuple[str, ...] = (),
        dependencies: tuple[str, ...] = (),
        approved: bool = False,
        ttl_seconds: float | None = None,
        activation_reason: str = "",
        approved_by: str = "",
        policy_source: str = "",
    ) -> dict[str, Any]:
        return self._rt.activate_tool_profile(
            profile_id,
            session_id=self.session_id,
            target_id=target_id,
            target_kind=target_kind,
            credential_scopes=credential_scopes,
            dependencies=dependencies,
            approved=approved,
            ttl_seconds=ttl_seconds,
            activation_reason=activation_reason,
            approved_by=approved_by,
            policy_source=policy_source,
        )

    def deactivate_tool_profile(
        self,
        profile_id: str,
        *,
        target_id: str = "",
    ) -> bool:
        return self._rt.deactivate_tool_profile(
            profile_id,
            session_id=self.session_id,
            target_id=target_id,
        )

    def switch_session(self, session_id: str) -> list[ChatMessage]:
        self.bind_session(session_id)
        return self.get_current_history()

    def switch_agent(self, agent_id: str) -> None:
        profile = self._rt.resolve_agent_profile(agent_id)
        self._agent_id = profile.name
        self._gateway = self._rt.resolve_gateway(self._agent_id)
        self._reset_token_usage_accounting()
        if self.is_bound and self._target == _TARGET_KIND_FOCUS:
            self._session_id = None
            self._sync_conversation_id()
        elif self._prompt_on_resume:
            self._session_id = None
            self._sync_conversation_id()
            self._refresh_pending_candidate()
        else:
            session = self._rt.sessions.resolve_session(
                agent_id=self._agent_id,
                channel=self._channel,
                target=self._target,
            )
            self._session_id = session.id
            self._sync_conversation_id()

    def new_session(self) -> str:
        return self.create_new_session()

    def bind_session(self, session_id: str) -> None:
        self._ensure_agent_resolved()
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            raise ValueError("session_id is required")
        record = self._rt.sessions.get_session(normalized_session_id)
        if record is None:
            raise ValueError(f"unknown session_id: {normalized_session_id}")
        if str(getattr(record, "target", "") or "").strip() != self._target:
            raise ValueError(f"session target mismatch: {record.target}")
        self._session_id = record.id
        self._sync_conversation_id()
        self._project_context_pending = False
        self._reset_token_usage_accounting()

    def _resolve_startup_session(self, session_id: str) -> None:
        self._ensure_agent_resolved()
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            raise ValueError("session_id is required")
        session = self._rt.sessions.resolve_session(
            agent_id=self.agent_id,
            channel=self._channel,
            target=self._target,
            session_id=normalized_session_id,
            metadata=self._session_metadata_patch(),
        )
        self._session_id = session.id
        self._sync_conversation_id()
        metadata_patch = self._session_metadata_patch()
        if metadata_patch:
            self._rt.sessions.update_session_metadata(
                session_id=session.id,
                patch=metadata_patch,
            )
        self._project_context_pending = False
        self._reset_token_usage_accounting()

    def create_new_session(self) -> str:
        self._ensure_agent_resolved()
        prefix = _TARGET_KIND_FOCUS if self._target == _TARGET_KIND_FOCUS else "sess"
        session = self._rt.sessions.resolve_session(
            agent_id=self.agent_id,
            channel=self._channel,
            target=self._target,
            session_id=f"{prefix}-{uuid4().hex}",
            metadata=self._session_metadata_patch(),
        )
        self._session_id = session.id
        self._sync_conversation_id()
        metadata_patch = self._session_metadata_patch()
        if metadata_patch:
            self._rt.sessions.update_session_metadata(
                session_id=session.id,
                patch=metadata_patch,
            )
        self._project_context_pending = self._project_context is not None
        self._reset_token_usage_accounting()
        return session.id

    def set_project_context(self, info: ProjectContextInfo | None) -> None:
        self._project_context = info
        self._project_context_pending = info is not None and self.is_bound

    def find_candidate_session(self):
        self._ensure_agent_resolved()
        if not self._working_dir:
            return None
        sessions = self.list_directory_sessions(limit=1)
        return sessions[0] if sessions else None

    def list_directory_sessions(self, *, limit: int = 20):
        self._ensure_agent_resolved()
        if not self._working_dir:
            return []
        return self._rt.sessions.list_sessions(
            limit=limit,
            newest_first=True,
            agent_id=self.agent_id,
            target=self._target,
            metadata_filter={"working_dir": self._working_dir},
        )

    async def send_message(
        self,
        text: str,
        *,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        inbound_metadata: dict[str, str] | None = None,
        approval_callback: ApprovalCallback | None = None,
    ) -> AsyncIterator[str]:
        timer = phase_timing.ChatPhaseTimer(cold_start=False)
        turn_id = uuid4().hex
        with phase_timing.use_chat_phase_timer(timer):
            try:
                async for chunk in self._send_message_impl(
                    text,
                    progress_callback=progress_callback,
                    inbound_metadata=inbound_metadata,
                    approval_callback=approval_callback,
                ):
                    if str(chunk or ""):
                        phase_timing.mark_active_chat_first_text()
                    yield chunk
            finally:
                self._record_chat_phase_timing(timer, turn_id=turn_id)

    def _record_chat_phase_timing(
        self,
        timer: phase_timing.ChatPhaseTimer,
        *,
        turn_id: str,
    ) -> None:
        try:
            runtime_settings = getattr(
                getattr(self._rt, "config", None), "runtime", None
            )
            payload = timer.build_payload(
                turn_id=turn_id,
                session_id=self.session_id,
                agent_id=self.agent_id,
                process_mode=str(getattr(runtime_settings, "process_mode", "") or ""),
                transport=self.transport,
            )
            phase_timing.record_chat_phase_timing_payload(
                getattr(self._rt, "telemetry_service", None),
                payload,
            )
        except (
            AttributeError,
            OSError,
            RuntimeError,
            SQLiteError,
            TypeError,
            ValueError,
        ) as exc:
            logger = getattr(self._rt, "logger", None)
            warning = getattr(logger, "warning", None)
            if callable(warning):
                warning("chat phase timing emission failed: %s", exc)

    def _turn_inbound_metadata(
        self,
        inbound_metadata: dict[str, str] | None,
    ) -> dict[str, str] | None:
        merged = self._merge_inbound_metadata(inbound_metadata) or {}
        overrides = {
            "override_provider": self._model_override_provider,
            "override_model": self._model_override_model,
            "override_thinking": self.effort_level,
            "effort_level": self.effort_level,
        }
        merged.update({key: value for key, value in overrides.items() if value})
        if self.permission_mode != PERMISSION_MODE_DEFAULT:
            merged["permission_mode"] = self.permission_mode
            if self.permission_mode == "readonly":
                merged["read_only"] = "1"
        if self._permission_overrides:
            merged["permission_overrides"] = json.dumps(
                self._permission_overrides,
                sort_keys=True,
            )
        if self.action_policy_mode_override:
            merged[ACTION_POLICY_SESSION_OVERRIDE_KEY] = (
                self.action_policy_mode_override
            )
        return merged or None

    def _prepare_gateway_turn(
        self,
        text: str,
        *,
        progress_callback: Callable[[dict[str, Any]], None] | None,
        inbound_metadata: dict[str, str] | None,
        approval_callback: ApprovalCallback | None,
    ) -> tuple[dict[str, Any], Callable[..., Any] | None, Callable[..., Any]]:
        kwargs: dict[str, Any] = {
            "channel": self._channel,
            "target": self._target,
            "body": text,
            "session_id": self.session_id,
        }
        merged_metadata = self._turn_inbound_metadata(inbound_metadata)
        if merged_metadata:
            kwargs["inbound_metadata"] = merged_metadata
        stream_handler = getattr(self._gateway, "handle_message_streaming", None)
        if not callable(stream_handler):
            stream_handler = None
        handler = stream_handler or self._gateway.handle_message
        parameters = inspect.signature(handler).parameters
        if "deliver" in parameters:
            kwargs["deliver"] = False
        wrapped_progress = self._wrap_progress_callback(progress_callback)
        if "progress_callback" in parameters:
            kwargs["progress_callback"] = wrapped_progress
        if approval_callback is not None and "approval_callback" in parameters:
            kwargs["approval_callback"] = approval_callback
        return kwargs, stream_handler, wrapped_progress

    async def _handle_gateway_message(self, kwargs: dict[str, Any]) -> Message:
        try:
            return await self._gateway.handle_message(**kwargs)
        except Exception:
            self._finalize_turn_usage(None, succeeded=False)
            raise

    async def _send_message_impl(
        self,
        text: str,
        *,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        inbound_metadata: dict[str, str] | None = None,
        approval_callback: ApprovalCallback | None = None,
    ) -> AsyncIterator[str]:
        self._ensure_agent_resolved()
        if not self.is_bound:
            raise RuntimeError("interactive runtime is not bound to a session")
        max_attempts = 2
        for attempt in range(1, max_attempts + 1):
            self._begin_turn_usage_tracking()
            kwargs, stream_handler, wrapped_progress = self._prepare_gateway_turn(
                text,
                progress_callback=progress_callback,
                inbound_metadata=inbound_metadata,
                approval_callback=approval_callback,
            )
            if stream_handler is not None:
                final_text = ""
                final_metadata: Mapping[str, Any] | None = None
                emitted_text = False
                try:
                    async for event in stream_handler(**kwargs):
                        kind = str(getattr(event, "kind", "") or "")
                        if kind == "assistant_token":
                            token = str(getattr(event, "text", "") or "")
                            if not token:
                                continue
                            phase_timing.mark_active_chat_provider_token()
                            emitted_text = True
                            final_text += token
                            yield token
                            continue
                        if kind == "final_message":
                            final_message = getattr(event, "final_message", None)
                            if isinstance(final_message, Mapping):
                                metadata = final_message.get("metadata", {})
                                final_metadata = (
                                    dict(metadata)
                                    if isinstance(metadata, Mapping)
                                    else {}
                                )
                                response = Message(
                                    channel=str(final_message.get("channel", "") or ""),
                                    target=str(final_message.get("target", "") or ""),
                                    body=str(final_message.get("body", "") or ""),
                                    metadata=dict(final_metadata),
                                )
                                final_text = self._message_text(response)
                            continue
                        progress_payload = self._progress_payload_from_stream_event(
                            event
                        )
                        if progress_payload:
                            wrapped_progress(progress_payload)
                except Exception:
                    self._finalize_turn_usage(None, succeeded=False)
                    raise
                retryable_failure = _retryable_turn_failure_message(final_text)
                if retryable_failure is not None:
                    self._finalize_turn_usage(final_metadata, succeeded=False)
                    if attempt < max_attempts and _is_retryable_turn_failure_text(
                        final_text
                    ):
                        continue
                    if not emitted_text:
                        yield retryable_failure
                    return
                self._finalize_turn_usage(final_metadata, succeeded=True)
                if final_text and not emitted_text:
                    yield final_text
                return
            response = await self._handle_gateway_message(kwargs)
            text_body = self._message_text(response)
            retryable_failure = _retryable_turn_failure_message(text_body)
            if retryable_failure is not None:
                self._finalize_turn_usage(
                    getattr(response, "metadata", None),
                    succeeded=False,
                )
                if attempt < max_attempts and _is_retryable_turn_failure_text(
                    text_body
                ):
                    continue
                yield retryable_failure
                return
            self._finalize_turn_usage(
                getattr(response, "metadata", None),
                succeeded=True,
            )
            yield text_body
            return

    def _merge_inbound_metadata(
        self,
        inbound_metadata: dict[str, str] | None,
    ) -> dict[str, str] | None:
        merged = dict(inbound_metadata or {})
        if self._working_dir:
            merged["workspace_root"] = self._working_dir
            merged["cwd"] = self._working_dir
        if self._project_context_pending and self._project_context is not None:
            merged.update(build_project_context_metadata(self._project_context))
            self._project_context_pending = False
        if (
            self._conversation_id
            and not str(merged.get("conversation_id", "") or "").strip()
        ):
            merged["conversation_id"] = self._conversation_id
        if (
            self._target == _TARGET_KIND_FOCUS
            and not str(merged.get(CALLER_HANDLES_DELIVERY_METADATA_KEY, "")).strip()
        ):
            merged[CALLER_HANDLES_DELIVERY_METADATA_KEY] = "true"
        return merged or None

    def _begin_turn_usage_tracking(self) -> None:
        self._current_turn_usage = None
        self._current_turn_has_live_deltas = False
        self._last_live_usage_update_at = None
        started_at = time.monotonic()
        self._current_turn_started_at_monotonic = started_at
        self._usage_updated_at_monotonic = started_at

    def _wrap_progress_callback(
        self,
        progress_callback: Callable[[dict[str, Any]], None] | None,
    ) -> Callable[[dict[str, Any]], None]:
        def _wrapped(payload: dict[str, Any]) -> None:
            self._consume_live_usage_payload(payload)
            if progress_callback is not None:
                progress_callback(payload)

        setattr(_wrapped, "__self__", getattr(progress_callback, "__self__", None))
        return _wrapped

    def _progress_payload_from_stream_event(self, event: Any) -> dict[str, Any]:
        kind = str(getattr(event, "kind", "") or "")
        if kind == "tool_call_started":
            return {
                "kind": "tool_started",
                "tool_name": str(getattr(event, "tool_name", "") or ""),
                "args": dict(getattr(event, "args", None) or {}),
                "call_id": str(getattr(event, "call_id", "") or ""),
                "state": str(getattr(event, "state", "") or ""),
                "model_tool_name": str(getattr(event, "model_tool_name", "") or ""),
                "runtime_tool_name": str(getattr(event, "runtime_tool_name", "") or ""),
                "runtime_binding_id": str(
                    getattr(event, "runtime_binding_id", "") or ""
                ),
                "runtime_fallback_used": bool(
                    getattr(event, "runtime_fallback_used", False)
                ),
                "runtime_fallback_chain": list(
                    getattr(event, "runtime_fallback_chain", None) or []
                ),
                "runtime_resolution_source": str(
                    getattr(event, "runtime_resolution_source", "") or ""
                ),
                "fallback_index": getattr(event, "fallback_index", None),
            }
        if kind == "tool_call_completed":
            return {
                "kind": "tool_completed",
                "tool_name": str(getattr(event, "tool_name", "") or ""),
                "args": dict(getattr(event, "args", None) or {}),
                "call_id": str(getattr(event, "call_id", "") or ""),
                "ok": bool(getattr(event, "ok", False)),
                "duration_ms": getattr(event, "duration_ms", None),
                "exit_code": getattr(event, "exit_code", None),
                "content": str(getattr(event, "text", "") or ""),
                "state": str(getattr(event, "state", "") or ""),
                "model_tool_name": str(getattr(event, "model_tool_name", "") or ""),
                "runtime_tool_name": str(getattr(event, "runtime_tool_name", "") or ""),
                "runtime_binding_id": str(
                    getattr(event, "runtime_binding_id", "") or ""
                ),
                "runtime_fallback_used": bool(
                    getattr(event, "runtime_fallback_used", False)
                ),
                "runtime_fallback_chain": list(
                    getattr(event, "runtime_fallback_chain", None) or []
                ),
                "runtime_resolution_source": str(
                    getattr(event, "runtime_resolution_source", "") or ""
                ),
                "fallback_index": getattr(event, "fallback_index", None),
            }
        if kind == "budget_event":
            payload = dict(getattr(event, "budget_payload", None) or {})
            payload.setdefault("kind", "budget_event")
            event_type = str(getattr(event, "budget_event_type", "") or "").strip()
            if event_type:
                payload.setdefault("event_type", event_type)
            return payload
        if kind == "status":
            payload = dict(getattr(event, "status_payload", None) or {})
            payload.setdefault("kind", "status")
            return payload
        return {}

    def _consume_live_usage_payload(self, payload: Mapping[str, Any] | None) -> None:
        turn_usage = usage_totals_from_mapping(payload)
        if turn_usage is None:
            return
        now = time.monotonic()
        last_updated = self._last_live_usage_update_at
        if (
            last_updated is not None
            and (now - last_updated) < _LIVE_USAGE_THROTTLE_SECONDS
        ):
            return
        self._current_turn_usage = turn_usage
        self._current_turn_has_live_deltas = True
        self._last_live_usage_update_at = now
        self._usage_updated_at_monotonic = now

    def _finalize_turn_usage(
        self,
        metadata: Mapping[str, Any] | None,
        *,
        succeeded: bool,
    ) -> None:
        now = time.monotonic()
        turn_started_at = self._current_turn_started_at_monotonic
        if turn_started_at is not None:
            self._last_turn_elapsed_seconds = max(0.0, now - turn_started_at)
        final_turn_usage = (
            usage_totals_from_mapping(metadata) or self._current_turn_usage
        )
        if succeeded and final_turn_usage is not None:
            self._last_turn_usage = final_turn_usage
            self._completed_session_usage = (
                accumulate_usage(self._completed_session_usage, final_turn_usage)
                or TokenUsageTotals()
            )
        self._current_turn_usage = None
        self._current_turn_has_live_deltas = False
        self._current_turn_started_at_monotonic = None
        self._last_live_usage_update_at = None
        self._usage_updated_at_monotonic = now

    def _reset_token_usage_accounting(self) -> None:
        self._completed_session_usage = TokenUsageTotals()
        self._last_turn_usage = TokenUsageTotals()
        self._current_turn_usage = None
        self._current_turn_has_live_deltas = False
        self._current_turn_started_at_monotonic = None
        self._last_turn_elapsed_seconds = None
        self._last_live_usage_update_at = None
        self._usage_updated_at_monotonic = None

    def _sync_conversation_id(self) -> None:
        session_id = str(self._session_id or "").strip()
        if self._target == _TARGET_KIND_FOCUS and session_id:
            self._conversation_id = f"focus-{session_id}"
            return
        self._conversation_id = ""

    def _context_limit_tokens(self) -> int | None:
        try:
            runtime_cfg = getattr(getattr(self._rt, "config", None), "runtime", None)
            value = getattr(runtime_cfg, "session_context_token_budget", None)
            if value in (None, "", 0, "0"):
                return None
            return max(0, int(value))
        except (TypeError, ValueError):
            return None

    def _ensure_agent_resolved(self) -> None:
        if self._gateway is not None and self._agent_id:
            return
        selected = self._selected_agent_id_for_config(self._rt.config)
        if not selected:
            raise ValueError("unable to resolve agent id for interactive runtime")
        profile = self._rt.resolve_agent_profile(selected)
        self._agent_id = str(profile.name).strip()
        self._gateway = self._rt.resolve_gateway(self._agent_id)

    def _session_metadata_patch(self) -> dict[str, Any]:
        patch: dict[str, Any] = {}
        if self._working_dir:
            patch["working_dir"] = self._working_dir
            patch["workspace_root"] = self._working_dir
            patch["cwd"] = self._working_dir
        if self._target == _TARGET_KIND_FOCUS:
            patch["focus_mode"] = True
        return patch

    @staticmethod
    def _normalize_working_dir(working_dir: str | None) -> str | None:
        raw = str(working_dir or "").strip()
        if not raw:
            return None
        return str(Path(raw).expanduser().resolve(strict=False))
