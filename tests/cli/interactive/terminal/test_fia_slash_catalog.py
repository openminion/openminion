from __future__ import annotations

import ast
import asyncio
import inspect
import io
from pathlib import Path
from types import SimpleNamespace

from rich.console import Console

from openminion.cli.interactive.terminal.shell import (
    _SLASH_COMMANDS,
    _handle_slash,
    _handle_slash_input,
)
from openminion.cli.interactive.terminal.shell.actions import (
    _handle_session_slash,
    _handle_shell_preference_slash,
)
from openminion.cli.interactive.terminal.shell.slash_output import (
    PROMPT_SAFE_OUTPUT_SLASHES,
    handle_debug_output_slash,
)
from openminion.cli.interactive.terminal.shell.sessions import resume_session
from openminion.cli.interactive.terminal.status_line import TerminalStatusLine
from openminion.cli.interactive.terminal.transcript import TerminalTranscript
from openminion.cli.interactive.models import ModelSelection


class _StubOverlay:
    pass


class _ResumeOverlay:
    def __init__(self, choice: str) -> None:
        self.choice = choice
        self.items: list[object] = []

    def present_resume_picker(self, sessions: list[object]) -> str:
        self.items = sessions
        return self.choice


class _VisibleRuntime:
    agent_id = "minimax-m2-7"
    provider_name = "openai"
    model_name = "MiniMax-M2.7"
    session_id = "session-1"
    permission_mode = "default"
    permission_overrides: dict[str, str] = {}

    def list_models(self) -> list[ModelSelection]:
        return [
            ModelSelection(
                index=1,
                connection_id="minimax",
                connection_name="MiniMax",
                provider="openai",
                transport_adapter="openai_chat",
                model="MiniMax-M2.7",
                configured_connection=True,
                active=True,
                agent_default=True,
            )
        ]

    def switch_model(self, _arg: str) -> ModelSelection:
        return self.list_models()[0]

    def memory_report(self) -> str:
        return ""

    def list_memory_records(self) -> list[object]:
        return []

    def list_memory_candidates(self) -> list[object]:
        return []

    def list_tools(self) -> list[tuple[str, bool]]:
        return [("file.read", True)]

    def list_skill_rows(self) -> list[dict[str, str]]:
        return [{"id": "demo-skill"}]

    def list_sessions(self) -> list[object]:
        return []

    def list_agents(self) -> list[object]:
        return []

    def mcp_status_report(self) -> str:
        return ""

    def token_usage_snapshot(self) -> None:
        return None

    def token_usage_report(self) -> str:
        return "no token usage data"

    def effort_level(self) -> str:
        return "default"

    def set_effort_level(self, value: str) -> str:
        return value

    def statusline_command(self) -> str:
        return ""

    def set_statusline_command(self, value: str) -> str:
        return value

    def statusline_label(self) -> str:
        return ""

    def undo_last_turn(self) -> dict[str, object]:
        return {"ok": False, "message": "nothing to undo"}

    def set_permission_mode(self, value: str) -> str:
        self.permission_mode = value
        return value

    def cycle_permission_mode(self) -> str:
        self.permission_mode = "readonly"
        return self.permission_mode

    def set_permission_override(self, _tool: str, value: str) -> str:
        return value

    def read_only_mode(self) -> bool:
        return False

    def set_read_only_mode(self, value: bool) -> bool:
        return value

    def compact_history(self) -> dict[str, str]:
        return {"reason": "no_session"}

    def execute_goal_command(self, _text: str) -> tuple[str, str]:
        return "ok", "goal ok"

    def room_participants_report(self) -> str:
        return "Room: review\n  routing: addressed\n  participants: 2"

    def room_invite_agent(self, agent_id: str) -> SimpleNamespace:
        return SimpleNamespace(
            participant_type="agent",
            participant_id=agent_id,
            role="participant",
        )

    def room_invite_human(
        self, human_id: str, *, role: str = "participant"
    ) -> SimpleNamespace:
        return SimpleNamespace(
            participant_type="human",
            participant_id=human_id,
            role=role,
        )

    def room_kick(self, _participant_type: str, _participant_id: str) -> bool:
        return True

    def room_activate(self, _agent_id: str) -> None:
        return None

    def room_set_routing(self, _mode: str) -> None:
        return None


