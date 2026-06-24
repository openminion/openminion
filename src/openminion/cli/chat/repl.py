from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
from uuid import uuid4

from openminion.cli.presentation import styles


@dataclass(frozen=True)
class ReplCommandDeps:
    close_session_record: Callable[..., bool]
    get_session_record: Callable[..., Any]
    resolve_lifecycle_state: Callable[
        ..., tuple[dict[str, str], str, str, str, dict[str, str]]
    ]
    build_lifecycle_payload: Callable[..., dict[str, str]]
    ensure_cli_session_record: Callable[..., bool]
    emit_session_open_events: Callable[..., None]
    set_session_name_if_missing: Callable[..., bool]
    print_chat_provider_banner: Callable[..., None]
    print_stale_session_notice: Callable[..., None]
    emit_session_event_safe: Callable[..., None]
    conversation_env_name: str


def _refresh_lifecycle_on_session_change(
    *,
    updated: dict[str, Any],
    args: Any,
    deps: ReplCommandDeps,
    conversation_id_fixed: bool,
    target_session_id: str,
    force_fresh: bool,
) -> None:
    if not conversation_id_fixed:
        (
            updated["conversation_selection"],
            updated["conversation_id"],
            updated["thread_id"],
            updated["attach_id"],
            updated["lifecycle_payload"],
        ) = deps.resolve_lifecycle_state(
            args,
            session_id=target_session_id,
            config_path=getattr(args, "config", None),
            force_fresh=force_fresh,
        )
    else:
        updated["attach_id"] = f"att-{uuid4().hex}"
        updated["lifecycle_payload"] = deps.build_lifecycle_payload(
            conversation_id=updated["conversation_id"],
            thread_id=updated["thread_id"],
            attach_id=updated["attach_id"],
        )


def _apply_agent_rotation(
    *,
    updated: dict[str, Any],
    args: Any,
    config: Any,
    runtime_state: Any,
    deps: ReplCommandDeps,
    conversation_id_fixed: bool,
) -> dict[str, Any]:
    old_session_id = updated["session_id"]
    deps.close_session_record(
        session_id=old_session_id,
        config_path=getattr(args, "config", None),
        reason="agent_switch",
    )
    updated["session_id"] = f"sess-{uuid4().hex}"
    _refresh_lifecycle_on_session_change(
        updated=updated,
        args=args,
        deps=deps,
        conversation_id_fixed=conversation_id_fixed,
        target_session_id=updated["session_id"],
        force_fresh=True,
    )
    updated["resume_requested"] = False
    updated["reset_requested"] = False
    deps.ensure_cli_session_record(
        session_id=updated["session_id"],
        agent_id=updated["agent_id"],
        config_path=getattr(args, "config", None),
    )
    deps.emit_session_open_events(
        state=runtime_state,
        config_path=getattr(args, "config", None),
        session_id=updated["session_id"],
        lifecycle_payload=updated["lifecycle_payload"],
        agent_id=updated["agent_id"],
        previously_existed=False,
    )
    deps.set_session_name_if_missing(
        session_id=updated["session_id"],
        config_path=getattr(args, "config", None),
        name=str(getattr(args, "session_name", "") or ""),
    )
    deps.print_chat_provider_banner(
        config,
        agent_id=updated["agent_id"],
        args=args,
    )
    print(
        styles.style(
            styles.StyleToken.SUCCESS,
            f"Switched to agent {updated['agent_id']} "
            f"(new session {updated['session_id'][:12]})",
        )
    )
    return updated


def _apply_explicit_session_change(
    *,
    updated: dict[str, Any],
    command_result: Any,
    args: Any,
    runtime_state: Any,
    deps: ReplCommandDeps,
    conversation_id_fixed: bool,
) -> None:
    previous_session = deps.get_session_record(
        session_id=command_result.session_id,
        config_path=getattr(args, "config", None),
    )
    updated["session_id"] = command_result.session_id
    _refresh_lifecycle_on_session_change(
        updated=updated,
        args=args,
        deps=deps,
        conversation_id_fixed=conversation_id_fixed,
        target_session_id=command_result.session_id,
        force_fresh=False,
    )
    deps.emit_session_open_events(
        state=runtime_state,
        config_path=getattr(args, "config", None),
        session_id=updated["session_id"],
        lifecycle_payload=updated["lifecycle_payload"],
        agent_id=updated["agent_id"],
        previously_existed=previous_session is not None,
    )
    deps.set_session_name_if_missing(
        session_id=updated["session_id"],
        config_path=getattr(args, "config", None),
        name=str(getattr(args, "session_name", "") or ""),
    )
    deps.print_stale_session_notice(
        session_id=updated["session_id"],
        config_path=getattr(args, "config", None),
        reset_requested=False,
    )


