from __future__ import annotations

import json
import shutil
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from openminion.base.config import (
    build_runtime_config,
    run_profile_overrides_from_mapping,
)
from openminion.base.config.base import UnknownProfileError
from openminion.base.config.core import resolve_default_agent_id
from openminion.base.config.env import resolve_environment_config
from openminion.base.constants import OPENMINION_COLOR_ENV
from openminion.base.logging import apply_logging_mode
from openminion.cli.presentation import styles
from openminion.cli.constants import (
    CLI_COLOR_PROMPT_TRUE_VALUES,
    OPENMINION_COLOR_PROMPT_ENV,
)
from openminion.cli.status import (
    PhaseStatusController,
    format_elapsed_time as _shared_format_elapsed_time,
    format_primary_status_text,
)
from openminion.cli.chat.theme import handle_theme as _handle_theme


def chat_help_lines() -> list[str]:
    return [
        "Chat commands:",
        "  / or /help or /?   show this help",
        "  /status            show active chat status",
        "  /stats             show compact session totals",
        "  /clear             clear terminal screen",
        "  /agent <id>        switch active agent id",
        "  /invite <id>       add an agent participant to the current room",
        "  /activate <id>     switch the room's active agent",
        "  /participants      list room participants",
        "  /kick <id>         remove a room participant",
        "  /join <id>         join the room as a human participant",
        "  /session <id>      switch active session id",
        "  /new               start a new conversation in current session",
        "  /new session       start a fresh session and close the current one",
        "  /sessions          list recent sessions",
        "  /tools             list available tools (CLI: `status capabilities` for full posture)",
        "  /artifacts         print last turn artifact refs",
        "  /debug             print context/llm/tool debug snapshot",
        "  /sidecar status    show sidecar status",
        "  /sidecar start     start a sidecar",
        "  /sidecar stop      stop a sidecar",
        "  /sidecar approve   persist sidecar consent",
        "  /sidecar deny      revoke sidecar consent",
        "  /trust <category>  grant session trust for a tool category",
        "  /untrust <category> revoke session trust for a category",
        "  /grants            list active session trust grants",
        "  /policy action [m] show or set session action policy (ask|auto|bypass)",
        "  /theme             show color/theme display settings",
        "  /agent inspect     inspect agent runtime state and skills",
        "  /inspect           alias for /agent inspect",
        "  /identity help     show identity management commands",
        "  /identity list     list identity profiles",
        "  /identity show     show active identity profile",
        "  /identity render   render active identity snippet",
        "  /skill ingest <p>  ingest a SKILL.md file",
        "  /skill catalog     list ingested skills",
        "  /skill list        show effective session skills",
        "  /skill load <id>   load a catalog skill for this session",
        "  /skill unload <id> unload a session skill",
        "  /skill auto        enable auto-selection for this session",
        "  /skill clear       clear session skill overrides",
        "  /exit              quit chat",
    ]


def print_chat_help() -> None:
    print("\n".join(chat_help_lines()))


def print_grouped_menu(*, config) -> None:
    pairing_enabled = getattr(
        getattr(config, "runtime", object()), "menu_pairing_enabled", True
    )

    lines = [
        "=== SESSION ===",
        "  /session <id>    switch session",
        "  /new             start new conversation",
        "  /new session     start fresh session",
        "  /sessions        list recent sessions",
        "  /status          show chat status",
        "  /stats           show compact session totals",
        "",
        "=== AGENT ===",
        "  /agent <id>      switch agent",
        "  /invite <id>     add an agent participant",
        "  /activate <id>   switch the active room agent",
        "  /participants    list room participants",
        "  /kick <id>       remove a room participant",
        "  /join <id>       join as a human participant",
        "  /agent inspect   inspect agent runtime state and skills",
        "  /inspect         alias for /agent inspect",
        "  /identity help   show identity command help",
        "  /identity show   show active identity profile",
        "",
        "=== TOOLS & DEBUG ===",
        "  /tools           list available tools (`status capabilities` shows full runtime posture)",
        "  /artifacts        print last turn artifact refs",
        "  /debug            print context/llm/tool debug snapshot",
        "  /sidecar status   show sidecar status",
        "  /trust <cat>      grant session trust for a category",
        "  /untrust <cat>    revoke session trust for a category",
        "  /grants           list active session grants",
        "  /policy action    show or set session action policy",
    ]

    if pairing_enabled:
        lines.extend(
            [
                "",
                "=== PAIRING ===",
                "  /pair status       show pairing state",
                "  /pair create       create pairing token (Telegram)",
                "  /pair revoke       revoke pairing token",
            ]
        )
    else:
        lines.extend(
            [
                "",
                "=== PAIRING ===",
                "  (pairing disabled via config)",
            ]
        )

    lines.extend(
        [
            "",
            "=== CONTROL ===",
            "  /clear            clear terminal",
            "  /menu             show this menu",
            "  /exit             quit chat",
        ]
    )

    print("\n".join(lines))


