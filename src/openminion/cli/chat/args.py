from dataclasses import dataclass

from openminion.base.config.core import resolve_default_agent_id


@dataclass(frozen=True)
class ChatArgs:
    agent_id: str
    session_id: str
    session_name: str
    quiet: bool
    show_progress: bool
    show_activity_indicator: bool
    verbose: bool
    tools_verbose: bool


def normalize_chat_args(args, config) -> ChatArgs:
    return ChatArgs(
        agent_id=str(getattr(args, "agent", "") or "").strip()
        or resolve_default_agent_id(config),
        session_id=str(getattr(args, "session", "") or "").strip() or "cli-chat",
        session_name=str(getattr(args, "session_name", "") or "").strip(),
        quiet=bool(getattr(args, "quiet", False)),
        show_progress=not bool(getattr(args, "no_progress", False)),
        show_activity_indicator=not bool(getattr(args, "no_activity_indicator", False)),
        verbose=bool(getattr(args, "verbose", False)),
        tools_verbose=bool(getattr(args, "tools_verbose", False)),
    )
