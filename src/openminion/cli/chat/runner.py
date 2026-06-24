from __future__ import annotations

try:
    import atexit
    import readline

    def _write_history_file_safely(history_path: str) -> None:
        try:
            readline.write_history_file(history_path)
        except (FileNotFoundError, OSError):
            pass

    def _setup_chat_readline(history_path: str | None = None) -> None:
        if history_path is None:
            return
        try:
            readline.read_history_file(history_path)
        except (FileNotFoundError, OSError):
            pass
        readline.set_history_length(500)
        atexit.register(_write_history_file_safely, history_path)

except ImportError:  # pragma: no cover — readline unavailable on some platforms

    def _setup_chat_readline(history_path: str | None = None) -> None:  # type: ignore[misc]
        pass


try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.filters import Condition
    from prompt_toolkit.formatted_text import ANSI
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.key_binding import KeyBindings
except ImportError:  # pragma: no cover - optional dependency
    _PROMPT_TOOLKIT_AVAILABLE = False
else:
    _PROMPT_TOOLKIT_AVAILABLE = True


from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4
import re
import sys

from openminion.base.config import ConfigError, ConfigManagerError
from openminion.base.config.bootstrap import bootstrap_config_path
from openminion.base.config.env import resolve_environment_config
from openminion.cli.chat.plan_hook import evict_plan_for_session
from openminion.cli.presentation import styles
from openminion.cli.ux.input_normalization import normalize_multiline_input_text


_ANSI_ESCAPE_RE = re.compile(r"(\x1b\[[0-9;]*m)")
_ANSI_ESCAPE_STRIP_RE = re.compile(r"\x1b\[[0-9;]*m")

try:
    readline  # type: ignore[name-defined]
except NameError:
    _READLINE_AVAILABLE = False
else:
    _READLINE_AVAILABLE = True

_READLINE_DOC = (
    str(getattr(readline, "__doc__", "") or "") if _READLINE_AVAILABLE else ""
)
_READLINE_USES_LIBEDIT = "libedit" in _READLINE_DOC.lower()


@dataclass(frozen=True)
class ChatRunnerDeps:
    resolve_chat_roots: Callable[[Any], tuple[Path, object]]
    load_config: Callable[..., Any]
    inspect_chat_onboarding: Callable[[Any], tuple[Any, Path, object]]
    print_onboarding_fail_fast: Callable[[Any], int]
    run_inline_setup_for_chat: Callable[[Any], int]
    materialize_demo_config_for_chat: Callable[..., Path]
    normalize_chat_args: Callable[[Any, Any], Any]
    perform_identity_sync: Callable[..., Any]
    should_suppress_console_info_logs: Callable[..., bool]
    set_quiet_log_level: Callable[[], None]
    init_runtime_state: Callable[[Any, Any], tuple[Any, Exception | None]]
    mark_stale_cli_sessions: Callable[..., int]
    resolve_initial_chat_agent_id: Callable[..., tuple[str, dict[str, str]]]
    resolve_lifecycle_state: Callable[
        ..., tuple[dict[str, str], str, str, str, dict[str, str]]
    ]
    session_profile_mismatch_message: Callable[..., str]
    print_chat_ready_banner: Callable[..., None]
    print_agent_resolution_notice: Callable[..., None]
    print_stale_session_notice: Callable[..., None]
    print_first_session_tip_if_requested: Callable[[Any], None]
    get_session_record: Callable[..., Any]
    emit_session_open_events: Callable[..., None]
    set_session_name_if_missing: Callable[..., bool]
    handle_chat_command: Callable[..., Any]
    handle_repl_command: Callable[..., dict[str, Any]]
    local_human_post_block_reason: Callable[..., str]
    build_lifecycle_payload: Callable[..., dict[str, str]]
    build_inbound_metadata: Callable[..., dict[str, str]]
    build_turn_idempotency_key: Callable[..., str]
    build_run_profile_override_payload: Callable[[Any], dict[str, str]]
    execute_turn: Callable[..., dict[str, Any]]
    maybe_auto_name_session: Callable[..., bool]
    emit_session_event_safe: Callable[..., None]
    close_runtime: Callable[[Any], None]
    chat_input_prompt: Callable[..., str]
    conversation_env_name: str
    resolve_environment_config: Callable[[], dict[str, Any]]
    stale_timeout_default: int
    turn_timeout_default: float
    turn_max_attempts_default: int


