from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass
from types import SimpleNamespace

from rich.console import Console

from openminion.base.config.action_policy import ACTION_POLICY_SESSION_OVERRIDE_KEY
from openminion.base.config.runtime.profile_overrides import RunProfileOverrides
from openminion.cli.interactive.runtime import OpenMinionRuntime
from openminion.cli.interactive.terminal.shell import _SLASH_COMMANDS, _handle_slash
from openminion.cli.interactive.terminal.status_line import TerminalStatusLine
from openminion.cli.interactive.terminal.transcript import TerminalTranscript
from openminion.cli.presentation.models import ChatMessage, MessageKind, ToolEvent


@dataclass
class _SessionRecord:
    id: str
    label: str
    message_count: int = 0


class _FakeRuntime:
    def __init__(self) -> None:
        self.created: list[str] = []
        self.bound: list[str] = []
        self.closed: list[str] = []
        self._next_session_id = "focus-new-001"
        self._directory_sessions = [
            _SessionRecord(id="focus-empty", label="focus-empty", message_count=0),
            _SessionRecord(id="focus-live", label="focus-live", message_count=3),
        ]
        self._history = [
            ChatMessage(kind=MessageKind.USER, sender="you", body="earlier"),
            ChatMessage(kind=MessageKind.AGENT, sender="agent", body="done"),
        ]

    def create_new_session(self) -> str:
        self.created.append(self._next_session_id)
        return self._next_session_id

    def close_current_session(self) -> str:
        self.closed.append("focus-live")
        return "focus-live"

    def list_directory_sessions(self, *, limit: int = 50):
        return list(self._directory_sessions[:limit])

    def bind_session(self, session_id: str) -> None:
        self.bound.append(session_id)

    def get_current_history(self):
        return list(self._history)


class _StubOverlay:
    def __init__(self, chosen: str | None) -> None:
        self._chosen = chosen
        self.presented: list[list[str]] = []

    def present_resume_picker(self, sessions):
        self.presented.append([str(getattr(item, "id", "")) for item in sessions])
        return self._chosen


class _PermissionSessions:
    def __init__(self) -> None:
        self.records = {
            "focus-existing": SimpleNamespace(id="focus-existing", metadata={}),
            "focus-historical": SimpleNamespace(
                id="focus-historical",
                metadata={
                    "permission_mode": "auto",
                    ACTION_POLICY_SESSION_OVERRIDE_KEY: "auto",
                    "permission_overrides": ["invalid"],
                },
            ),
        }

    def resolve_session(self, *, session_id: str, metadata=None, **_kwargs):
        record = self.records.get(session_id)
        if record is None:
            record = SimpleNamespace(id=session_id, metadata=dict(metadata or {}))
            self.records[session_id] = record
        return record

    def update_session_metadata(self, *, session_id: str, patch: dict) -> None:
        self.records[session_id].metadata.update(patch)


def _permission_runtime(
    sessions: _PermissionSessions,
    configured: RunProfileOverrides = RunProfileOverrides(),
) -> OpenMinionRuntime:
    runtime = OpenMinionRuntime.__new__(OpenMinionRuntime)
    runtime._rt = SimpleNamespace(
        sessions=sessions,
        config=SimpleNamespace(runtime=SimpleNamespace(security_lab=None)),
        run_profile_overrides=configured,
    )
    runtime._agent_id = "agent-1"
    runtime._gateway = object()
    runtime._channel = "cli"
    runtime._target = "tui"
    runtime._session_id = "focus-existing"
    runtime._conversation_id = ""
    runtime._working_dir = ""
    runtime._permission_mode = ""
    runtime._read_only_mode = False
    runtime._action_policy_mode_override = ""
    runtime._permission_overrides = {}
    runtime._permission_overrides_explicit = False
    runtime._permission_posture_diagnostic = ""
    runtime._project_context = None
    runtime._project_context_pending = False
    runtime.restore_session_model_selection = lambda _record: None
    runtime._reset_token_usage_accounting = lambda: None
    runtime._clear_model_selection = lambda: None
    runtime._rebind_model_gateway = lambda: None
    return runtime


def _make_console() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=160)
    return console, buf


async def _dispatch(
    text: str,
    *,
    runtime: _FakeRuntime,
    overlay: _StubOverlay,
    transcript: TerminalTranscript | None = None,
):
    console, buf = _make_console()
    transcript = transcript or TerminalTranscript(console)
    result = await _handle_slash(
        text,
        runtime=runtime,
        console=console,
        transcript=transcript,
        overlay=overlay,  # type: ignore[arg-type]
        status_line=TerminalStatusLine(),
        working_dir="/tmp/project",
    )
    return transcript, buf.getvalue(), result


def test_new_and_resume_added_to_catalog() -> None:
    assert "/new" in _SLASH_COMMANDS
    assert "/close" in _SLASH_COMMANDS
    assert "/resume" in _SLASH_COMMANDS


def test_new_starts_session_and_clears_transcript() -> None:
    runtime = _FakeRuntime()
    overlay = _StubOverlay(None)
    console, _ = _make_console()
    transcript = TerminalTranscript(console)
    transcript.push_message(ChatMessage(kind=MessageKind.USER, sender="you", body="x"))
    transcript._truncated_blocks = [
        ToolEvent(tool_name="bash", args={}, content="x", call_id="call-1")
    ]
    transcript._live_narrated_call_ids = {"call-1"}

    transcript, out, should_exit = asyncio.run(
        _dispatch("/new", runtime=runtime, overlay=overlay, transcript=transcript)
    )

    assert should_exit is False
    assert runtime.created == ["focus-new-001"]
    assert transcript._messages == []
    assert transcript._truncated_blocks == []
    assert transcript._live_narrated_call_ids == set()
    assert "started new session" in out