def _extract_implemented_slashes() -> set[str]:
    implemented: set[str] = set()

    dispatchers = (
        _handle_slash,
        _handle_session_slash,
        _handle_shell_preference_slash,
        handle_debug_output_slash,
    )
    for dispatcher in dispatchers:
        tree = ast.parse(inspect.getsource(dispatcher))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            if (
                isinstance(node.left, ast.Name)
                and node.left.id == "cmd"
                and len(node.ops) == 1
                and isinstance(node.ops[0], ast.Eq)
                and len(node.comparators) == 1
                and isinstance(node.comparators[0], ast.Constant)
                and isinstance(node.comparators[0].value, str)
                and node.comparators[0].value.startswith("/")
            ):
                implemented.add(node.comparators[0].value)
            if (
                isinstance(node.left, ast.Name)
                and node.left.id == "cmd"
                and len(node.ops) == 1
                and isinstance(node.ops[0], ast.In)
                and len(node.comparators) == 1
                and isinstance(node.comparators[0], (ast.Tuple, ast.List, ast.Set))
            ):
                implemented.update(
                    elt.value
                    for elt in node.comparators[0].elts
                    if isinstance(elt, ast.Constant)
                    and isinstance(elt.value, str)
                    and elt.value.startswith("/")
                )

    return implemented


# ── Load-bearing test ─────────────────────────────────────────────


def test_slash_catalog_matches_implementation() -> None:
    cataloged = set(_SLASH_COMMANDS)
    implemented = _extract_implemented_slashes()
    missing = cataloged - implemented
    assert not missing, (
        f"Slashes in catalog without dispatch implementation: "
        f"{sorted(missing)}. Either implement them in _handle_slash "
        f"or strip them from _SLASH_COMMANDS."
    )


# ── Post-FIA-01 strip verification ───────────────────────────────


def test_stripped_slashes_not_in_catalog() -> None:
    stripped: set[str] = set()
    cataloged = set(_SLASH_COMMANDS)
    overlap = cataloged & stripped
    assert not overlap, (
        f"FIA-01 strip regression: {sorted(overlap)} reintroduced to "
        f"_SLASH_COMMANDS without implementation. Per FIA tracker "
        f"locked scope, these slashes need runtime cooperation or "
        f"duplicate existing slashes; do not re-add without "
        f"implementation."
    )


def test_implemented_slashes_in_catalog() -> None:
    cataloged = set(_SLASH_COMMANDS)
    implemented = _extract_implemented_slashes()
    # Originally-implemented (pre-FIA) slashes that MUST be in
    # the catalog.
    pre_fia = {
        "/clear",
        "/exit",
        "/expand",
        "/help",
        "/normal",
        "/quiet",
        "/quit",
        "/verbose",
    }
    for slash in pre_fia:
        assert slash in cataloged, (
            f"{slash} dropped from catalog (pre-FIA implementation should still be exposed)"
        )
        assert slash in implemented, f"{slash} dispatch arm missing"


# ── Helper invariants ────────────────────────────────────────────


def test_extractor_finds_known_slashes() -> None:
    implemented = _extract_implemented_slashes()
    # Must find at least these well-known dispatch arms.
    assert "/" in implemented
    assert "/exit" in implemented
    assert "/quit" in implemented
    assert "/clear" in implemented
    assert "/expand" in implemented
    assert "/quiet" in implemented
    assert "/verbose" in implemented
    assert "/normal" in implemented


def test_catalog_has_no_duplicates() -> None:
    assert len(_SLASH_COMMANDS) == len(set(_SLASH_COMMANDS))


def test_catalog_size_after_fia_01() -> None:
    assert len(_SLASH_COMMANDS) >= 9, (
        f"Catalog has {len(_SLASH_COMMANDS)} entries; expected ≥ 9 "
        f"after FIA-01 strip pass (≥ 13 after FIA-05)"
    )


def test_bare_slash_dispatch_prints_menu() -> None:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=160)

    asyncio.run(
        _handle_slash(
            "/",
            runtime=object(),
            console=console,
            transcript=TerminalTranscript(console),
            overlay=_StubOverlay(),  # type: ignore[arg-type]
            status_line=TerminalStatusLine(),
            working_dir="/tmp",
        )
    )

    out = buf.getvalue()
    assert "Slash commands:" in out
    assert "/help" in out
    assert "not yet implemented" not in out


def test_terminal_room_invite_rejects_agent_role_operand() -> None:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=160)

    asyncio.run(
        _handle_slash(
            "/invite agent beta owner",
            runtime=_VisibleRuntime(),
            console=console,
            transcript=TerminalTranscript(console),
            overlay=_StubOverlay(),  # type: ignore[arg-type]
            status_line=TerminalStatusLine(),
            working_dir="/tmp",
        )
    )

    assert "usage: /invite agent <id>" in buf.getvalue()