def _apply_new_conversation(
    *,
    updated: dict[str, Any],
    args: Any,
    runtime_state: Any,
    deps: ReplCommandDeps,
    conversation_id_fixed: bool,
) -> None:
    if conversation_id_fixed:
        print(
            styles.style(
                styles.StyleToken.WARNING,
                "[chat] /new ignored because conversation id is fixed "
                f"(--conversation or {deps.conversation_env_name}).",
            )
        )
        return
    (
        updated["conversation_selection"],
        updated["conversation_id"],
        updated["thread_id"],
        updated["attach_id"],
        updated["lifecycle_payload"],
    ) = deps.resolve_lifecycle_state(
        args,
        session_id=updated["session_id"],
        config_path=getattr(args, "config", None),
        force_fresh=True,
    )
    updated["resume_requested"] = False
    updated["reset_requested"] = False
    deps.emit_session_event_safe(
        state=runtime_state,
        config_path=getattr(args, "config", None),
        session_id=updated["session_id"],
        event_type="client.attach",
        lifecycle_payload=updated["lifecycle_payload"],
        extra_payload={"selected_profile_id": updated["agent_id"]},
    )
    print(
        styles.style(
            styles.StyleToken.SUCCESS,
            f"conversation={updated['conversation_id']}",
        )
    )


def _apply_new_session(
    *,
    updated: dict[str, Any],
    args: Any,
    runtime_state: Any,
    deps: ReplCommandDeps,
    conversation_id_fixed: bool,
) -> None:
    old_session_id = updated["session_id"]
    deps.close_session_record(
        session_id=old_session_id,
        config_path=getattr(args, "config", None),
        reason="new_session",
    )
    updated["session_id"] = f"sess-{uuid4().hex}"
    _refresh_lifecycle_on_session_change(
        updated=updated,
        args=args,
        deps=deps,
        conversation_id_fixed=conversation_id_fixed,
        target_session_id=updated["session_id"],
        force_fresh=True,
    )
    updated["resume_requested"] = False
    updated["reset_requested"] = False
    deps.ensure_cli_session_record(
        session_id=updated["session_id"],
        agent_id=updated["agent_id"],
        config_path=getattr(args, "config", None),
    )
    deps.emit_session_open_events(
        state=runtime_state,
        config_path=getattr(args, "config", None),
        session_id=updated["session_id"],
        lifecycle_payload=updated["lifecycle_payload"],
        agent_id=updated["agent_id"],
        previously_existed=False,
    )
    deps.set_session_name_if_missing(
        session_id=updated["session_id"],
        config_path=getattr(args, "config", None),
        name=str(getattr(args, "session_name", "") or ""),
    )
    print(
        styles.style(
            styles.StyleToken.SUCCESS,
            f"session={updated['session_id']}",
        )
    )


def handle_repl_command(
    *,
    command_result: Any,
    args: Any,
    config: Any,
    runtime_state: Any,
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
    deps: ReplCommandDeps,
) -> dict[str, Any]:
    updated: dict[str, Any] = {
        "handled": command_result.handled,
        "exit_clean": False,
        "agent_id": agent_id,
        "session_id": session_id,
        "conversation_selection": conversation_selection,
        "conversation_id": conversation_id,
        "thread_id": thread_id,
        "attach_id": attach_id,
        "lifecycle_payload": lifecycle_payload,
        "resume_requested": resume_requested,
        "reset_requested": reset_requested,
    }
    if not command_result.handled:
        return updated
    if command_result.exit:
        updated["exit_clean"] = True
        return updated
    agent_changed = (
        command_result.agent_id is not None and command_result.agent_id != agent_id
    )
    if command_result.agent_id is not None:
        updated["agent_id"] = command_result.agent_id
    if command_result.rotate_session_on_agent_change and agent_changed:
        return _apply_agent_rotation(
            updated=updated,
            args=args,
            config=config,
            runtime_state=runtime_state,
            deps=deps,
            conversation_id_fixed=conversation_id_fixed,
        )
    if command_result.agent_id is not None:
        deps.print_chat_provider_banner(
            config,
            agent_id=command_result.agent_id,
            args=args,
        )
    if command_result.session_id is not None:
        _apply_explicit_session_change(
            updated=updated,
            command_result=command_result,
            args=args,
            runtime_state=runtime_state,
            deps=deps,
            conversation_id_fixed=conversation_id_fixed,
        )
    if command_result.new_conversation:
        _apply_new_conversation(
            updated=updated,
            args=args,
            runtime_state=runtime_state,
            deps=deps,
            conversation_id_fixed=conversation_id_fixed,
        )
    if command_result.new_session:
        _apply_new_session(
            updated=updated,
            args=args,
            runtime_state=runtime_state,
            deps=deps,
            conversation_id_fixed=conversation_id_fixed,
        )
    return updated