def print_status(
    *, agent_id: str, session_id: str, transport: str, mode: str, config
) -> None:
    agent_runtime_mode = getattr(config, "agent_runtime_mode", "brain")
    brain_integration_mode = getattr(
        config.gateway,
        "brain_integration_mode",
        "contextctl_authoritative",
    )
    context_source = "contextctl"
    llm_bridge_source = getattr(config, "llm_bridge_source", "openminion.modules.llm")
    print(
        json.dumps(
            {
                "agent": agent_id,
                "session": session_id,
                "transport": transport,
                "mode": mode,
                "agent_runtime_mode": agent_runtime_mode,
                "brain_integration_mode": brain_integration_mode,
                "context_source": context_source,
                "llm_bridge_source": llm_bridge_source,
            }
        )
    )


def print_tools_from_payload(tools: list, *, verbose: bool = False) -> None:
    if not tools:
        print("(no tools available)")
        return

    tool_infos = []
    for item in tools:
        if isinstance(item, dict):
            tool_infos.append(
                {
                    "name": item.get("name", "unknown"),
                    "description": item.get("description", ""),
                    "source": item.get("source", "core"),
                    "enabled": item.get("enabled", True),
                    "schema": item.get("schema", {}),
                    "runtime_binding_id": item.get("runtime_binding_id", ""),
                    "runtime_tool_name": item.get("runtime_tool_name", ""),
                }
            )
        elif hasattr(item, "name"):
            tool_infos.append(
                {
                    "name": item.name,
                    "description": getattr(item, "description", ""),
                    "source": getattr(item, "source", "core"),
                    "enabled": True,
                    "schema": {},
                    "runtime_binding_id": "",
                    "runtime_tool_name": "",
                }
            )

    tool_infos.sort(key=lambda x: (x["source"], x["name"]))

    print(f"Available tools ({len(tool_infos)}):")

    if verbose:
        by_source: dict[str, list] = {}
        for t in tool_infos:
            src = t["source"] or "core"
            by_source.setdefault(src, []).append(t)

        for source, items in by_source.items():
            print(f"\n[{source.upper()}]")
            for t in items:
                status = "✓" if t["enabled"] else "✗"
                print(f"  {status} {t['name']}")
                if t["description"]:
                    desc = t["description"]
                    if len(desc) > 60:
                        desc = desc[:57] + "..."
                    print(f"      {desc}")
                runtime_tool = str(t.get("runtime_tool_name", "") or "").strip()
                runtime_binding = str(t.get("runtime_binding_id", "") or "").strip()
                if runtime_tool or runtime_binding:
                    print(
                        f"      -> runtime: {runtime_tool or '(unresolved)'}"
                        + (f" [{runtime_binding}]" if runtime_binding else "")
                    )
    else:
        names = [t["name"] for t in tool_infos]
        print("\n".join(f"  {name}" for name in names))


def chat_input_prompt(*, session_id: str, agent_id: str) -> str:
    context_plain = f"[{session_id}|{agent_id}]"
    prompt_env = (
        resolve_environment_config()
        .get(
            OPENMINION_COLOR_PROMPT_ENV,
            "",
        )
        .strip()
        .lower()
    )
    color_enabled = _terminal_supports_color()
    if prompt_env in {"0", "false", "off", "no"}:
        color_enabled = False
    elif prompt_env in CLI_COLOR_PROMPT_TRUE_VALUES:
        color_enabled = True
    if color_enabled:
        context_styled = _styled_context(session_id=session_id, agent_id=agent_id)
        return f"{context_styled} {_ansi('you', '1;34')}{_ansi('>', '2')} "
    return f"{context_plain} you> "


