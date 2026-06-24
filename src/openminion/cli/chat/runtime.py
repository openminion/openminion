from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from openminion.api.runtime import APIRuntime
from openminion.api.turns import run_turn
from openminion.base.constants import OPENMINION_DAEMON_AUTO_START_ENV
from openminion.base.config.env import resolve_environment_config
from openminion.base.config.core import resolve_default_agent_id
from openminion.cli.presentation import styles
from openminion.cli.status import (
    TokenUsageSnapshot,
    TokenUsageTotals,
    accumulate_usage,
    build_token_usage_snapshot,
    format_token_usage_summary,
    usage_totals_from_mapping,
)
from openminion.services.stats import RunStats, format_run_stats_footer
from openminion.cli.commands.daemon import ensure_daemon_running
from .plan_hook import (
    capture_plan_snapshot,
    maybe_print_plan_render,
    maybe_print_plan_render_for_session_change,
)
from openminion.cli.transport.daemon_client import (
    DaemonStreamEvent,
    daemon_request,
    daemon_stream_request,
)
from .approval import ChatApprovalState, build_chat_approval_callback
from .ui import PhaseStatusDisplay, Spinner


def _trace_chat_turn(message: str) -> None:
    if (
        str(resolve_environment_config().get("OPENMINION_CHAT_TURN_TRACE", "")).strip()
        != "1"
    ):
        return
    print(f"[chat-trace] {message}", file=sys.stderr, flush=True)


@dataclass
class ChatRuntimeState:
    endpoint: Any
    transport: str
    inproc_runtime: APIRuntime | None
    mode: str
    auto_start: bool
    show_progress: bool
    show_activity_indicator: bool = True
    quiet: bool = False
    home_root: str | None = None
    data_root: str | None = None
    completed_session_usage: TokenUsageTotals = field(default_factory=TokenUsageTotals)
    last_turn_usage: TokenUsageTotals = field(default_factory=TokenUsageTotals)
    last_turn_elapsed_seconds: float | None = None
    usage_updated_at_monotonic: float | None = None
    # in-memory approval state for the chat process. `session_grants`
    # is a flat set of tool names allowed for the lifetime of this process;
    # persistent grants (`allow_always`) are deferred per spec §7.5.
    approval_state: ChatApprovalState = field(default_factory=ChatApprovalState)


def _resolve_daemon_auto_start(config) -> bool:
    env = resolve_environment_config()
    raw = str(env.get(OPENMINION_DAEMON_AUTO_START_ENV, "")).strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(config.runtime.daemon_auto_start)


def init_runtime_state(args, config) -> tuple[ChatRuntimeState, Exception | None]:
    mode = str(config.runtime.process_mode or "daemon").strip().lower()
    auto_start = _resolve_daemon_auto_start(config)
    quiet = bool(getattr(args, "quiet", False))
    show_progress = not bool(getattr(args, "no_progress", False))
    show_activity_indicator = not bool(getattr(args, "no_activity_indicator", False))

    endpoint = None
    transport = "in-process"
    error: Exception | None = None
    if mode != "single-process":
        try:
            endpoint = ensure_daemon_running(args.config, auto_start=auto_start)
            transport = f"daemon({endpoint.host}:{endpoint.port})"
        except RuntimeError as exc:
            endpoint = None
            transport = "in-process"
            error = exc

    return (
        ChatRuntimeState(
            endpoint=endpoint,
            transport=transport,
            inproc_runtime=None,
            mode=mode,
            auto_start=auto_start,
            show_progress=show_progress,
            show_activity_indicator=show_activity_indicator,
            quiet=quiet,
            home_root=str(getattr(args, "home_root", "") or "").strip() or None,
            data_root=str(getattr(args, "data_root", "") or "").strip() or None,
        ),
        error,
    )


def ensure_inproc_runtime(
    state: ChatRuntimeState, config_path: Optional[str]
) -> APIRuntime:
    if state.inproc_runtime is None:
        kwargs: dict[str, Any] = {
            "home_root": state.home_root,
            "data_root": state.data_root,
        }
        if state.quiet:
            kwargs["logging_mode"] = "interactive"
        state.inproc_runtime = APIRuntime.from_config_path(
            config_path,
            **kwargs,
        )
    return state.inproc_runtime


