from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any

from openminion.api.runtime import APIRuntime
from openminion.api.turns import run_turn
from openminion.base.config import (
    OpenMinionConfig,
    resolve_config_path,
    run_profile_overrides_from_mapping,
    save_config,
)
from openminion.base.config.core import resolve_default_agent_id
from openminion.base.config.env import resolve_environment_config
from openminion.cli.chat import lifecycle as chat_lifecycle
from openminion.cli.chat import repl as chat_repl
from openminion.cli.chat import runner as chat_runner
from openminion.cli.chat import runtime as chat_runtime
from openminion.cli.chat import session as chat_session
from openminion.cli.chat import startup as chat_startup
from openminion.cli.chat import ui as chat_ui
from openminion.cli.chat.args import normalize_chat_args
from openminion.cli.chat.commands import ChatCommandResult, handle_chat_command
from openminion.cli.chat.runtime import (
    ChatRuntimeState,
    close_runtime,
    emit_session_event,
    ensure_inproc_runtime,
    format_api_error,
    init_runtime_state,
    is_retryable_turn_error,
    request_daemon_turn,
    request_inproc_turn,
)
from openminion.cli.chat.ui import (
    chat_input_prompt,
    print_assistant_text,
    print_fallback_notice,
    print_turn_error,
    set_quiet_log_level,
)
from openminion.cli.commands.daemon import ensure_daemon_running
from openminion.cli.config import load_cli_config, resolve_cli_roots
from openminion.cli.identity.sync import sync_cli_identity_profiles
from openminion.cli.constants import OPENMINION_CONVERSATION_ID_ENV
from openminion.cli.transport.daemon_client import daemon_request, daemon_stream_request
from openminion.modules.cli_common import has_tty


load_config = load_cli_config


def _should_suppress_console_info_logs(*, chat_args: Any) -> bool:
    return bool(getattr(chat_args, "quiet", False)) or (
        bool(getattr(chat_args, "show_progress", False)) and has_tty()
    )


chat_turn_max_attempts_DEFAULT = 2
_CHAT_TURN_TIMEOUT_SECONDS_DEFAULT = 90.0
_CHAT_TURN_IDEMPOTENCY_PREFIX = "cli-chat"
_SESSION_STALE_TIMEOUT_SECONDS_DEFAULT = 24 * 60 * 60
_SESSION_AUTO_NAME_MAX_CHARS = 60


def _resolve_chat_roots(args) -> tuple[Path, object]:
    return chat_startup.resolve_chat_roots(
        args,
        resolve_cli_roots=resolve_cli_roots,
        resolve_config_path=resolve_config_path,
    )


def _print_onboarding_fail_fast(status: Any) -> int:
    return chat_startup.print_onboarding_fail_fast(status)


def _inspect_chat_onboarding(args) -> tuple[Any, Path, object]:
    return chat_startup.inspect_chat_onboarding(
        args,
        resolve_chat_roots_fn=_resolve_chat_roots,
        has_tty_fn=has_tty,
    )


def _build_demo_chat_config(*, agent_name: str, data_root: Path) -> OpenMinionConfig:
    return chat_startup.build_demo_chat_config(
        agent_name=agent_name,
        data_root=data_root,
    )


def _materialize_demo_config_for_chat(args, *, roots, config_path: Path) -> Path:
    return chat_startup.materialize_demo_config_for_chat(
        args,
        roots=roots,
        config_path=config_path,
        build_demo_chat_config_fn=_build_demo_chat_config,
        save_config_fn=save_config,
    )


def _run_inline_setup_for_chat(args) -> int:
    from openminion.cli.commands.setup import run_setup

    return chat_startup.run_inline_setup_for_chat(
        args,
        run_setup_fn=run_setup,
    )


def _generate_conversation_id() -> str:
    return chat_lifecycle.generate_conversation_id()


def _with_session_store(
    *,
    config_path: str | None,
    default: Any,
    operation: Any,
) -> Any:
    return chat_session.with_session_store(
        config_path=config_path,
        default=default,
        operation=operation,
        resolve_cli_roots_fn=resolve_cli_roots,
    )