def print_assistant_text(*, text: str, session_id: str, agent_id: str) -> None:
    sender, content = _split_sender_and_content(text, default_sender=agent_id)
    prefix_plain = f"[{session_id}|{agent_id}] {sender}:"
    color_enabled = _terminal_supports_color()
    if color_enabled:
        prefix = (
            f"{_styled_context(session_id=session_id, agent_id=agent_id)} "
            f"{_ansi(sender, _sender_style(sender))}"
            f"{_ansi(':', '2')}"
        )
    else:
        prefix = prefix_plain

    lines = str(content or "").splitlines() or [""]
    first_line = (
        styles.style(styles.StyleToken.ASSISTANT, lines[0])
        if color_enabled
        else lines[0]
    )
    print(f"{prefix} {first_line}".rstrip())
    continuation = " " * (len(prefix_plain) + 1)
    for line in lines[1:]:
        rendered = (
            styles.style(styles.StyleToken.ASSISTANT, line) if color_enabled else line
        )
        print(f"{continuation}{rendered}")


def clear_screen() -> None:
    if sys.stdout.isatty():
        sys.stdout.write("\033[2J\033[H")
        sys.stdout.flush()


def handle_theme(
    *,
    line: str = "/theme",
    data_root: Any = None,
    theme_applier: Callable[[Any], bool] | None = None,
    active_theme_name_getter: Callable[[], str] | None = None,
) -> None:
    _handle_theme(
        line=line,
        data_root=data_root,
        theme_applier=theme_applier,
        active_theme_name_getter=active_theme_name_getter,
    )


def print_fallback_notice(exc: Exception) -> None:
    message = str(exc).strip() or "daemon unavailable"
    print(f"[chat] daemon unavailable, falling back to in-process runtime: {message}")


def print_turn_error(error: object) -> None:
    message = str(error).strip() or "turn failed"
    print(f"[chat] turn failed: {message}")


def print_retry_notice(*, attempt: int, max_attempts: int, error: object) -> None:
    message = str(error).strip() or "turn failed"
    next_attempt = int(attempt) + 1
    print(
        f"[chat] transient failure, retrying ({next_attempt}/{max_attempts}): {message}"
    )


def set_quiet_log_level() -> None:
    apply_logging_mode("interactive")


def _terminal_supports_color() -> bool:
    env_config = resolve_environment_config()
    if env_config.get("NO_COLOR", "").strip():
        return False
    forced = env_config.get(OPENMINION_COLOR_ENV, "").strip().lower()
    if forced in {"0", "false", "off", "no"}:
        return False
    if forced in {"1", "true", "on", "yes", "always"}:
        return True
    return bool(getattr(sys.stdout, "isatty", lambda: False)())


def _ansi(value: str, code: str) -> str:
    return f"\033[{code}m{value}\033[0m"


def _sender_style(sender: str) -> str:
    if str(sender or "").strip().lower() == "you":
        return "1;34"
    return "1;32"


def _split_sender_and_content(raw_body: str, *, default_sender: str) -> tuple[str, str]:
    body = str(raw_body or "").strip()
    if ":" not in body:
        return default_sender, body
    left, right = body.split(":", 1)
    candidate = left.strip()
    if not candidate:
        return default_sender, body
    if " " in candidate or len(candidate) > 64:
        return default_sender, body
    return candidate, right.strip()


def _styled_context(*, session_id: str, agent_id: str) -> str:
    if not _terminal_supports_color():
        return f"[{session_id}|{agent_id}]"
    return (
        f"{_ansi('[', '2')}"
        f"{_ansi(session_id, '2;36')}"
        f"{_ansi('|', '2')}"
        f"{_ansi(agent_id, '1;36')}"
        f"{_ansi(']', '2')}"
    )


def _truncate_terminal_text(text: str, *, prefix_width: int = 0) -> str:
    normalized = " ".join(str(text or "").replace("\r", "\n").split())
    try:
        columns = max(20, shutil.get_terminal_size((100, 20)).columns)
    except OSError:
        columns = 100
    available = max(8, columns - max(0, prefix_width))
    if len(normalized) <= available:
        return normalized
    if available <= 1:
        return "…"
    return normalized[: available - 1] + "…"


def _phase_status_text(status: object) -> str:
    return format_primary_status_text(status, fallback_label="Working...")


_format_elapsed_time = _shared_format_elapsed_time