def request_daemon_turn(
    *,
    endpoint,
    payload: dict[str, Any],
    show_progress: bool,
    phase_status_callback: Callable[[object | dict[str, Any]], None] | None = None,
) -> tuple[int, dict]:
    if phase_status_callback is not None:

        def _handle_event(event: DaemonStreamEvent) -> None:
            if event.event != "chunk" or not isinstance(event.data, dict):
                return
            kind = str(event.data.get("kind", "")).strip()
            payload = event.data.get("data")
            if not isinstance(payload, dict):
                return
            event_payload = dict(payload)
            if kind and kind != "status":
                event_payload.setdefault("kind", kind)
            phase_status_callback(event_payload)

        return daemon_stream_request(
            endpoint=endpoint,
            method="POST",
            path="/v1/turn/stream",
            payload=payload,
            timeout_s=90,
            on_event=_handle_event,
        )
    with Spinner(enabled=show_progress):
        status, response = daemon_request(
            endpoint=endpoint,
            method="POST",
            path="/v1/turn/stream",
            payload=payload,
            timeout_s=90,
        )
    return status, response


def _format_stream_progress_note(payload: dict[str, Any]) -> str | None:
    from openminion.cli.status.activity_ledger import (
        KIND_STATUS,
        KIND_SUMMARY,
        activity_from_progress_payload,
        format_activity_line,
    )

    event = activity_from_progress_payload(payload)
    if event is None:
        return None
    if event.kind in {KIND_STATUS, KIND_SUMMARY}:
        return None
    return format_activity_line(event)


def build_chat_progress_callback(
    *,
    phase_display: PhaseStatusDisplay,
) -> Callable[[dict[str, Any] | object], None]:
    def _callback(payload: dict[str, Any] | object) -> None:
        if isinstance(payload, dict):
            note = _format_stream_progress_note(payload)
            if note:
                phase_display.emit_note(note)
                return
        phase_display.update(payload)

    return _callback


def request_inproc_turn(
    *,
    runtime: APIRuntime,
    config_path: Optional[str],
    payload: dict[str, Any],
    show_progress: bool,
    phase_status_callback: Callable[[object], None] | None = None,
    approval_callback: Any | None = None,
) -> dict:
    if phase_status_callback is not None or approval_callback is not None:
        return run_turn(
            config_path=config_path,
            runtime=runtime,
            payload=payload,
            progress_callback=phase_status_callback,
            approval_callback=approval_callback,
        )
    with Spinner(enabled=show_progress):
        return run_turn(
            config_path=config_path,
            runtime=runtime,
            payload=payload,
        )