def test_advertised_output_slashes_are_visible(monkeypatch, tmp_path: Path) -> None:
    from openminion.cli.interactive.terminal.shell import actions

    monkeypatch.setattr(
        actions,
        "render_browser_command",
        lambda _arg, *, working_dir: "Browser: providers=pinchtab sidecar=ready",
    )

    for slash in sorted(PROMPT_SAFE_OUTPUT_SLASHES):
        buf = io.StringIO()
        console = Console(file=buf, force_terminal=False, width=160)
        transcript = TerminalTranscript(console)
        before = len(transcript._messages)

        asyncio.run(
            _handle_slash(
                slash,
                runtime=_VisibleRuntime(),
                console=console,
                transcript=transcript,
                overlay=_StubOverlay(),  # type: ignore[arg-type]
                status_line=TerminalStatusLine(),
                working_dir=str(tmp_path),
            )
        )

        assert buf.getvalue().strip() or len(transcript._messages) > before, (
            f"{slash} accepted input but produced no visible terminal output"
        )


def test_prompt_loop_routes_output_slashes_through_transcript(
    monkeypatch, tmp_path: Path
) -> None:
    from openminion.cli.interactive.terminal.shell import actions

    monkeypatch.setattr(
        actions,
        "render_browser_command",
        lambda _arg, *, working_dir: "Browser: providers=pinchtab sidecar=ready",
    )
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=160)
    transcript = TerminalTranscript(console)

    asyncio.run(
        _handle_slash_input(
            "/model",
            runtime=_VisibleRuntime(),
            console=console,
            transcript=transcript,
            overlay=_StubOverlay(),  # type: ignore[arg-type]
            status_line=TerminalStatusLine(),
            working_dir=str(tmp_path),
            custom_commands={},
        )
    )

    out = buf.getvalue()
    assert "current model: MiniMax-M2.7" in out
    assert "Connection" in out
    assert "API format" in out
    assert transcript._messages[-1].kind.value == "system"


def test_prompt_loop_routes_unknown_slash_with_suggestion_through_transcript(
    tmp_path: Path,
) -> None:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=160)
    transcript = TerminalTranscript(console)

    asyncio.run(
        _handle_slash_input(
            "/skill",
            runtime=_VisibleRuntime(),
            console=console,
            transcript=transcript,
            overlay=_StubOverlay(),  # type: ignore[arg-type]
            status_line=TerminalStatusLine(),
            working_dir=str(tmp_path),
            custom_commands={},
        )
    )

    assert transcript._messages[-1].body == (
        "Unknown command: /skill\n"
        "Did you mean /skills?\n"
        "Type / to view available commands."
    )


def test_prompt_loop_passes_skill_id_to_skill_detail_report(tmp_path: Path) -> None:
    class _SkillDetailRuntime(_VisibleRuntime):
        def skills_report(self, skill_id: str = "") -> str:
            return f"Skill detail: {skill_id}"

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=160)
    transcript = TerminalTranscript(console)

    asyncio.run(
        _handle_slash_input(
            "/skills demo_skill",
            runtime=_SkillDetailRuntime(),
            console=console,
            transcript=transcript,
            overlay=_StubOverlay(),  # type: ignore[arg-type]
            status_line=TerminalStatusLine(),
            working_dir=str(tmp_path),
            custom_commands={},
        )
    )

    assert transcript._messages[-1].body == "Skill detail: demo_skill"


def test_resume_session_accepts_dict_session_message_count() -> None:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=160)
    transcript = SimpleNamespace(messages=[], set_messages=lambda rows: rows)
    overlay = _ResumeOverlay("session-1")
    bound: list[str] = []

    runtime = SimpleNamespace(
        list_directory_sessions=lambda limit=50: [
            {"id": "session-1", "label": "Session 1", "message_count": 2}
        ],
        bind_session=lambda session_id: bound.append(session_id),
        get_current_history=lambda: ["history"],
    )

    resume_session(
        runtime=runtime,
        console=console,
        transcript=transcript,  # type: ignore[arg-type]
        overlay=overlay,  # type: ignore[arg-type]
    )

    assert bound == ["session-1"]
    assert overlay.items == [
        {"id": "session-1", "label": "Session 1", "message_count": 2}
    ]
    assert "resumed session: session-1" in buf.getvalue()