def _latest_session_conversation_id(*, session_id: str, config_path: str | None) -> str:
    return chat_session.latest_session_conversation_id(
        session_id=session_id,
        config_path=config_path,
        resolve_cli_roots_fn=resolve_cli_roots,
    )


def _latest_session_agent_id(*, session_id: str, config_path: str | None) -> str:
    return chat_session.latest_session_agent_id(
        session_id=session_id,
        config_path=config_path,
        resolve_cli_roots_fn=resolve_cli_roots,
    )


def _session_allows_agent_id(
    *,
    session_id: str,
    agent_id: str,
    config_path: str | None,
) -> bool:
    return chat_session.session_allows_agent_id(
        session_id=session_id,
        agent_id=agent_id,
        config_path=config_path,
        resolve_cli_roots_fn=resolve_cli_roots,
    )


def _local_human_post_block_reason(
    *,
    session_id: str,
    config_path: str | None,
) -> str:
    return chat_session.local_human_post_block_reason(
        session_id=session_id,
        config_path=config_path,
        resolve_cli_roots_fn=resolve_cli_roots,
    )


def _get_session_record(*, session_id: str, config_path: str | None) -> Any:
    return chat_session.get_session_record(
        session_id=session_id,
        config_path=config_path,
        resolve_cli_roots_fn=resolve_cli_roots,
    )


def _close_session_record(
    *,
    session_id: str,
    config_path: str | None,
    reason: str,
) -> bool:
    return chat_session.close_session_record(
        session_id=session_id,
        config_path=config_path,
        reason=reason,
        resolve_cli_roots_fn=resolve_cli_roots,
    )


def _mark_stale_cli_sessions(*, config_path: str | None, timeout_seconds: int) -> int:
    return chat_session.mark_stale_cli_sessions(
        config_path=config_path,
        timeout_seconds=timeout_seconds,
        resolve_cli_roots_fn=resolve_cli_roots,
    )


def _ensure_cli_session_record(
    *,
    session_id: str,
    agent_id: str,
    config_path: str | None,
) -> bool:
    return chat_session.ensure_cli_session_record(
        session_id=session_id,
        agent_id=agent_id,
        config_path=config_path,
        resolve_cli_roots_fn=resolve_cli_roots,
    )


def _set_session_name_if_missing(
    *,
    session_id: str,
    config_path: str | None,
    name: str,
) -> bool:
    return chat_session.set_session_name_if_missing(
        session_id=session_id,
        config_path=config_path,
        name=name,
        max_chars=_SESSION_AUTO_NAME_MAX_CHARS,
        resolve_cli_roots_fn=resolve_cli_roots,
    )


def _maybe_auto_name_session(
    *,
    session_id: str,
    config_path: str | None,
    first_user_text: str,
) -> bool:
    return chat_session.maybe_auto_name_session(
        session_id=session_id,
        config_path=config_path,
        first_user_text=first_user_text,
        max_chars=_SESSION_AUTO_NAME_MAX_CHARS,
        resolve_cli_roots_fn=resolve_cli_roots,
    )


def _emit_session_open_events(
    *,
    state: ChatRuntimeState,
    config_path: str | None,
    session_id: str,
    lifecycle_payload: dict[str, Any],
    agent_id: str,
    previously_existed: bool,
) -> None:
    extra_payload = {"selected_profile_id": agent_id}
    _emit_session_event_safe(
        state=state,
        config_path=config_path,
        session_id=session_id,
        event_type="session.resumed" if previously_existed else "session.created",
        lifecycle_payload=lifecycle_payload,
        extra_payload=extra_payload,
    )
    _emit_session_event_safe(
        state=state,
        config_path=config_path,
        session_id=session_id,
        event_type="client.attach",
        lifecycle_payload=lifecycle_payload,
        extra_payload=extra_payload,
    )