def _split_input_prompt_for_readline(prompt: str) -> tuple[str, str]:
    if not _READLINE_AVAILABLE or "\x1b[" not in prompt:
        return "", prompt
    if _READLINE_USES_LIBEDIT:
        return "", _ANSI_ESCAPE_STRIP_RE.sub("", prompt)
    wrapped = _ANSI_ESCAPE_RE.sub(lambda match: f"\001{match.group(1)}\002", prompt)
    return "", wrapped


def _read_noninteractive_chat_stdin(*, one_shot: bool, consumed: bool) -> str | None:
    if one_shot:
        if consumed:
            return None
        raw = sys.stdin.read()
        if raw == "":
            return None
        text = raw.strip()
        return text or None
    while True:
        raw = sys.stdin.readline()
        if raw == "":
            return None
        line = raw.strip()
        if line:
            return line


class _PromptToolkitInteractiveChatReader:
    def __init__(self, history_path: str | None = None) -> None:
        self._multiline = False
        kb = KeyBindings()

        @kb.add("c-j")
        def _newline(event) -> None:
            self._insert_newline(event)

        @kb.add("enter")
        def _submit(event) -> None:
            event.current_buffer.validate_and_handle()

        @kb.add("c-l")
        def _toggle(_event) -> None:
            self.toggle_multiline()

        @kb.add("<bracketed-paste>")
        def _paste(event) -> None:
            self._handle_bracketed_paste(event)

        history = FileHistory(history_path) if history_path else None
        self._session = PromptSession(
            history=history,
            key_bindings=kb,
            enable_history_search=True,
        )

    def toggle_multiline(self) -> None:
        self._multiline = not self._multiline

    def _insert_newline(self, event) -> None:
        if not self._multiline:
            self._multiline = True
        event.app.current_buffer.insert_text("\n")

    def _insert_pasted_text(self, text: str, *, buffer) -> None:
        text = normalize_multiline_input_text(text)
        if not text:
            return
        if "\n" in text and not self._multiline:
            self._multiline = True
        buffer.insert_text(text)

    def _handle_bracketed_paste(self, event) -> None:
        self._insert_pasted_text(
            str(getattr(event, "data", "") or ""),
            buffer=event.app.current_buffer,
        )

    def _format_prompt(self, prompt: str):
        if "\x1b[" in prompt:
            return ANSI(prompt)
        return prompt

    def read_line(self, prompt: str) -> str | None:
        try:
            text = self._session.prompt(
                self._format_prompt(prompt),
                multiline=Condition(lambda: self._multiline),
            )
        except (EOFError, KeyboardInterrupt):
            return None
        finally:
            self._multiline = False
        return str(text or "").strip()


def _build_prompt_toolkit_chat_reader(
    history_path: str | None = None,
) -> _PromptToolkitInteractiveChatReader | None:
    if not _PROMPT_TOOLKIT_AVAILABLE:
        return None
    raw_term = str(resolve_environment_config().get("TERM", "") or "").strip().lower()
    if raw_term in {"", "dumb"}:
        return None
    try:
        return _PromptToolkitInteractiveChatReader(history_path=history_path)
    except (OSError, RuntimeError, ValueError):
        return None


def _apply_chat_theme(args: Any, roots: Any) -> None:
    try:
        from openminion.cli.theme import resolve_theme

        cli_theme = str(getattr(args, "theme", "") or "").strip() or None
        data_root = getattr(roots, "data_root", None)
        resolved = resolve_theme(
            cli_flag=cli_theme,
            data_root=Path(str(data_root)) if data_root is not None else None,
        )
        styles.set_active_theme(resolved)
    except (OSError, RuntimeError, TypeError, ValueError):
        pass


def _load_chat_config_with_roots(
    deps: ChatRunnerDeps, *, config_path: Path, roots: Any
) -> Any:
    return deps.load_config(
        str(config_path),
        home_root=roots.home_root,
        data_root=roots.data_root,
    )


def _apply_chat_onboarding_recovery(
    args: Any,
    deps: ChatRunnerDeps,
    *,
    roots: Any,
    resolved_config_path: Path,
    effective_config_path: Path,
) -> tuple[Any | None, Path, int | None]:
    status, _, _ = deps.inspect_chat_onboarding(args)
    if status.action.value == "fail_fast":
        return None, effective_config_path, deps.print_onboarding_fail_fast(status)
    if status.action.value == "launch_setup":
        setup_code = deps.run_inline_setup_for_chat(args)
        if setup_code != 0:
            return None, effective_config_path, setup_code
        config = _load_chat_config_with_roots(
            deps, config_path=effective_config_path, roots=roots
        )
        return config, effective_config_path, None
    if status.state.value == "explicit_demo":
        effective_config_path = deps.materialize_demo_config_for_chat(
            args, roots=roots, config_path=resolved_config_path
        )
        config = _load_chat_config_with_roots(
            deps, config_path=effective_config_path, roots=roots
        )
        return config, effective_config_path, None
    return None, effective_config_path, None