@dataclass
class PhaseStatusDisplay:
    enabled: bool
    animate: bool = True
    fallback_label: str = "Working..."
    clock: Callable[[], float] = time.monotonic

    def __post_init__(self) -> None:
        is_tty = bool(getattr(sys.stdout, "isatty", lambda: False)())
        self.enabled = bool(self.enabled)
        self.animate = bool(self.animate) and self.enabled and is_tty
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._render_lock = threading.Lock()
        self._label = self.fallback_label
        self._show_spinner = bool(self.animate)
        self._last_rendered_line: str | None = None
        self._is_tty = is_tty
        self._controller = PhaseStatusController(
            fallback_label=self.fallback_label,
            clock=self.clock,
        )

    @property
    def callback(self) -> Callable[[object | dict[str, Any]], None] | None:
        if not self.enabled:
            return None
        return self.update

    def __enter__(self) -> "PhaseStatusDisplay":
        if not self.enabled:
            return self
        self._controller.start_turn()
        if not self.animate:
            return self
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()
        self._render_once()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.clear()

    def update(self, status: object | dict[str, Any]) -> None:
        if not self.enabled:
            return
        view = self._controller.update(status)
        if view is None:
            return
        with self._lock:
            self._label = view.primary_text
            self._show_spinner = bool(view.show_spinner)
        if not self.animate:
            self._render_status_line()
            return
        if not view.show_spinner:
            self._clear_line()
            self._render_status_line(dedupe_terminal_line=True)
            return
        self._render_once()

    def clear(self) -> None:
        if self._thread is None:
            if self.enabled:
                self._controller.end_turn()
            return
        self._stop_event.set()
        self._thread.join(timeout=1.0)
        self._thread = None
        self._controller.end_turn()
        self._clear_line()

    def emit_note(self, text: str) -> None:
        if not self.enabled:
            return
        note = _truncate_terminal_text(str(text or "").strip())
        if not note:
            return
        self._clear_line()
        sys.stdout.write(f"{note}\n")
        sys.stdout.flush()
        self._last_rendered_line = None
        if self.animate:
            self._render_once()

    def _spin(self) -> None:
        from openminion.cli.presentation.styles import get_spinner_frame, reset_spinner

        reset_spinner()
        while not self._stop_event.wait(0.1):
            with self._lock:
                show_spinner = self._show_spinner
            if not show_spinner:
                continue
            self._render_once(frame=get_spinner_frame())

    def _clear_line(self) -> None:
        from openminion.cli.presentation.styles import clear_line

        with self._render_lock:
            sys.stdout.write(clear_line())
            sys.stdout.flush()

    def _render_status_line(self, *, dedupe_terminal_line: bool = False) -> None:
        with self._lock:
            label = self._label
        elapsed_seconds = self._controller.elapsed_seconds()
        if elapsed_seconds is not None:
            elapsed_text = _shared_format_elapsed_time(elapsed_seconds)
            label = f"{elapsed_text} | {label}"
        label = _truncate_terminal_text(label)
        if dedupe_terminal_line and label == self._last_rendered_line:
            return
        sys.stdout.write(f"{label}\n")
        sys.stdout.flush()
        self._last_rendered_line = label

    def _render_once(self, *, frame: str | None = None) -> None:
        if not self.enabled:
            return
        from openminion.cli.presentation.styles import (
            StyleToken,
            clear_line,
            get_spinner_frame,
            style,
        )

        with self._lock:
            label = self._label
        elapsed_seconds = self._controller.elapsed_seconds()
        if elapsed_seconds is not None:
            elapsed_text = _shared_format_elapsed_time(elapsed_seconds)
            label = f"{elapsed_text} | {label}"
        label = _truncate_terminal_text(label, prefix_width=2)
        if label == self._last_rendered_line:
            return
        spinner_frame = frame or get_spinner_frame()
        with self._render_lock:
            sys.stdout.write(
                f"{clear_line()}{spinner_frame} {style(StyleToken.SPINNER, label)}"
            )
            sys.stdout.flush()
        self._last_rendered_line = label