def _print_stale_session_notice(
    *,
    session_id: str,
    config_path: str | None,
    reset_requested: bool,
) -> None:
    chat_ui.print_stale_session_notice(
        session_id=session_id,
        config_path=config_path,
        reset_requested=reset_requested,
        get_session_record_fn=_get_session_record,
    )


def _resolve_initial_chat_agent_id(
    args,
    *,
    config,
    session_id: str,
) -> tuple[str, dict[str, str]]:
    explicit_agent_id = str(getattr(args, "agent", "") or "").strip()
    try:
        default_agent_id = resolve_default_agent_id(config)
    except Exception:
        default_agent_id = ""
    reset_requested = bool(getattr(args, "reset_session", False))
    session_agent_id = ""
    if not reset_requested:
        session_agent_id = _latest_session_agent_id(
            session_id=session_id,
            config_path=getattr(args, "config", None),
        )

    if explicit_agent_id:
        resolution = {
            "source": "explicit",
            "session_agent_id": session_agent_id,
            "default_agent_id": default_agent_id,
        }
        return explicit_agent_id, resolution

    if session_agent_id:
        resolution = {
            "source": "session_resume",
            "session_agent_id": session_agent_id,
            "default_agent_id": default_agent_id,
        }
        return session_agent_id, resolution

    resolution = {
        "source": "config_default",
        "session_agent_id": "",
        "default_agent_id": default_agent_id,
    }
    return default_agent_id, resolution


def _resolve_conversation_selection(
    args,
    *,
    session_id: str,
    config_path: str | None = None,
    force_fresh: bool = False,
) -> dict[str, str]:
    return chat_lifecycle.resolve_conversation_selection(
        args,
        session_id=session_id,
        config_path=config_path,
        force_fresh=force_fresh,
        conversation_env_name=OPENMINION_CONVERSATION_ID_ENV,
        resolve_environment_config_fn=resolve_environment_config,
        latest_session_conversation_id_fn=_latest_session_conversation_id,
        generate_conversation_id_fn=_generate_conversation_id,
    )


def _build_turn_idempotency_key(
    *,
    agent_id: str,
    session_id: str,
    conversation_id: str,
    thread_id: str,
    turn_nonce: str,
) -> str:
    return chat_lifecycle.build_turn_idempotency_key(
        agent_id=agent_id,
        session_id=session_id,
        conversation_id=conversation_id,
        thread_id=thread_id,
        turn_nonce=turn_nonce,
        prefix=_CHAT_TURN_IDEMPOTENCY_PREFIX,
    )


def _build_lifecycle_payload(
    *,
    conversation_id: str,
    thread_id: str,
    attach_id: str,
    source: str = "cli-chat",
) -> dict[str, str]:
    return chat_lifecycle.build_lifecycle_payload(
        conversation_id=conversation_id,
        thread_id=thread_id,
        attach_id=attach_id,
        source=source,
    )


def _resolve_lifecycle_state(
    args,
    *,
    session_id: str,
    config_path: str | None,
    force_fresh: bool = False,
) -> tuple[dict[str, str], str, str, str, dict[str, str]]:
    return chat_lifecycle.resolve_lifecycle_state(
        args,
        session_id=session_id,
        config_path=config_path,
        force_fresh=force_fresh,
        resolve_conversation_selection_fn=_resolve_conversation_selection,
        build_lifecycle_payload_fn=_build_lifecycle_payload,
    )


def _emit_session_event_safe(
    *,
    state: ChatRuntimeState,
    config_path: str | None,
    session_id: str,
    event_type: str,
    lifecycle_payload: dict[str, Any],
    extra_payload: dict[str, Any] | None = None,
) -> None:
    payload = dict(lifecycle_payload)
    if extra_payload:
        payload.update(extra_payload)
    emit_session_event(
        state=state,
        config_path=config_path,
        session_id=session_id,
        event_type=event_type,
        payload=payload,
    )