def _resolve_chat_config_with_onboarding(
    args: Any,
    deps: ChatRunnerDeps,
    *,
    roots: Any,
    resolved_config_path: Path,
) -> tuple[Any, Path, int | None]:
    effective_config_path = resolved_config_path

    if bool(getattr(args, "demo", False)):
        effective_config_path = deps.materialize_demo_config_for_chat(
            args, roots=roots, config_path=resolved_config_path
        )
        config = _load_chat_config_with_roots(
            deps, config_path=effective_config_path, roots=roots
        )
    else:
        try:
            config = _load_chat_config_with_roots(
                deps, config_path=effective_config_path, roots=roots
            )
        except (ConfigError, ConfigManagerError):
            recovered_config, effective_config_path, exit_code = (
                _apply_chat_onboarding_recovery(
                    args,
                    deps,
                    roots=roots,
                    resolved_config_path=resolved_config_path,
                    effective_config_path=effective_config_path,
                )
            )
            if exit_code is not None:
                return None, effective_config_path, exit_code
            if recovered_config is None:
                raise
            config = recovered_config

    if resolved_config_path.exists():
        recovered_config, effective_config_path, exit_code = (
            _apply_chat_onboarding_recovery(
                args,
                deps,
                roots=roots,
                resolved_config_path=resolved_config_path,
                effective_config_path=effective_config_path,
            )
        )
        if exit_code is not None:
            return config, effective_config_path, exit_code
        if recovered_config is not None:
            config = recovered_config

    return config, effective_config_path, None