def emit_session_event(
    *,
    state: ChatRuntimeState,
    config_path: Optional[str],
    session_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    normalized_session = str(session_id or "").strip()
    normalized_event = str(event_type or "").strip()
    if not normalized_session or not normalized_event:
        return
    if state.endpoint is not None:
        try:
            daemon_request(
                endpoint=state.endpoint,
                method="POST",
                path=f"/sessions/{normalized_session}/events",
                payload={"event_type": normalized_event, "payload": payload},
                timeout_s=5,
            )
        except RuntimeError:
            return
        return
    try:
        runtime = ensure_inproc_runtime(state, config_path)
    except Exception:
        return
    try:
        if runtime.sessions.get_session(normalized_session) is None:
            _default_runtime_agent_id = resolve_default_agent_id(runtime.config)
            runtime.sessions.resolve_session(
                agent_id=str(
                    payload.get("selected_profile_id")
                    or payload.get("profile_agent_id")
                    or payload.get("agent_id")
                    or _default_runtime_agent_id
                ).strip()
                or _default_runtime_agent_id,
                channel=str(payload.get("channel", "")).strip() or "console",
                target=str(payload.get("target", "")).strip() or "cli-chat",
                session_id=normalized_session,
                metadata=payload,
            )
        runtime.sessions.append_event(
            session_id=normalized_session,
            event_type=normalized_event,
            payload=payload,
        )
    except Exception:
        return


def close_runtime(state: ChatRuntimeState) -> None:
    if state.inproc_runtime is not None:
        state.inproc_runtime.close()


def is_retryable_turn_error(error: object) -> bool:
    normalized = str(error).strip().lower()
    if not normalized:
        return False
    retryable_tokens = (
        "timeout",
        "timed out",
        "network",
        "connection reset",
        "connection refused",
        "temporarily unavailable",
        "rate limited",
        "did not include text content",
        "missing choices",
        "invalid choice payload",
        "missing message payload",
        "required completion contract",
        "finalization_status contract",
        "response was not valid json",
        "service unavailable",
        "gateway timeout",
    )
    return any(token in normalized for token in retryable_tokens)


def format_api_error(payload: dict, status: int) -> str:
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = str(error.get("message", "")).strip()
            if message:
                return f"daemon request failed ({status}): {message}"
    return f"daemon request failed ({status})"


def _chat_token_usage_snapshot(state: ChatRuntimeState) -> TokenUsageSnapshot | None:
    if not hasattr(state, "last_turn_usage"):
        setattr(state, "last_turn_usage", TokenUsageTotals())
    if not hasattr(state, "completed_session_usage"):
        setattr(state, "completed_session_usage", TokenUsageTotals())
    if not hasattr(state, "last_turn_elapsed_seconds"):
        setattr(state, "last_turn_elapsed_seconds", None)
    if not hasattr(state, "usage_updated_at_monotonic"):
        setattr(state, "usage_updated_at_monotonic", None)
    if (
        state.last_turn_usage.is_empty
        and state.completed_session_usage.is_empty
        and state.last_turn_elapsed_seconds is None
    ):
        return None
    snapshot = build_token_usage_snapshot(
        turn=None if state.last_turn_usage.is_empty else state.last_turn_usage,
        session=(
            None
            if state.completed_session_usage.is_empty
            else state.completed_session_usage
        ),
        context_used_tokens=None,
        context_limit_tokens=None,
        has_live_deltas=False,
        turn_elapsed_seconds=state.last_turn_elapsed_seconds,
        updated_at_monotonic=state.usage_updated_at_monotonic,
    )
    return snapshot if snapshot.has_any_usage else None


def _record_chat_turn_usage(
    state: ChatRuntimeState,
    *,
    payload: dict[str, Any] | None,
    elapsed_seconds: float,
) -> str:
    _chat_token_usage_snapshot(state)
    usage = usage_totals_from_mapping(payload)
    if usage is not None:
        state.last_turn_usage = usage
        state.completed_session_usage = (
            accumulate_usage(state.completed_session_usage, usage) or TokenUsageTotals()
        )
    state.last_turn_elapsed_seconds = max(0.0, float(elapsed_seconds))
    now = time.monotonic()
    state.usage_updated_at_monotonic = now
    snapshot = _chat_token_usage_snapshot(state)
    if snapshot is None:
        return ""
    return format_token_usage_summary(snapshot, now_monotonic=now)


def _format_post_turn_footer(
    state: ChatRuntimeState,
    *,
    payload: dict[str, Any] | None,
    elapsed_seconds: float,
) -> str:
    payload_map = dict(payload or {})
    legacy_summary = _record_chat_turn_usage(
        state,
        payload=payload_map.get("metadata")
        if isinstance(payload_map.get("metadata"), dict)
        else payload_map,
        elapsed_seconds=elapsed_seconds,
    )
    stats = RunStats.from_mapping(payload_map.get("stats"))
    if stats is not None:
        return format_run_stats_footer(stats)
    return legacy_summary


def print_turn_usage_summary(summary: str) -> None:
    normalized = str(summary or "").strip()
    if not normalized:
        return
    print(styles.style(styles.StyleToken.SYSTEM, f"[chat] {normalized}"))


def _is_replayed_response(metadata: dict[str, Any] | None) -> bool:
    if not isinstance(metadata, dict):
        return False
    return str(metadata.get("replayed_response", "")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _chat_failure_message_from_metadata(metadata: dict[str, Any] | None) -> str | None:
    if not isinstance(metadata, dict):
        return None
    if str(metadata.get("pae_idle_tick_noop", "")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return None
    explicit_error = str(metadata.get("error", "") or "").strip()
    if explicit_error:
        if "finalization_status contract" in explicit_error:
            return (
                "The model ended the turn without the required completion contract. "
                "Please try again."
            )
        return explicit_error
    termination_reason = str(
        metadata.get("tool_loop_termination_reason", "") or ""
    ).strip()
    fallback_messages = {
        "finalization_contract_missing": (
            "The model ended the turn without the required completion contract. "
            "Please try again."
        ),
        "duplicate_tool_calls": (
            "The model repeated the same tool request and the turn could not complete."
        ),
        "tool_loop_max_steps": (
            "The turn hit the tool-step limit before it could finish."
        ),
        "tool_no_success": (
            "The tool work did not complete successfully enough to finish the turn."
        ),
        "tool_arg_exhausted": (
            "The model exhausted its tool-call retries before completing the turn."
        ),
    }
    return fallback_messages.get(termination_reason)


def _chat_failure_message_from_text(text: str) -> str | None:
    normalized = str(text or "").strip()
    if not normalized:
        return None
    lowered = normalized.lower()
    if "finalization_status contract" in lowered:
        return (
            "The model ended the turn without the required completion contract. "
            "Please try again."
        )
    if "adaptive loop stopped unexpectedly" in lowered:
        return (
            "The turn stopped unexpectedly before it could complete. Please try again."
        )
    return None


@dataclass(frozen=True)
class TurnExecutionDeps:
    request_daemon_turn: Callable[..., tuple[int, dict]]
    ensure_inproc_runtime: Callable[..., APIRuntime]
    set_quiet_log_level: Callable[[], None]
    request_inproc_turn: Callable[..., dict]
    format_api_error: Callable[[dict, int], str]
    is_retryable_turn_error: Callable[[object], bool]
    print_fallback_notice: Callable[[Exception], None]
    print_turn_error: Callable[[object], None]
    print_assistant_text: Callable[..., None]
    print_turn_usage_summary: Callable[[str], None]
    emit_session_event_safe: Callable[..., None]
    build_run_profile_override_payload: Callable[[Any], dict[str, str]]


def _resolve_progress_modes(runtime_state: ChatRuntimeState) -> tuple[bool, bool]:
    show_progress = bool(getattr(runtime_state, "show_progress", True))
    status_enabled = show_progress
    activity_enabled = (
        bool(getattr(runtime_state, "show_activity_indicator", True))
        and not status_enabled
    )
    return status_enabled, activity_enabled


def _execute_inproc_turn(
    *,
    runtime_state: ChatRuntimeState,
    args: Any,
    payload: dict[str, Any],
    inbound_metadata: dict[str, Any],
    line: str,
    agent_id: str,
    session_id: str,
    lifecycle_payload: dict[str, str],
    chat_turn_timeout: float,
    attempt: int,
    chat_turn_max_attempts: int,
    deps: TurnExecutionDeps,
    turn_started_at: float,
    status_enabled: bool,
    activity_enabled: bool,
) -> dict[str, Any]:
    _trace_chat_turn("inproc turn start")
    if runtime_state.quiet:
        deps.set_quiet_log_level()
        _trace_chat_turn("quiet log level set")
    inproc_runtime = deps.ensure_inproc_runtime(runtime_state, args.config)
    _trace_chat_turn("inproc runtime ready")
    pre_turn_plan_snapshot = capture_plan_snapshot(session_id)
    try:
        if status_enabled:
            with PhaseStatusDisplay(
                enabled=True,
                animate=runtime_state.show_progress,
            ) as phase_display:
                progress_callback = build_chat_progress_callback(
                    phase_display=phase_display
                )
                turn = deps.request_inproc_turn(
                    runtime=inproc_runtime,
                    config_path=args.config,
                    payload={
                        "message": line,
                        "agent_id": agent_id,
                        "session_id": session_id,
                        "channel": "console",
                        "target": "cli-chat",
                        "deliver": False,
                        "inbound_metadata": inbound_metadata,
                        "idempotency_key": payload["idempotency_key"],
                        "timeout_seconds": chat_turn_timeout,
                        **deps.build_run_profile_override_payload(args),
                    },
                    show_progress=runtime_state.show_progress,
                    phase_status_callback=progress_callback,
                    approval_callback=build_chat_approval_callback(
                        state=runtime_state.approval_state
                    ),
                )
        else:
            _trace_chat_turn(f"request_inproc_turn spinner enabled={activity_enabled}")
            with Spinner(
                enabled=activity_enabled,
                label="[chat] processing turn...",
                show_elapsed=True,
            ):
                turn = deps.request_inproc_turn(
                    runtime=inproc_runtime,
                    config_path=args.config,
                    payload={
                        "message": line,
                        "agent_id": agent_id,
                        "session_id": session_id,
                        "channel": "console",
                        "target": "cli-chat",
                        "deliver": False,
                        "inbound_metadata": inbound_metadata,
                        "idempotency_key": payload["idempotency_key"],
                        "timeout_seconds": chat_turn_timeout,
                        **deps.build_run_profile_override_payload(args),
                    },
                    show_progress=False,
                    phase_status_callback=None,
                    approval_callback=build_chat_approval_callback(
                        state=runtime_state.approval_state
                    ),
                )
            _trace_chat_turn("request_inproc_turn returned")
    except Exception as exc:
        _trace_chat_turn(f"request_inproc_turn raised: {exc}")
        message = str(exc).strip() or "turn failed"
        if attempt < chat_turn_max_attempts and deps.is_retryable_turn_error(message):
            return {"retry": True, "error": message}
        deps.print_turn_error(exc)
        return {"stop": True}

    metadata = turn.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    replayed_response = _is_replayed_response(metadata)
    if replayed_response:
        print(
            styles.style(
                styles.StyleToken.WARNING,
                "pending response replayed; retrying your message once",
            )
        )
    text = str(turn.get("body", "")).strip()
    if text:
        _trace_chat_turn("assistant text available")
        body_failure_message = _chat_failure_message_from_text(text)
        if body_failure_message:
            if attempt < chat_turn_max_attempts and deps.is_retryable_turn_error(
                body_failure_message
            ):
                return {"retry": True, "error": body_failure_message}
            deps.print_turn_error(body_failure_message)
            deps.print_turn_usage_summary(
                _format_post_turn_footer(
                    runtime_state,
                    payload=turn,
                    elapsed_seconds=time.monotonic() - turn_started_at,
                )
            )
            return {
                "stop": True,
                "last_turn_debug": {
                    "source": "in_process",
                    "run_id": str(turn.get("run_id", "")).strip() or None,
                    "run_state": str(turn.get("run_state", "")).strip() or None,
                    "trace_id": str(metadata.get("trace_id", "")).strip() or None,
                    "metadata": metadata,
                    "body": text,
                    "body_preview": text[:200],
                    "failure_message": body_failure_message,
                },
            }
        rendered = maybe_print_plan_render(metadata)
        if not rendered:
            maybe_print_plan_render_for_session_change(
                session_id=session_id,
                previous_snapshot=pre_turn_plan_snapshot,
            )
        deps.print_assistant_text(text=text, session_id=session_id, agent_id=agent_id)
        _trace_chat_turn("assistant text printed")
        deps.print_turn_usage_summary(
            _format_post_turn_footer(
                runtime_state,
                payload=turn,
                elapsed_seconds=time.monotonic() - turn_started_at,
            )
        )
    else:
        failure_message = _chat_failure_message_from_metadata(metadata)
        if failure_message:
            deps.print_turn_error(failure_message)
            deps.print_turn_usage_summary(
                _format_post_turn_footer(
                    runtime_state,
                    payload=turn,
                    elapsed_seconds=time.monotonic() - turn_started_at,
                )
            )
            return {
                "stop": True,
                "last_turn_debug": {
                    "source": "in_process",
                    "run_id": str(turn.get("run_id", "")).strip() or None,
                    "run_state": str(turn.get("run_state", "")).strip() or None,
                    "trace_id": str(metadata.get("trace_id", "")).strip() or None,
                    "metadata": metadata,
                    "body": "",
                    "body_preview": "",
                    "failure_message": failure_message,
                },
            }
    if replayed_response and attempt >= chat_turn_max_attempts:
        print(
            styles.style(
                styles.StyleToken.WARNING,
                "pending response replayed; please re-send your message",
            )
        )
    deps.emit_session_event_safe(
        state=runtime_state,
        config_path=args.config,
        session_id=session_id,
        event_type="response.acked",
        lifecycle_payload=lifecycle_payload,
        extra_payload={
            "run_id": str(turn.get("run_id", "")).strip(),
            "trace_id": str(metadata.get("trace_id", "")).strip(),
        },
    )
    _trace_chat_turn("response ack emitted")
    if replayed_response and attempt < chat_turn_max_attempts:
        return {"retry": True, "replayed_response": True}
    return {
        "retry": False,
        "last_turn_debug": {
            "source": "in_process",
            "run_id": str(turn.get("run_id", "")).strip() or None,
            "run_state": str(turn.get("run_state", "")).strip() or None,
            "trace_id": str(metadata.get("trace_id", "")).strip() or None,
            "metadata": metadata,
            "body": text,
            "body_preview": text[:200],
        },
    }


def execute_turn(
    *,
    runtime_state: ChatRuntimeState,
    args: Any,
    payload: dict[str, Any],
    inbound_metadata: dict[str, Any],
    line: str,
    agent_id: str,
    session_id: str,
    lifecycle_payload: dict[str, str],
    chat_turn_timeout: float,
    attempt: int,
    chat_turn_max_attempts: int,
    deps: TurnExecutionDeps,
) -> dict[str, Any]:
    response = None
    status = 0
    turn_started_at = time.monotonic()
    status_enabled, activity_enabled = _resolve_progress_modes(runtime_state)

    if runtime_state.endpoint is not None:
        try:
            if status_enabled:
                with PhaseStatusDisplay(
                    enabled=True,
                    animate=runtime_state.show_progress,
                ) as phase_display:
                    progress_callback = build_chat_progress_callback(
                        phase_display=phase_display
                    )
                    status, response = deps.request_daemon_turn(
                        endpoint=runtime_state.endpoint,
                        payload=payload,
                        show_progress=runtime_state.show_progress,
                        phase_status_callback=progress_callback,
                    )
            else:
                with Spinner(
                    enabled=activity_enabled,
                    label="[chat] processing turn...",
                    show_elapsed=True,
                ):
                    status, response = deps.request_daemon_turn(
                        endpoint=runtime_state.endpoint,
                        payload=payload,
                        show_progress=False,
                        phase_status_callback=None,
                    )
        except RuntimeError as exc:
            response = None
            runtime_state.endpoint = None
            runtime_state.transport = "in-process"
            deps.print_fallback_notice(exc)
        else:
            if status >= 400 or not response.get("ok", False):
                message = deps.format_api_error(response, status)
                if attempt < chat_turn_max_attempts and deps.is_retryable_turn_error(
                    message
                ):
                    return {"retry": True, "error": message}
                deps.print_turn_error(message)
                return {"stop": True}

    if response is None:
        return _execute_inproc_turn(
            runtime_state=runtime_state,
            args=args,
            payload=payload,
            inbound_metadata=inbound_metadata,
            line=line,
            agent_id=agent_id,
            session_id=session_id,
            lifecycle_payload=lifecycle_payload,
            chat_turn_timeout=chat_turn_timeout,
            attempt=attempt,
            chat_turn_max_attempts=chat_turn_max_attempts,
            deps=deps,
            turn_started_at=turn_started_at,
            status_enabled=status_enabled,
            activity_enabled=activity_enabled,
        )

    turn_payload = response.get("turn") if isinstance(response, dict) else None
    if turn_payload is None:
        message = deps.format_api_error(response, status)
        if attempt < chat_turn_max_attempts and deps.is_retryable_turn_error(message):
            return {"retry": True, "error": message}
        deps.print_turn_error(message)
        return {
            "stop": True,
            "last_turn_debug": {
                "source": "daemon",
                "trace_id": None,
                "metadata": {},
                "tool_calls_summary": None,
                "errors": None,
                "artifacts": None,
                "final_text_preview": "",
                "failure_message": message,
            },
        }
    if isinstance(turn_payload, dict):
        metadata = turn_payload.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        replayed_response = _is_replayed_response(metadata)
        if replayed_response:
            print(
                styles.style(
                    styles.StyleToken.WARNING,
                    "pending response replayed; retrying your message once",
                )
            )
        text = (
            str(turn_payload.get("final_text", "")).strip()
            or str(turn_payload.get("body", "")).strip()
        )
        if text:
            maybe_print_plan_render(metadata)
            deps.print_assistant_text(
                text=text, session_id=session_id, agent_id=agent_id
            )
            deps.print_turn_usage_summary(
                _format_post_turn_footer(
                    runtime_state,
                    payload=turn_payload,
                    elapsed_seconds=time.monotonic() - turn_started_at,
                )
            )
        if replayed_response and attempt >= chat_turn_max_attempts:
            print(
                styles.style(
                    styles.StyleToken.WARNING,
                    "pending response replayed; please re-send your message",
                )
            )
        deps.emit_session_event_safe(
            state=runtime_state,
            config_path=args.config,
            session_id=session_id,
            event_type="response.acked",
            lifecycle_payload=lifecycle_payload,
            extra_payload={
                "trace_id": str(turn_payload.get("trace_id", "")).strip(),
            },
        )
        if replayed_response and attempt < chat_turn_max_attempts:
            return {"retry": True, "replayed_response": True}
        artifacts = turn_payload.get("artifacts")
        return {
            "retry": False,
            "last_artifacts": artifacts if isinstance(artifacts, list) else None,
            "last_turn_debug": {
                "source": "daemon",
                "trace_id": str(turn_payload.get("trace_id", "")).strip() or None,
                "telemetry": turn_payload.get("telemetry"),
                "metadata": metadata,
                "tool_calls_summary": turn_payload.get("tool_calls_summary"),
                "errors": turn_payload.get("errors"),
                "artifacts": turn_payload.get("artifacts"),
                "final_text_preview": text[:200],
            },
        }

    return {"stop": True}