def _handle_repl_command(
    *,
    command_result: ChatCommandResult,
    args,
    config,
    runtime_state: ChatRuntimeState,
    agent_id: str,
    session_id: str,
    conversation_selection: dict[str, str],
    conversation_id: str,
    thread_id: str,
    attach_id: str,
    lifecycle_payload: dict[str, str],
    conversation_id_fixed: bool,
    resume_requested: bool,
    reset_requested: bool,
) -> dict[str, Any]:
    return chat_repl.handle_repl_command(
        command_result=command_result,
        args=args,
        config=config,
        runtime_state=runtime_state,
        agent_id=agent_id,
        session_id=session_id,
        conversation_selection=conversation_selection,
        conversation_id=conversation_id,
        thread_id=thread_id,
        attach_id=attach_id,
        lifecycle_payload=lifecycle_payload,
        conversation_id_fixed=conversation_id_fixed,
        resume_requested=resume_requested,
        reset_requested=reset_requested,
        deps=chat_repl.ReplCommandDeps(
            close_session_record=_close_session_record,
            get_session_record=_get_session_record,
            resolve_lifecycle_state=_resolve_lifecycle_state,
            build_lifecycle_payload=_build_lifecycle_payload,
            ensure_cli_session_record=_ensure_cli_session_record,
            emit_session_open_events=_emit_session_open_events,
            set_session_name_if_missing=_set_session_name_if_missing,
            print_chat_provider_banner=_print_chat_provider_banner,
            print_stale_session_notice=_print_stale_session_notice,
            emit_session_event_safe=_emit_session_event_safe,
            conversation_env_name=OPENMINION_CONVERSATION_ID_ENV,
        ),
    )


def _execute_turn(
    *,
    runtime_state: ChatRuntimeState,
    args,
    config,
    payload: dict[str, Any],
    inbound_metadata: dict[str, Any],
    line: str,
    agent_id: str,
    session_id: str,
    lifecycle_payload: dict[str, str],
    chat_turn_timeout: float,
    attempt: int,
    chat_turn_max_attempts: int,
) -> dict[str, Any]:
    return chat_runtime.execute_turn(
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
        deps=chat_runtime.TurnExecutionDeps(
            request_daemon_turn=request_daemon_turn,
            ensure_inproc_runtime=ensure_inproc_runtime,
            set_quiet_log_level=set_quiet_log_level,
            request_inproc_turn=request_inproc_turn,
            format_api_error=format_api_error,
            is_retryable_turn_error=is_retryable_turn_error,
            print_fallback_notice=print_fallback_notice,
            print_turn_error=print_turn_error,
            print_assistant_text=print_assistant_text,
            print_turn_usage_summary=chat_runtime.print_turn_usage_summary,
            emit_session_event_safe=_emit_session_event_safe,
            build_run_profile_override_payload=_build_run_profile_override_payload,
        ),
    )


def _wire_runtime_shims() -> None:
    chat_runtime.ensure_daemon_running = ensure_daemon_running
    chat_runtime.daemon_request = daemon_request
    chat_runtime.daemon_stream_request = daemon_stream_request
    chat_runtime.APIRuntime = APIRuntime
    chat_runtime.run_turn = run_turn


def _print_chat_provider_banner(
    config: Any,
    *,
    agent_id: str,
    args: Any | None = None,
) -> None:
    chat_ui.print_chat_provider_banner(
        config,
        agent_id=agent_id,
        args=args,
    )


def _print_chat_ready_banner(
    *,
    runtime_state: ChatRuntimeState,
    agent_id: str,
    session_id: str,
    conversation_selection: dict[str, str],
    conversation_id: str,
    resume_requested: bool,
    reset_requested: bool,
    config: Any,
    args: Any,
) -> None:
    chat_ui.print_chat_ready_banner(
        runtime_state=runtime_state,
        agent_id=agent_id,
        session_id=session_id,
        conversation_selection=conversation_selection,
        conversation_id=conversation_id,
        resume_requested=resume_requested,
        reset_requested=reset_requested,
        config=config,
        args=args,
    )


def _print_first_session_tip_if_requested(args) -> None:
    chat_ui.print_first_session_tip_if_requested(args)


