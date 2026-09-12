from __future__ import annotations

import ast
import asyncio
import inspect
import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console

from openminion.cli.interactive.terminal.shell import (
    _SLASH_COMMANDS,
    _handle_slash,
    _handle_slash_input,
)
from openminion.cli.interactive.terminal.shell.actions import (
    _handle_session_slash,
    _handle_shell_preference_slash,
    _handle_visible_parity_slash,
)
from openminion.cli.interactive.terminal.shell.slash_output import (
    PROMPT_SAFE_OUTPUT_SLASHES,
    handle_debug_output_slash,
    render_context_review,
)
from openminion.cli.interactive.terminal.shell.sessions import resume_session
from openminion.cli.interactive.terminal.status_line import TerminalStatusLine
from openminion.cli.interactive.terminal.transcript import TerminalTranscript
from openminion.cli.interactive.models import ModelSelection
from openminion.cli.presentation.models import ChatMessage, MessageKind
from openminion.cli.presentation.custom_commands import CustomCommand
from openminion.cli.presentation.slash_commands import (
    SLASH_COMMANDS,
    canonical_slash_command_name,
    slash_completion_catalog,
)


class _StubOverlay:
    def present_approval(self, _prompt: str) -> str:
        return "deny"


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

    def context_trace_payload(self, *, session_id: str) -> dict[str, object]:
        return {
            "session_id": session_id,
            "traces": [],
            "count": 0,
            "degraded": "context_trace_not_found",
        }

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


class _HelpSafetyRuntime(_VisibleRuntime):
    def __init__(self) -> None:
        self.list_agents_calls = 0

    def list_agents(self) -> list[object]:
        self.list_agents_calls += 1
        return []


def _run_prompt_slash(
    text: str,
    tmp_path: Path,
    *,
    runtime: object | None = None,
    custom_commands: dict[str, CustomCommand] | None = None,
    transcript: TerminalTranscript | None = None,
) -> tuple[bool, TerminalTranscript]:
    console = Console(file=io.StringIO(), force_terminal=False, width=160)
    active_transcript = transcript or TerminalTranscript(console)
    should_exit = asyncio.run(
        _handle_slash_input(
            text,
            runtime=runtime or _HelpSafetyRuntime(),
            console=console,
            transcript=active_transcript,
            overlay=_StubOverlay(),  # type: ignore[arg-type]
            status_line=TerminalStatusLine(),
            working_dir=str(tmp_path),
            custom_commands=custom_commands or {},
        )
    )
    return should_exit, active_transcript


def _extract_implemented_slashes() -> set[str]:
    implemented: set[str] = set()

    dispatchers = (
        _handle_slash,
        _handle_session_slash,
        _handle_shell_preference_slash,
        _handle_visible_parity_slash,
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
    cataloged = {canonical_slash_command_name(name) for name in _SLASH_COMMANDS}
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


def test_every_non_control_slash_uses_prompt_safe_output() -> None:
    expected = {command.name for command in SLASH_COMMANDS} - {"/clear", "/exit"}

    assert PROMPT_SAFE_OUTPUT_SLASHES == expected


def test_context_review_forwards_explicit_paths(monkeypatch, tmp_path: Path) -> None:
    from openminion.cli.interactive.terminal.shell import slash_output

    captured: dict[str, str] = {}

    def _build_review(payload, **kwargs):
        captured.update(kwargs)
        return {"payload": payload}

    monkeypatch.setattr(slash_output, "build_memory_context_review", _build_review)
    monkeypatch.setattr(
        slash_output,
        "render_memory_context_review",
        lambda review: f"context review: {review['payload']['session_id']}",
    )
    runtime = _VisibleRuntime()
    runtime.context_trace_payload = lambda *, session_id: {
        "session_id": session_id,
        "traces": [],
        "count": 0,
    }
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=160)

    asyncio.run(
        _handle_slash(
            "/context-review session=review canary=canary.json "
            "calibration=calibration.json artifacts=artifacts",
            runtime=runtime,
            console=console,
            transcript=TerminalTranscript(console),
            overlay=_StubOverlay(),  # type: ignore[arg-type]
            status_line=TerminalStatusLine(),
            working_dir=str(tmp_path),
        )
    )

    assert "context review: review" in buf.getvalue()
    assert captured == {
        "canary_path": "canary.json",
        "calibration_path": "calibration.json",
        "artifacts_dir": "artifacts",
    }