@dataclass
class Spinner:
    enabled: bool
    label: str = "waiting for response"
    show_elapsed: bool = False
    clock: Callable[[], float] = time.monotonic

    def __post_init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._started_at: float | None = None

    def __enter__(self) -> "Spinner":
        if not self.enabled or not sys.stdout.isatty():
            return self
        self._started_at = self.clock()
        self._render_frame("|")
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._thread is None:
            return
        self._stop_event.set()
        self._thread.join(timeout=1.0)
        self._clear_line()

    def _spin(self) -> None:
        from openminion.cli.presentation.styles import reset_spinner

        reset_spinner()
        frames = "|/-\\"
        index = 1
        while not self._stop_event.wait(0.1):
            frame = frames[index % len(frames)]
            self._render_frame(frame)
            index += 1

    def _render_frame(self, frame: str) -> None:
        from openminion.cli.presentation.styles import StyleToken, style

        label = self.label
        if self.show_elapsed and self._started_at is not None:
            label = (
                f"{_shared_format_elapsed_time(self.clock() - self._started_at)}"
                f" | {label}"
            )
        label = _truncate_terminal_text(label, prefix_width=4)
        label_styled = style(StyleToken.SPINNER, label)
        spinner_styled = style(StyleToken.SPINNER, f"[{frame}]")
        sys.stdout.write(f"\r{spinner_styled} {label_styled}")
        sys.stdout.flush()

    def _clear_line(self) -> None:
        label = self.label
        if self.show_elapsed:
            label = f"999s | {label}"
        blank = " " * (len(label) + 6)
        sys.stdout.write(f"\r{blank}\r")
        sys.stdout.flush()


def resolve_chat_provider_details(
    config: Any,
    *,
    agent_id: str,
    args: Any | None = None,
) -> tuple[str, str]:
    try:
        effective_config = build_runtime_config(
            config,
            agent_id=agent_id,
            overrides=run_profile_overrides_from_mapping(vars(args) if args else None),
        )
        profile = getattr(effective_config, "agents", {}).get(agent_id)
        if profile is None:
            default_agent_id = resolve_default_agent_id(effective_config)
            profile = getattr(effective_config, "agents", {}).get(default_agent_id)
    except (AttributeError, TypeError, ValueError, UnknownProfileError):
        effective_config = config
        try:
            default_agent_id = resolve_default_agent_id(config)
            profile = getattr(config, "agents", {}).get(default_agent_id)
        except (AttributeError, TypeError, ValueError, UnknownProfileError):
            profile = None
    provider_name = (
        str(getattr(profile, "provider", "") or "echo").strip().lower() or "echo"
    )
    providers = getattr(effective_config, "providers", None)
    model_name = ""
    if providers is not None:
        if provider_name == "openai":
            model_name = str(
                getattr(getattr(providers, "openai", None), "model", "") or ""
            ).strip()
        elif provider_name in {"anthropic", "claude"}:
            model_name = str(
                getattr(getattr(providers, "anthropic", None), "model", "") or ""
            ).strip()
        elif provider_name == "openrouter":
            model_name = str(
                getattr(getattr(providers, "openrouter", None), "model", "") or ""
            ).strip()
        elif provider_name == "cerebras":
            model_name = str(
                getattr(getattr(providers, "cerebras", None), "model", "") or ""
            ).strip()
        elif provider_name == "groq":
            model_name = str(
                getattr(getattr(providers, "groq", None), "model", "") or ""
            ).strip()
        elif provider_name == "ollama":
            model_name = str(
                getattr(getattr(providers, "ollama", None), "model", "") or ""
            ).strip()
        elif provider_name == "cortensor":
            model_name = str(
                getattr(getattr(providers, "cortensor", None), "model", "") or ""
            ).strip()
    return provider_name, model_name


def print_chat_provider_banner(
    config: Any,
    *,
    agent_id: str,
    args: Any | None = None,
) -> None:
    provider_name, model_name = resolve_chat_provider_details(
        config,
        agent_id=agent_id,
        args=args,
    )
    demo_mode = bool(getattr(getattr(config, "runtime", None), "demo_mode", False))
    details = f"provider={provider_name}"
    if model_name:
        details += f" model={model_name}"
    print(styles.style(styles.StyleToken.SYSTEM, details))
    if provider_name == "echo" and demo_mode:
        print(
            styles.style(
                styles.StyleToken.WARNING,
                "[chat] demo mode active; responses mirror input text intentionally.",
            )
        )
    elif provider_name == "echo":
        print(
            styles.style(
                styles.StyleToken.WARNING,
                "[chat] provider=echo active; responses mirror input text. "
                "Check --config path, agent profile, and provider settings.",
            )
        )