def _print_agent_resolution_notice(
    *,
    session_id: str,
    agent_id: str,
    agent_resolution: dict[str, str],
    reset_requested: bool,
    config_path: str | None = None,
) -> None:
    chat_ui.print_agent_resolution_notice(
        session_id=session_id,
        agent_id=agent_id,
        agent_resolution=agent_resolution,
        reset_requested=reset_requested,
        config_path=config_path,
        session_allows_agent_id_fn=_session_allows_agent_id,
    )


def _session_profile_mismatch_message(
    *,
    session_id: str,
    agent_id: str,
    agent_resolution: dict[str, str],
    reset_requested: bool,
    config_path: str | None = None,
) -> str:
    return chat_ui.session_profile_mismatch_message(
        session_id=session_id,
        agent_id=agent_id,
        agent_resolution=agent_resolution,
        reset_requested=reset_requested,
        config_path=config_path,
        session_allows_agent_id_fn=_session_allows_agent_id,
    )


def _build_run_profile_override_payload(args) -> dict[str, str]:
    return chat_lifecycle.build_run_profile_override_payload(
        args,
        run_profile_overrides_from_mapping_fn=run_profile_overrides_from_mapping,
    )


def _build_inbound_metadata(
    *,
    conversation_id: str,
    thread_id: str,
    attach_id: str,
    resume_requested: bool,
    reset_requested: bool,
    cwd: str | None = None,
    recent_artifacts: list[dict[str, Any]] | None = None,
) -> dict[str, str]:
    return chat_lifecycle.build_inbound_metadata(
        conversation_id=conversation_id,
        thread_id=thread_id,
        attach_id=attach_id,
        resume_requested=resume_requested,
        reset_requested=reset_requested,
        cwd=cwd,
        recent_artifacts=recent_artifacts,
    )


def _session_has_prior_trace_history(
    *, session_id: str, config_path: str | None
) -> bool:
    normalized = str(session_id or "").strip()
    if not normalized:
        return False
    safe_pattern = re.compile(r"[^A-Za-z0-9._-]+")

    def _safe_segment(value: str, fallback: str) -> str:
        token = str(value or "").strip()
        if not token:
            return fallback
        normalized_token = safe_pattern.sub("-", token).strip("-._")
        return normalized_token or fallback

    try:
        roots = resolve_cli_roots(
            config_path=config_path,
            fallback_to_cwd=True,
        )
        trace_root = roots.data_root / "traces"
        agent_token = normalized.split("::", 1)[0] if "::" in normalized else normalized
        session_token = (
            normalized.split("::", 1)[1] if "::" in normalized else normalized
        )
        agent_slug = _safe_segment(agent_token, "agent")
        session_slug = _safe_segment(session_token, "session")
        agent_dir = trace_root / "llm" / agent_slug
        if agent_dir.exists():
            for run_dir in agent_dir.glob(f"*-{session_slug}"):
                if run_dir.is_dir():
                    return True

    except Exception:
        return False
    return False