def test_context_review_renders_runtime_degradation() -> None:
    runtime = _VisibleRuntime()
    runtime.context_trace_payload = lambda *, session_id: {
        "session_id": session_id,
        "traces": [],
        "count": 0,
        "degraded": "context_trace_not_found",
    }

    rendered = render_context_review(runtime, "")

    assert "degraded: context_trace_not_found" in rendered


def test_overview_renders_operations_sections(monkeypatch, tmp_path: Path) -> None:
    from openminion.cli.status import overview

    monkeypatch.setattr(
        overview,
        "build_operations_overview",
        lambda _runtime, *, working_dir: {"working_dir": working_dir},
    )
    monkeypatch.setattr(
        overview,
        "render_operations_overview",
        lambda snapshot: (
            f"Runtime  [available]\nHost  [available]\n{snapshot['working_dir']}"
        ),
    )
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=160)

    asyncio.run(
        _handle_slash(
            "/overview",
            runtime=_VisibleRuntime(),
            console=console,
            transcript=TerminalTranscript(console),
            overlay=_StubOverlay(),  # type: ignore[arg-type]
            status_line=TerminalStatusLine(),
            working_dir=str(tmp_path),
        )
    )

    output = buf.getvalue()
    assert "Runtime  [available]" in output
    assert "Host  [available]" in output
    assert str(tmp_path) in output


def test_copy_uses_latest_copyable_message(monkeypatch, tmp_path: Path) -> None:
    from openminion.cli.interactive.terminal.shell import slash_output
    from openminion.cli.presentation.models import ChatMessage, MessageKind

    copied: list[str] = []
    monkeypatch.setattr(
        slash_output,
        "copy_to_clipboard",
        lambda body: copied.append(body) or True,
    )
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=160)
    transcript = TerminalTranscript(console)
    transcript.push_message(
        ChatMessage(kind=MessageKind.AGENT, sender="assistant", body="copy this"),
        render=False,
    )
    transcript.push_message(
        ChatMessage(kind=MessageKind.SYSTEM, sender="system", body="skip this"),
        render=False,
    )

    asyncio.run(
        _handle_slash(
            "/copy",
            runtime=_VisibleRuntime(),
            console=console,
            transcript=transcript,
            overlay=_StubOverlay(),  # type: ignore[arg-type]
            status_line=TerminalStatusLine(),
            working_dir=str(tmp_path),
        )
    )

    assert copied == ["copy this"]
    assert "copied last message" in buf.getvalue()


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


@pytest.mark.parametrize(
    "text",
    (
        "/help agents",
        "/help /agents",
        "/help agent",
        "/agents --help",
        "/agents ?",
        "/agent --help",
    ),
)
def test_prompt_loop_resolves_contextual_help_to_canonical_command(
    text: str, tmp_path: Path
) -> None:
    runtime = _HelpSafetyRuntime()
    should_exit, transcript = _run_prompt_slash(text, tmp_path, runtime=runtime)

    output = transcript._messages[-1].body
    assert should_exit is False
    assert output.startswith(
        "/agents — List configured agents or filter by exact ID or label"
    )
    assert "Usage:\n  /agents\n  /agents <agent-id-or-label>" in output
    assert "Alias: /agent" in output
    assert "--profile" in output
    assert runtime.list_agents_calls == 0


@pytest.mark.parametrize("text", ("/exit --help", "/clear ?", "/review --help"))
def test_contextual_help_does_not_execute_target_command(
    text: str, tmp_path: Path
) -> None:
    transcript = TerminalTranscript(
        Console(file=io.StringIO(), force_terminal=False, width=160)
    )
    transcript.push_message(
        ChatMessage(kind=MessageKind.AGENT, sender="assistant", body="keep me"),
        render=False,
    )
    should_exit, transcript = _run_prompt_slash(text, tmp_path, transcript=transcript)

    assert should_exit is False
    assert any(message.body == "keep me" for message in transcript._messages)
    assert transcript._messages[-1].body.startswith(text.split()[0] + " —")