def test_close_closes_session_and_clears_transcript() -> None:
    runtime = _FakeRuntime()
    overlay = _StubOverlay(None)
    console, _ = _make_console()
    transcript = TerminalTranscript(console)
    transcript.push_message(ChatMessage(kind=MessageKind.USER, sender="you", body="x"))

    transcript, out, should_exit = asyncio.run(
        _dispatch("/close", runtime=runtime, overlay=overlay, transcript=transcript)
    )

    assert should_exit is False
    assert runtime.closed == ["focus-live"]
    assert transcript._messages == []
    assert "closed session: focus-live" in out


def test_resume_filters_to_non_empty_sessions_and_reloads_history() -> None:
    runtime = _FakeRuntime()
    overlay = _StubOverlay("focus-live")

    transcript, out, should_exit = asyncio.run(
        _dispatch("/resume", runtime=runtime, overlay=overlay)
    )

    assert should_exit is False
    assert overlay.presented == [["focus-live"]]
    assert runtime.bound == ["focus-live"]
    assert [msg.body for msg in transcript._messages] == ["earlier", "done"]
    assert "resumed session: focus-live" in out


def test_resume_with_no_non_empty_sessions_surfaces_guidance() -> None:
    runtime = _FakeRuntime()
    runtime._directory_sessions = [
        _SessionRecord(id="focus-empty", label="empty", message_count=0)
    ]
    overlay = _StubOverlay("focus-empty")

    _, out, _ = asyncio.run(_dispatch("/resume", runtime=runtime, overlay=overlay))

    assert "no prior sessions with messages" in out.lower()
    assert overlay.presented == []
    assert runtime.bound == []


def test_resume_cancel_keeps_existing_state() -> None:
    runtime = _FakeRuntime()
    overlay = _StubOverlay(None)
    console, _ = _make_console()
    transcript = TerminalTranscript(console)
    transcript.push_message(
        ChatMessage(kind=MessageKind.USER, sender="you", body="keep")
    )

    transcript, out, _ = asyncio.run(
        _dispatch("/resume", runtime=runtime, overlay=overlay, transcript=transcript)
    )

    assert runtime.bound == []
    assert [msg.body for msg in transcript._messages] == ["keep"]
    assert "resumed session" not in out.lower()


def test_permission_posture_survives_process_boundary_and_new_session_resets() -> None:
    sessions = _PermissionSessions()
    first = _permission_runtime(sessions)
    first.set_permission_posture(
        permission_mode="auto",
        action_policy_mode="auto",
        permission_overrides={"file.copy": "auto"},
    )

    reconstructed = _permission_runtime(sessions)
    reconstructed.bind_session("focus-existing")
    assert reconstructed.permission_mode == "auto"
    assert reconstructed.action_policy_mode_override == "auto"
    assert reconstructed.permission_overrides == {"file.copy": "auto"}

    new_session_id = reconstructed.create_new_session()
    assert new_session_id != "focus-existing"
    assert reconstructed.permission_mode == "default"
    assert reconstructed.action_policy_mode_override == ""
    assert reconstructed.permission_overrides == {}


def test_historical_malformed_permission_posture_restores_safe_defaults() -> None:
    runtime = _permission_runtime(_PermissionSessions())

    runtime.bind_session("focus-historical")

    assert runtime.permission_mode == "default"
    assert runtime.action_policy_mode_override == ""
    assert runtime.permission_overrides == {}
    assert (
        "Malformed saved permission posture" in runtime._permission_posture_diagnostic
    )


def test_new_and_historical_sessions_use_configured_permission_defaults() -> None:
    configured = RunProfileOverrides(
        permission_mode="readonly",
        permission_overrides=(("file.write", "readonly"),),
    )
    sessions = _PermissionSessions()
    runtime = _permission_runtime(sessions, configured)

    runtime.bind_session("focus-existing")
    assert runtime.permission_mode == "readonly"
    assert runtime.action_policy_mode_override == ""
    assert runtime.permission_overrides == {"file.write": "readonly"}

    runtime.create_new_session()
    assert runtime.permission_mode == "readonly"
    assert runtime.action_policy_mode_override == ""
    assert runtime.permission_overrides == {"file.write": "readonly"}


def test_configured_auto_and_bypass_restore_matching_action_policy_modes() -> None:
    for permission_mode in ("auto", "bypass"):
        sessions = _PermissionSessions()
        runtime = _permission_runtime(
            sessions,
            RunProfileOverrides(permission_mode=permission_mode),
        )

        runtime.bind_session("focus-existing")
        assert runtime.permission_mode == permission_mode
        assert runtime.action_policy_mode_override == permission_mode

        new_session_id = runtime.create_new_session()
        assert runtime.permission_mode == permission_mode
        assert runtime.action_policy_mode_override == permission_mode
        assert (
            sessions.records[new_session_id].metadata[
                ACTION_POLICY_SESSION_OVERRIDE_KEY
            ]
            == permission_mode
        )