def print_chat_ready_banner(
    *,
    runtime_state: Any,
    agent_id: str,
    session_id: str,
    conversation_selection: dict[str, str],
    conversation_id: str,
    resume_requested: bool,
    reset_requested: bool,
    config: Any,
    args: Any,
) -> None:
    print(
        styles.style(styles.StyleToken.SYSTEM, "chat ready")
        + f" agent={agent_id} session={session_id} transport={runtime_state.transport} (type / for help)"
    )
    if resume_requested and conversation_selection["source"] == "session_reuse":
        print(
            styles.style(
                styles.StyleToken.WARNING,
                f"[chat] resuming conversation '{conversation_id}' for session '{session_id}'.",
            )
        )
    elif resume_requested:
        print(
            styles.style(
                styles.StyleToken.WARNING,
                f"[chat] no prior conversation found for session '{session_id}'; starting fresh.",
            )
        )
    elif not reset_requested and conversation_selection["source"] == "session_reuse":
        print(
            styles.style(
                styles.StyleToken.WARNING,
                f"[chat] resuming existing session context for '{session_id}'. "
                "Use --reset-session or a new --session id to start fresh.",
            )
        )
    print_chat_provider_banner(config, agent_id=agent_id, args=args)


def print_first_session_tip_if_requested(args: Any) -> None:
    if not bool(getattr(args, "first_session_tip", False)):
        return
    print(
        styles.style(
            styles.StyleToken.WARNING,
            "Tip: type `openminion chat` to start a conversation, or "
            "`openminion focus` for a single-agent shell.",
        )
    )


def print_stale_session_notice(
    *,
    session_id: str,
    config_path: str | None,
    reset_requested: bool,
    get_session_record_fn: Callable[..., Any],
) -> None:
    if reset_requested:
        return
    session = get_session_record_fn(session_id=session_id, config_path=config_path)
    if session is None or str(getattr(session, "status", "") or "").strip() != "stale":
        return
    print(
        styles.style(
            styles.StyleToken.WARNING,
            f"[chat] session '{session_id}' was marked stale after inactivity; resuming anyway.",
        )
    )


def print_agent_resolution_notice(
    *,
    session_id: str,
    agent_id: str,
    agent_resolution: dict[str, str],
    reset_requested: bool,
    config_path: str | None = None,
    session_allows_agent_id_fn: Callable[..., bool],
) -> None:
    source = str(agent_resolution.get("source", "") or "").strip()
    session_agent_id = str(agent_resolution.get("session_agent_id", "") or "").strip()
    default_agent_id = str(agent_resolution.get("default_agent_id", "") or "").strip()
    if (
        source == "session_resume"
        and session_agent_id
        and session_agent_id != default_agent_id
    ):
        print(
            styles.style(
                styles.StyleToken.WARNING,
                f"[chat] using prior session agent '{session_agent_id}' for '{session_id}' "
                f"instead of config default '{default_agent_id}'.",
            )
        )
        return
    if (
        source == "explicit"
        and not reset_requested
        and session_agent_id
        and session_agent_id != agent_id
        and not session_allows_agent_id_fn(
            session_id=session_id,
            agent_id=agent_id,
            config_path=config_path,
        )
    ):
        print(
            styles.style(
                styles.StyleToken.WARNING,
                f"[chat] session '{session_id}' was previously used with agent "
                f"'{session_agent_id}', but continuing with explicit --agent '{agent_id}'. "
                "Use --reset-session or a new --session id to avoid cross-agent context bleed.",
            )
        )


def session_profile_mismatch_message(
    *,
    session_id: str,
    agent_id: str,
    agent_resolution: dict[str, str],
    reset_requested: bool,
    config_path: str | None = None,
    session_allows_agent_id_fn: Callable[..., bool],
) -> str:
    if reset_requested:
        return ""
    if str(agent_resolution.get("source", "") or "").strip() != "explicit":
        return ""
    session_agent_id = str(agent_resolution.get("session_agent_id", "") or "").strip()
    if not session_agent_id or session_agent_id == agent_id:
        return ""
    if session_allows_agent_id_fn(
        session_id=session_id,
        agent_id=agent_id,
        config_path=config_path,
    ):
        return ""
    return (
        f"[chat] session '{session_id}' is already bound to profile "
        f"'{session_agent_id}', but requested profile '{agent_id}' does not match. "
        "Use --reset-session or a new --session id to start fresh."
    )