def test_help_with_extra_operands_returns_help_usage(tmp_path: Path) -> None:
    _, transcript = _run_prompt_slash("/help new session", tmp_path)

    output = transcript._messages[-1].body
    assert output.startswith("/help —")
    assert "  /help <command>" in output
    assert not output.startswith("/new —")


def test_unknown_contextual_help_stays_local(tmp_path: Path) -> None:
    _, transcript = _run_prompt_slash("/help statsu", tmp_path)

    assert transcript._messages[-1].body == (
        "Unknown command: /statsu\n"
        "Did you mean /status?\n"
        "Type / to view available commands."
    )


def test_custom_contextual_help_uses_metadata_without_rendering_body(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from openminion.cli.presentation import custom_commands as custom_module

    command = CustomCommand(
        slash="/sample",
        body="Read @secret.txt then run !`touch should-not-exist` for $ARGUMENTS",
        source="project",
        path=tmp_path / "sample.md",
        description="Run the sample workflow",
        usage="/sample <topic>",
    )
    monkeypatch.setattr(
        custom_module,
        "render_command",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("custom body must not render for help")
        ),
    )
    _, transcript = _run_prompt_slash(
        "/sample --help", tmp_path, custom_commands={"/sample": command}
    )

    output = transcript._messages[-1].body
    assert output.startswith("/sample — Run the sample workflow")
    assert "  /sample <topic>" in output
    assert "Source: project" in output
    assert "secret.txt" not in output
    assert not (tmp_path / "should-not-exist").exists()


def test_custom_help_defaults_usage_and_description_without_guessing(
    tmp_path: Path,
) -> None:
    command = CustomCommand(
        slash="/plain",
        body="body",
        source="user",
        path=tmp_path / "plain.md",
    )
    _, transcript = _run_prompt_slash(
        "/help plain", tmp_path, custom_commands={"/plain": command}
    )

    output = transcript._messages[-1].body
    assert output.startswith("/plain — custom command")
    assert "Usage:\n  /plain" in output
    assert "[arguments]" not in output
    assert "Source: user" in output


def test_built_ins_win_custom_primary_and_alias_collisions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from openminion.cli.presentation import custom_commands as custom_module

    colliding = {
        name: CustomCommand(
            slash=name,
            body="custom body",
            source="project",
            path=tmp_path / f"{name[1:]}.md",
            description="custom collision",
        )
        for name in ("/agents", "/agent")
    }
    catalog = slash_completion_catalog(colliding)

    assert catalog["/agents"].startswith("List configured agents")
    assert "/agent" not in catalog

    _, transcript = _run_prompt_slash("/help", tmp_path, custom_commands=colliding)
    assert "custom collision" not in transcript._messages[-1].body

    monkeypatch.setattr(
        custom_module,
        "render_command",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("built-in alias must win dispatch")
        ),
    )
    runtime = _HelpSafetyRuntime()
    _run_prompt_slash(
        "/agent",
        tmp_path,
        runtime=runtime,
        custom_commands=colliding,
    )
    assert runtime.list_agents_calls == 1


def test_global_help_includes_non_colliding_custom_command_once(tmp_path: Path) -> None:
    command = CustomCommand(
        slash="/ship",
        body="ship it",
        source="project",
        path=tmp_path / "ship.md",
        description="Ship the current change",
    )
    _, transcript = _run_prompt_slash(
        "/help", tmp_path, custom_commands={"/ship": command}
    )

    output = transcript._messages[-1].body
    assert output.count("/ship") == 1
    assert "Ship the current change" in output


def test_ordinary_custom_command_still_renders_and_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from openminion.cli.interactive.terminal import shell as terminal_shell

    captured: list[str] = []

    async def _capture_turn(*, text: str, **_kwargs: object) -> None:
        captured.append(text)

    monkeypatch.setattr(terminal_shell, "_run_interruptible_agent_turn", _capture_turn)
    command = CustomCommand(
        slash="/sample",
        body="Sample: $ARGUMENTS",
        source="project",
        path=tmp_path / "sample.md",
    )
    _, transcript = _run_prompt_slash(
        "/sample topic", tmp_path, custom_commands={"/sample": command}
    )

    assert captured == ["Sample: topic"]
    assert transcript._messages[-1].kind == MessageKind.USER
    assert transcript._messages[-1].body == "Sample: topic"


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