def run_chat(args) -> int:
    from openminion.cli.chat._deprecation import print_deprecation_notice

    print_deprecation_notice()
    _wire_runtime_shims()
    return chat_runner.run_chat(
        args,
        deps=chat_runner.ChatRunnerDeps(
            resolve_chat_roots=_resolve_chat_roots,
            load_config=load_config,
            inspect_chat_onboarding=_inspect_chat_onboarding,
            print_onboarding_fail_fast=_print_onboarding_fail_fast,
            run_inline_setup_for_chat=_run_inline_setup_for_chat,
            materialize_demo_config_for_chat=_materialize_demo_config_for_chat,
            normalize_chat_args=normalize_chat_args,
            perform_identity_sync=sync_cli_identity_profiles,
            should_suppress_console_info_logs=_should_suppress_console_info_logs,
            set_quiet_log_level=set_quiet_log_level,
            init_runtime_state=init_runtime_state,
            mark_stale_cli_sessions=_mark_stale_cli_sessions,
            resolve_initial_chat_agent_id=_resolve_initial_chat_agent_id,
            resolve_lifecycle_state=_resolve_lifecycle_state,
            session_profile_mismatch_message=_session_profile_mismatch_message,
            print_chat_ready_banner=_print_chat_ready_banner,
            print_agent_resolution_notice=_print_agent_resolution_notice,
            print_stale_session_notice=_print_stale_session_notice,
            print_first_session_tip_if_requested=_print_first_session_tip_if_requested,
            get_session_record=_get_session_record,
            emit_session_open_events=_emit_session_open_events,
            set_session_name_if_missing=_set_session_name_if_missing,
            handle_chat_command=handle_chat_command,
            handle_repl_command=_handle_repl_command,
            local_human_post_block_reason=_local_human_post_block_reason,
            build_lifecycle_payload=_build_lifecycle_payload,
            build_inbound_metadata=_build_inbound_metadata,
            build_turn_idempotency_key=_build_turn_idempotency_key,
            build_run_profile_override_payload=_build_run_profile_override_payload,
            execute_turn=_execute_turn,
            maybe_auto_name_session=_maybe_auto_name_session,
            emit_session_event_safe=_emit_session_event_safe,
            close_runtime=close_runtime,
            chat_input_prompt=chat_input_prompt,
            conversation_env_name=OPENMINION_CONVERSATION_ID_ENV,
            resolve_environment_config=resolve_environment_config,
            stale_timeout_default=_SESSION_STALE_TIMEOUT_SECONDS_DEFAULT,
            turn_timeout_default=_CHAT_TURN_TIMEOUT_SECONDS_DEFAULT,
            turn_max_attempts_default=chat_turn_max_attempts_DEFAULT,
        ),
    )


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    chat = subparsers.add_parser("chat", help="Interactive chat client")
    chat.add_argument(
        "--profile",
        "--agent",
        dest="agent",
        default=None,
        help="Configured profile id (compat: --agent)",
    )
    chat.add_argument(
        "--override-provider",
        default=None,
        help="Run-scoped provider override applied after profile selection",
    )
    chat.add_argument(
        "--override-model",
        default=None,
        help="Run-scoped model override applied after profile selection",
    )
    chat.add_argument(
        "--override-system-prompt",
        default=None,
        help="Run-scoped system prompt override applied after profile selection",
    )
    chat.add_argument("--session", default=None, help="Session id")
    chat.add_argument(
        "--session-name",
        default=None,
        help="Optional display name for the session (applied if currently unset)",
    )
    chat.add_argument(
        "--conversation",
        default=None,
        help="Conversation id (overrides default per-run conversation scope)",
    )
    chat.add_argument(
        "--resume",
        action="store_true",
        help="Force reuse of the latest resolved thread even if settled",
    )
    chat.add_argument(
        "--reset-session",
        action="store_true",
        help="Force creation of a fresh thread for this chat session",
    )
    chat.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress INFO logs in chat mode",
    )
    chat.add_argument(
        "--sync-identity",
        action="store_true",
        help="Refresh YAML-backed identity profiles into SQLite and regenerate generated markdown sidecars before chat starts",
    )
    chat.add_argument(
        "--demo",
        action="store_true",
        help="Run explicit demo mode with the echo provider",
    )
    chat.add_argument(
        "--no-progress",
        action="store_true",
        help="Disable detailed brain/progress status while waiting for responses",
    )
    chat.add_argument(
        "--no-activity-indicator",
        action="store_true",
        help="Suppress the minimal elapsed waiting indicator when --no-progress is used",
    )
    chat.add_argument(
        "--no-interactive",
        action="store_true",
        help="Disable inline first-run setup and fail fast with remediation",
    )
    chat.add_argument(
        "--stdin-one-shot",
        action="store_true",
        help=(
            "When stdin is piped, read it to EOF and send it as one user turn "
            "instead of treating each non-empty line as a separate turn."
        ),
    )
    chat.add_argument(
        "--theme",
        default=None,
        help=(
            "Theme variant override (e.g. light, dark). "
            "Top precedence — beats env and persisted preference."
        ),
    )
    chat.set_defaults(handler=run_chat, needs_app=False)