def _emit_chat_ready_notices(
    args: Any,
    deps: ChatRunnerDeps,
    *,
    config: Any,
    runtime_state: Any,
    agent_id: str,
    agent_resolution: dict,
    session_id: str,
    conversation_selection: dict,
    conversation_id: str,
    resume_requested: bool,
    reset_requested: bool,
    lifecycle_payload: dict,
    chat_args: Any,
) -> None:
    deps.print_chat_ready_banner(
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
    deps.print_agent_resolution_notice(
        session_id=session_id,
        agent_id=agent_id,
        agent_resolution=agent_resolution,
        reset_requested=reset_requested,
        config_path=getattr(args, "config", None),
    )
    deps.print_stale_session_notice(
        session_id=session_id,
        config_path=getattr(args, "config", None),
        reset_requested=reset_requested,
    )
    deps.print_first_session_tip_if_requested(args)
    session_existed = (
        deps.get_session_record(
            session_id=session_id,
            config_path=getattr(args, "config", None),
        )
        is not None
    )
    deps.emit_session_open_events(
        state=runtime_state,
        config_path=args.config,
        session_id=session_id,
        lifecycle_payload=lifecycle_payload,
        agent_id=agent_id,
        previously_existed=session_existed,
    )
    deps.set_session_name_if_missing(
        session_id=session_id,
        config_path=getattr(args, "config", None),
        name=chat_args.session_name,
    )


def run_chat(args: Any, *, deps: ChatRunnerDeps) -> int:
    resolved_config_path, roots = deps.resolve_chat_roots(args)

    _apply_chat_theme(args, roots)
    config, effective_config_path, config_exit_code = (
        _resolve_chat_config_with_onboarding(
            args, deps, roots=roots, resolved_config_path=resolved_config_path
        )
    )
    if config_exit_code is not None:
        return config_exit_code

    bootstrap_config_path(effective_config_path)
    deps.perform_identity_sync(
        enabled=bool(getattr(args, "sync_identity", False)),
        config=config,
        roots=roots,
    )
    chat_args = deps.normalize_chat_args(args, config)
    suppress_console_info_logs = deps.should_suppress_console_info_logs(
        chat_args=chat_args
    )
    if suppress_console_info_logs:
        deps.set_quiet_log_level()

    stale_timeout_seconds = int(
        getattr(getattr(config, "runtime", None), "session_stale_timeout_seconds", 0)
        or deps.stale_timeout_default
    )
    deps.mark_stale_cli_sessions(
        config_path=getattr(args, "config", None),
        timeout_seconds=stale_timeout_seconds,
    )

    runtime_state, runtime_error = deps.init_runtime_state(args, config)
    if suppress_console_info_logs:
        runtime_state.quiet = True
    if runtime_error is not None:
        from openminion.cli.chat.ui import print_fallback_notice

        print_fallback_notice(runtime_error)

    session_id = chat_args.session_id
    agent_id, agent_resolution = deps.resolve_initial_chat_agent_id(
        args,
        config=config,
        session_id=session_id,
    )
    (
        conversation_selection,
        conversation_id,
        thread_id,
        attach_id,
        lifecycle_payload,
    ) = deps.resolve_lifecycle_state(
        args,
        session_id=session_id,
        config_path=getattr(args, "config", None),
    )
    conversation_id_fixed = bool(
        str(getattr(args, "conversation", "") or "").strip()
        or deps.resolve_environment_config().get(deps.conversation_env_name, "").strip()
    )
    reset_requested = bool(getattr(args, "reset_session", False))
    resume_requested = not reset_requested and (
        bool(getattr(args, "resume", False))
        or conversation_selection["source"] == "session_reuse"
    )
    last_artifacts: list[dict[str, Any]] = []
    last_turn_debug: dict[str, Any] = {}
    chat_cwd = str(Path.cwd().resolve(strict=False))
    chat_turn_timeout = float(
        getattr(getattr(config, "runtime", None), "chat_turn_timeout_seconds", None)
        or deps.turn_timeout_default
    )
    chat_turn_max_attempts = int(
        getattr(getattr(config, "runtime", None), "chat_turn_max_attempts", None)
        or deps.turn_max_attempts_default
    )
    mismatch_message = deps.session_profile_mismatch_message(
        session_id=session_id,
        agent_id=agent_id,
        agent_resolution=agent_resolution,
        reset_requested=reset_requested,
        config_path=getattr(args, "config", None),
    )
    if mismatch_message:
        print(styles.style(styles.StyleToken.WARNING, mismatch_message))
        return 2

    _emit_chat_ready_notices(
        args,
        deps,
        config=config,
        runtime_state=runtime_state,
        agent_id=agent_id,
        agent_resolution=agent_resolution,
        session_id=session_id,
        conversation_selection=conversation_selection,
        conversation_id=conversation_id,
        resume_requested=resume_requested,
        reset_requested=reset_requested,
        lifecycle_payload=lifecycle_payload,
        chat_args=chat_args,
    )
    _history_dir = Path(str(roots.data_root)) / "cli"
    _history_dir.mkdir(parents=True, exist_ok=True)
    history_path = str(_history_dir / "chat_history")
    _setup_chat_readline(history_path)
    stdin_is_tty = bool(getattr(sys.stdin, "isatty", lambda: False)())
    stdin_one_shot = bool(getattr(args, "stdin_one_shot", False)) and not stdin_is_tty
    # Non-TTY stdin must stay on the stream-reader path even when stdout is
    interactive_stdin = stdin_is_tty
    interactive_reader = (
        _build_prompt_toolkit_chat_reader(history_path) if interactive_stdin else None
    )
    noninteractive_input_consumed = False

    def _next_chat_line() -> str | None:
        def _read_interactive_line() -> str | None:
            prompt = deps.chat_input_prompt(session_id=session_id, agent_id=agent_id)
            if interactive_reader is not None:
                return interactive_reader.read_line(prompt)
            try:
                display_prompt, input_prompt = _split_input_prompt_for_readline(prompt)
                if display_prompt and not input_prompt:
                    sys.stdout.write(display_prompt)
                    sys.stdout.flush()
                return input(input_prompt).strip()
            except (EOFError, KeyboardInterrupt):
                return None

        if interactive_stdin:
            return _read_interactive_line()
        try:
            return _read_noninteractive_chat_stdin(
                one_shot=stdin_one_shot,
                consumed=noninteractive_input_consumed,
            )
        except OSError:
            # Pytest capture and similar harnesses can install a
            return _read_interactive_line()

    try:
        exit_clean = False
        while True:
            line = _next_chat_line()
            if stdin_one_shot:
                noninteractive_input_consumed = True
            if line is None:
                exit_clean = True
                if interactive_stdin:
                    print()
                break

            if not line:
                continue

            command_result = deps.handle_chat_command(
                line=line,
                args=args,
                config=config,
                agent_id=agent_id,
                session_id=session_id,
                transport=runtime_state.transport,
                mode=runtime_state.mode,
                runtime_state=runtime_state,
                last_artifacts=last_artifacts,
                last_turn_debug=last_turn_debug,
            )
            if command_result.handled:
                command_update = deps.handle_repl_command(
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
                )
                if command_update["exit_clean"]:
                    exit_clean = True
                    break
                agent_id = command_update["agent_id"]
                session_id = command_update["session_id"]
                conversation_selection = command_update["conversation_selection"]
                conversation_id = command_update["conversation_id"]
                thread_id = command_update["thread_id"]
                attach_id = command_update["attach_id"]
                lifecycle_payload = command_update["lifecycle_payload"]
                resume_requested = command_update["resume_requested"]
                reset_requested = command_update["reset_requested"]
                if stdin_one_shot:
                    exit_clean = True
                    break
                continue

            observer_block = deps.local_human_post_block_reason(
                session_id=session_id,
                config_path=getattr(args, "config", None),
            )
            if observer_block:
                print(styles.style(styles.StyleToken.ERROR, observer_block))
                if stdin_one_shot:
                    exit_clean = True
                    break
                continue

            inbound_metadata = deps.build_inbound_metadata(
                conversation_id=conversation_id,
                thread_id=thread_id,
                attach_id=attach_id,
                resume_requested=resume_requested,
                reset_requested=reset_requested,
                cwd=chat_cwd,
                recent_artifacts=last_artifacts,
            )
            turn_nonce = uuid4().hex
            turn_idempotency_key = deps.build_turn_idempotency_key(
                agent_id=agent_id,
                session_id=session_id,
                conversation_id=conversation_id,
                thread_id=thread_id,
                turn_nonce=turn_nonce,
            )
            payload = {
                "message": line,
                "input_text": line,
                "agent_id": agent_id,
                "session_id": session_id,
                "channel": "console",
                "target": "cli-chat",
                "stream": True,
                "deliver": False,
                "meta": inbound_metadata,
                "idempotency_key": turn_idempotency_key,
                "timeout_seconds": chat_turn_timeout,
            }
            payload.update(deps.build_run_profile_override_payload(args))

            for attempt in range(1, chat_turn_max_attempts + 1):
                turn_result = deps.execute_turn(
                    runtime_state=runtime_state,
                    args=args,
                    config=config,
                    payload=payload,
                    inbound_metadata=inbound_metadata,
                    line=line,
                    agent_id=agent_id,
                    session_id=session_id,
                    lifecycle_payload=lifecycle_payload,
                    chat_turn_timeout=chat_turn_timeout,
                    attempt=attempt,
                    chat_turn_max_attempts=chat_turn_max_attempts,
                )
                if turn_result.get("retry"):
                    error_message = str(turn_result.get("error", "")).strip()
                    if error_message:
                        from openminion.cli.chat.ui import print_retry_notice

                        print_retry_notice(
                            attempt=attempt,
                            max_attempts=chat_turn_max_attempts,
                            error=error_message,
                        )
                    continue
                artifacts = turn_result.get("last_artifacts")
                if isinstance(artifacts, list):
                    last_artifacts = artifacts
                debug_payload = turn_result.get("last_turn_debug")
                if isinstance(debug_payload, dict):
                    last_turn_debug = debug_payload
                    metadata = debug_payload.get("metadata")
                    if isinstance(metadata, dict):
                        resolved_conversation_id = str(
                            metadata.get("conversation_id", "") or ""
                        ).strip()
                        resolved_thread_id = str(
                            metadata.get("thread_id", "") or ""
                        ).strip()
                        if (
                            resolved_conversation_id
                            and not conversation_id_fixed
                            and resolved_conversation_id != conversation_id
                        ):
                            conversation_id = resolved_conversation_id
                        if resolved_thread_id and resolved_thread_id != thread_id:
                            thread_id = resolved_thread_id
                        if resolved_conversation_id or resolved_thread_id:
                            lifecycle_payload = deps.build_lifecycle_payload(
                                conversation_id=conversation_id,
                                thread_id=thread_id,
                                attach_id=attach_id,
                            )
                if turn_result.get("stop"):
                    break
                deps.maybe_auto_name_session(
                    session_id=session_id,
                    config_path=getattr(args, "config", None),
                    first_user_text=line,
                )
                break
            if stdin_one_shot:
                exit_clean = True
                break
    finally:
        deps.emit_session_event_safe(
            state=runtime_state,
            config_path=args.config,
            session_id=session_id,
            event_type="client.exit_clean" if exit_clean else "client.detached",
            lifecycle_payload=lifecycle_payload,
        )
        if exit_clean:
            deps.emit_session_event_safe(
                state=runtime_state,
                config_path=args.config,
                session_id=session_id,
                event_type="client.detached",
                lifecycle_payload=lifecycle_payload,
            )
        try:
            evict_plan_for_session(session_id)
        except Exception:
            pass
        deps.close_runtime(runtime_state)

    return 0
