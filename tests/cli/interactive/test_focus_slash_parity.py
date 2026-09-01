from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from openminion.cli.presentation import styles
from openminion.cli.interactive.commands import SlashCommandMixin
from openminion.cli.theme import DARK
from openminion.cli.interactive.app import FocusApp, _DemoFocusRuntime
from openminion.cli.presentation.models import MessageKind
from openminion.cli.interactive.widgets import FocusTranscript
from openminion.modules.telemetry.schemas import TelemetryEvent
from openminion.modules.telemetry.service import TelemetryService


@pytest.fixture(autouse=True)
def _restore_active_theme():
    original_codes = dict(styles._ANSI_CODES)
    original_name = styles.get_active_theme_name()
    styles.set_active_theme(DARK)
    yield
    styles._ANSI_CODES.clear()
    styles._ANSI_CODES.update(original_codes)
    styles._ACTIVE_THEME_NAME = original_name


def _last_system_body(chat: FocusTranscript) -> str:
    for msg in reversed(chat._messages):
        if msg.kind == MessageKind.SYSTEM:
            return str(msg.body)
    return ""


@pytest.mark.asyncio
async def test_help_lists_every_registered_command_including_fcpp_06_additions() -> (
    None
):
    with tempfile.TemporaryDirectory() as tmp:
        runtime = _DemoFocusRuntime(working_dir=tmp, session="help-test")
        app = FocusApp(runtime=runtime, working_dir=tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            screen._handle_command("/help")
            await pilot.pause()
            chat = screen.query_one(FocusTranscript)
            body = _last_system_body(chat)
    for cmd in (
        "/help",
        "/compact",
        "/cost",
        "/tokens",
        "/mcp",
        "/model",
        "/resume",
        "/theme",
        "/sessions",
        "/agent",
        "/tools",
        "/clear",
        "/new",
        "/details",
        "/export",
        "/editor",
        "/exit",
    ):
        assert cmd in body, f"`{cmd}` missing from /help cheat-sheet"


@pytest.mark.asyncio
async def test_model_command_renders_provider_and_model() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runtime = _DemoFocusRuntime(working_dir=tmp, session="model-test")
        app = FocusApp(runtime=runtime, working_dir=tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            screen._handle_command("/model")
            await pilot.pause()
            body = _last_system_body(screen.query_one(FocusTranscript))
    assert "echo" in body
    assert "demo" in body
    assert "current" in body.lower()


@pytest.mark.asyncio
async def test_cost_command_surfaces_no_usage_when_runtime_has_none() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runtime = _DemoFocusRuntime(working_dir=tmp, session="cost-test")
        app = FocusApp(runtime=runtime, working_dir=tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.screen._handle_command("/cost")
            await pilot.pause()
            body = _last_system_body(app.screen.query_one(FocusTranscript))
    assert "no token" in body.lower() or "no usage" in body.lower()


@pytest.mark.asyncio
async def test_details_command_toggles_focus_transcript_verbosity() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runtime = _DemoFocusRuntime(working_dir=tmp, session="details-test")
        app = FocusApp(runtime=runtime, working_dir=tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            screen._handle_command("/details")
            await pilot.pause()
            chat = screen.query_one(FocusTranscript)
            first = _last_system_body(chat)
            assert chat.verbosity == "verbose"
            screen._handle_command("/details off")
            await pilot.pause()
            second = _last_system_body(chat)
    assert "details on" in first
    assert "details off" in second


@pytest.mark.asyncio
async def test_export_and_editor_commands_surface_guidance() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runtime = _DemoFocusRuntime(working_dir=tmp, session="export-test")
        app = FocusApp(runtime=runtime, working_dir=tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.screen._handle_command("/export")
            await pilot.pause()
            export_body = _last_system_body(app.screen.query_one(FocusTranscript))
            app.screen._handle_command("/editor")
            await pilot.pause()
            editor_body = _last_system_body(app.screen.query_one(FocusTranscript))
    assert "openminion export transcript --session-id export-test" in export_body
    assert "External-editor" in editor_body


@pytest.mark.asyncio
async def test_cost_command_renders_usage_when_runtime_has_data() -> None:
    from openminion.cli.status.token_usage import TokenUsageSnapshot

    snap = TokenUsageSnapshot(
        turn_total_tokens=42,
        session_total_tokens=1234,
        context_used_tokens=5000,
        context_limit_tokens=200000,
    )

    class _UsageRuntime(_DemoFocusRuntime):
        def token_usage_snapshot(self):
            return snap

    with tempfile.TemporaryDirectory() as tmp:
        runtime = _UsageRuntime(working_dir=tmp, session="cost-data-test")
        app = FocusApp(runtime=runtime, working_dir=tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.screen._handle_command("/cost")
            await pilot.pause()
            body = _last_system_body(app.screen.query_one(FocusTranscript))
    assert "1234" in body
    assert "42" in body
    assert "5000" in body
    assert "200000" in body
    assert "cost" in body
    assert "unavailable" in body


@pytest.mark.asyncio
async def test_tokens_command_renders_durable_usage_report() -> None:
    class _UsageRuntime(_DemoFocusRuntime):
        def token_usage_report(self) -> str:
            return "status tokens: session=usage-test\ntotals: provider=42"

    with tempfile.TemporaryDirectory() as tmp:
        runtime = _UsageRuntime(working_dir=tmp, session="usage-test")
        app = FocusApp(runtime=runtime, working_dir=tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.screen._handle_command("/tokens")
            await pilot.pause()
            body = _last_system_body(app.screen.query_one(FocusTranscript))

    assert "session=usage-test" in body
    assert "provider=42" in body


@pytest.mark.asyncio
async def test_mcp_command_renders_status_report() -> None:
    class _MCPRuntime(_DemoFocusRuntime):
        def mcp_status_report(self) -> str:
            return "MCP servers:\n- fixture  [ready]  transport=stdio  tools=1"

    with tempfile.TemporaryDirectory() as tmp:
        runtime = _MCPRuntime(working_dir=tmp, session="mcp-test")
        app = FocusApp(runtime=runtime, working_dir=tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.screen._handle_command("/mcp")
            await pilot.pause()
            body = _last_system_body(app.screen.query_one(FocusTranscript))
    assert "MCP servers:" in body
    assert "fixture" in body


@pytest.mark.asyncio
async def test_memory_command_renders_capture_health() -> None:
    class _MemoryRuntime(_DemoFocusRuntime):
        def memory_report(self) -> str:
            return (
                "Memory:\n"
                "  capture     3 eligible · 1 pending · 2 terminal\n"
                "  terminal    1 processed · 1 no output · 0 rejected · 0 failed\n"
                "  integrity   0 errors\n"
                "  recall      healthy · mode shadow\n"
                "  capability  keyword, graph, vector\n"
                "  score       hybrid-semantic-v1\n"
                "  selected    memory 2 · knowledge 1\n"
                "  omissions   budget 1 · relevance 2"
            )

    with tempfile.TemporaryDirectory() as tmp:
        runtime = _MemoryRuntime(working_dir=tmp, session="memory-test")
        app = FocusApp(runtime=runtime, working_dir=tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.screen._handle_command("/memory")
            await pilot.pause()
            body = _last_system_body(app.screen.query_one(FocusTranscript))

    assert "1 pending" in body
    assert "2 terminal" in body
    assert "1 no output" in body
    assert "recall      healthy · mode shadow" in body
    assert "selected    memory 2 · knowledge 1" in body
    assert "omissions   budget 1 · relevance 2" in body


@pytest.mark.asyncio
async def test_graph_command_surfaces_viewer_commands() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runtime = _DemoFocusRuntime(working_dir=tmp, session="graph-test")
        app = FocusApp(runtime=runtime, working_dir=tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.screen._handle_command("/graph current --node-kind fact")
            await pilot.pause()
            body = _last_system_body(app.screen.query_one(FocusTranscript))

    assert "openminion graph view --current --node-kind fact" in body


@pytest.mark.asyncio
async def test_compact_command_surfaces_not_supported_when_hook_missing() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runtime = _DemoFocusRuntime(working_dir=tmp, session="compact-test")
        app = FocusApp(runtime=runtime, working_dir=tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.screen._handle_command("/compact")
            await pilot.pause()
            body = _last_system_body(app.screen.query_one(FocusTranscript))
    assert "not supported" in body.lower()


@pytest.mark.asyncio
async def test_compact_command_calls_hook_when_present() -> None:

    class _CompactingRuntime(_DemoFocusRuntime):
        compact_called = False

        def compact_history(self):
            type(self).compact_called = True
            return {"session_total_tokens": 100}

    with tempfile.TemporaryDirectory() as tmp:
        runtime = _CompactingRuntime(working_dir=tmp, session="compact-ok-test")
        app = FocusApp(runtime=runtime, working_dir=tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.screen._handle_command("/compact")
            await pilot.pause()
            body = _last_system_body(app.screen.query_one(FocusTranscript))
    assert _CompactingRuntime.compact_called is True
    assert "compacted" in body.lower()
    assert "100" in body


@pytest.mark.asyncio
async def test_compact_command_surfaces_bounded_failure_on_exception() -> None:

    class _BrokenRuntime(_DemoFocusRuntime):
        def compact_history(self):
            raise RuntimeError("compaction explosion")

    with tempfile.TemporaryDirectory() as tmp:
        runtime = _BrokenRuntime(working_dir=tmp, session="compact-broken-test")
        app = FocusApp(runtime=runtime, working_dir=tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.screen._handle_command("/compact")
            await pilot.pause()
            body = _last_system_body(app.screen.query_one(FocusTranscript))
    assert "failed" in body.lower()
    assert "explosion" in body


@pytest.mark.asyncio
async def test_resume_surfaces_guidance_when_no_prior_sessions() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runtime = _DemoFocusRuntime(working_dir=tmp, session="resume-empty-test")
        app = FocusApp(runtime=runtime, working_dir=tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.screen._handle_command("/resume")
            await pilot.pause()
            body = _last_system_body(app.screen.query_one(FocusTranscript))
    assert "no prior sessions" in body.lower() or "/new" in body


@pytest.mark.asyncio
async def test_resume_filters_to_non_empty_sessions() -> None:
    forwarded: list = []

    class _SessionsRuntime(_DemoFocusRuntime):
        def list_directory_sessions(self, *, limit: int = 20):
            del limit
            return [
                SimpleNamespace(id="empty-1", message_count=0),
                SimpleNamespace(id="real-1", message_count=5),
                SimpleNamespace(id="empty-2", message_count=0),
                SimpleNamespace(id="real-2", message_count=12),
            ]

    with tempfile.TemporaryDirectory() as tmp:
        runtime = _SessionsRuntime(working_dir=tmp, session="resume-filter-test")
        app = FocusApp(runtime=runtime, working_dir=tmp)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            # Capture what the picker receives by intercepting
            # `_open_session_picker`.
            screen._open_session_picker = lambda sessions: forwarded.extend(sessions)  # type: ignore[method-assign]
            screen._handle_command("/resume")
            await pilot.pause()
    ids = [getattr(s, "id", "") for s in forwarded]
    assert "real-1" in ids
    assert "real-2" in ids
    assert "empty-1" not in ids
    assert "empty-2" not in ids


class _TelemetrySlashHarness(SlashCommandMixin):
    def __init__(self, data_root: Path) -> None:
        self._runtime = SimpleNamespace(
            api_runtime=SimpleNamespace(data_root=data_root)
        )
        self.messages: list[str] = []

    def _push_system_body(self, body: str) -> None:
        self.messages.append(body)


def _record_focus_telemetry_invocation(data_root: Path) -> None:
    service = TelemetryService(
        data_root / "telemetry" / "telemetry.db",
        env={
            "OPENMINION_HOME": str(data_root),
            "OPENMINION_DATA_ROOT": str(data_root),
        },
    )
    service.record_event_sync(
        TelemetryEvent(
            session_id="focus-session",
            turn_id="focus-turn",
            invocation_id="focus-invocation",
            agent_id="focus-agent",
            event_type="agent.invocation.started",
            timestamp=1.0,
            event_id="focus-start",
        )
    )
    service.record_event_sync(
        TelemetryEvent(
            session_id="focus-session",
            turn_id="focus-turn",
            invocation_id="focus-invocation",
            agent_id="focus-agent",
            event_type="agent.invocation.completed",
            timestamp=2.0,
            event_id="focus-completed",
            data={"status": "completed"},
        )
    )
    service.close_sync()


def test_focus_telemetry_uses_slash_actions_and_labels_shell_command(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / ".openminion"
    _record_focus_telemetry_invocation(data_root)
    harness = _TelemetrySlashHarness(data_root)

    harness._handle_command("/telemetry")

    body = harness.messages[-1]
    assert "next: /telemetry failed | /telemetry invocation focus-invocation" in body
    assert "shell: telemetryctl debug bundle focus-invocation" in body
    assert "next: telemetryctl" not in body


def test_focus_telemetry_and_trace_errors_stay_local(tmp_path: Path) -> None:
    harness = _TelemetrySlashHarness(tmp_path)

    harness._handle_command("/telemetry invocation")
    harness._handle_command("/trace list --limit 0")

    assert harness.messages[0].startswith("usage: /telemetry")
    assert harness.messages[1].startswith("usage: /trace")
    assert list(tmp_path.iterdir()) == []
