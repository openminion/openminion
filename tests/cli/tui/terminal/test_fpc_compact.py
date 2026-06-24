from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console

from openminion.cli.tui.providers.runtime import OpenMinionRuntime


class _StubAPIRuntime:
    def __init__(self) -> None:
        self.config = SimpleNamespace(
            runtime=SimpleNamespace(
                session_keep_recent_messages=20,
                session_max_compact_per_turn=100,
                session_summary_max_chars=8000,
                session_archive_enabled=True,
                session_archive_ref_limit=3,
                session_context_token_budget=0,
                session_context_chars_per_token=4.0,
                session_summary_enrichment_enabled=False,
            ),
            providers=SimpleNamespace(
                anthropic=SimpleNamespace(model="claude-3-5-sonnet-latest"),
                openai=SimpleNamespace(model="gpt-4.1-mini"),
                openrouter=SimpleNamespace(model="openai/gpt-4.1-mini"),
                cerebras=SimpleNamespace(model="gpt-oss-120b"),
                groq=SimpleNamespace(model="llama-3.1-70b"),
                ollama=SimpleNamespace(model="llama3"),
                cortensor=SimpleNamespace(model="cortensor-default"),
            ),
            agents={
                "default-agent": SimpleNamespace(
                    name="default-agent",
                    provider="anthropic",
                    model="claude-3-5-sonnet-latest",
                    default_channel="cli",
                )
            },
        )
        self.sessions = MagicMock(name="SessionStore")
        self.logger = MagicMock(name="Logger")
        self.config_path = "/tmp/config.yaml"
        self.storage_path = Path("/tmp/storage")
        self.memory_root = Path("/tmp/memory")
        self.data_root = Path("/tmp/data")
        self.retrieve_ctl = None

    def resolve_agent_profile(self, agent_id=None, overrides=None):
        return self.config.agents.get("default-agent")


def _make_runtime(
    *, bound: bool = True, session_id: str = "sess-1"
) -> OpenMinionRuntime:
    rt = OpenMinionRuntime.__new__(OpenMinionRuntime)
    rt._rt = _StubAPIRuntime()
    rt._agent_id_override = "default-agent"
    rt._agent_id = "default-agent"
    rt._channel = "cli"
    rt._target = "tui"
    rt._history_limit = 200
    rt._working_dir = ""
    rt._gateway = object()
    rt._session_id = session_id if bound else None
    rt._prompt_on_resume = False
    rt._project_context = None
    rt._project_context_pending = False
    rt._model_override_provider = ""
    rt._model_override_model = ""
    rt._pending_candidate_session = None
    # Token usage plumbing (FPP/FLE) — minimal stub.
    rt._completed_session_usage = SimpleNamespace(
        total_tokens=0, prompt_tokens=0, completion_tokens=0
    )
    rt._last_turn_usage = SimpleNamespace(
        total_tokens=0, prompt_tokens=0, completion_tokens=0
    )
    rt._current_turn_usage = None
    rt._current_turn_has_live_deltas = False
    rt._current_turn_started_at_monotonic = None
    rt._last_turn_elapsed_seconds = None
    rt._usage_updated_at_monotonic = None
    rt._last_live_usage_update_at = None
    return rt


# ── Unbound session ─────────────────────────────────────────────


def test_compact_history_returns_no_session_when_unbound() -> None:
    rt = _make_runtime(bound=False)
    result = rt.compact_history()
    assert result["reason"] == "no_session"
    assert result["compacted_count"] == 0
    assert result["summary_updated"] is False


def test_compact_history_returns_no_session_when_session_id_empty() -> None:
    rt = _make_runtime(bound=True, session_id="")
    result = rt.compact_history()
    assert result["reason"] == "no_session"


# ── Backend integration via factory mock ────────────────────────


def test_compact_history_delegates_to_session_context_service() -> None:
    rt = _make_runtime()

    fake_result = SimpleNamespace(
        compacted_count=5,
        summary_updated=True,
        archive_relative_path="archives/2026-05-23/sess-1.jsonl",
    )
    fake_service = MagicMock()
    fake_service.compact_session.return_value = fake_result

    with patch(
        "openminion.services.runtime.bootstrap.build_session_context_service",
        return_value=fake_service,
    ):
        result = rt.compact_history()

    fake_service.compact_session.assert_called_once_with(session_id="sess-1")
    assert result["compacted_count"] == 5
    assert result["summary_updated"] is True
    assert result["archive_relative_path"] == "archives/2026-05-23/sess-1.jsonl"


def test_compact_history_zero_count_when_nothing_to_compact() -> None:
    rt = _make_runtime()

    fake_result = SimpleNamespace(
        compacted_count=0,
        summary_updated=False,
        archive_relative_path="",
    )
    fake_service = MagicMock()
    fake_service.compact_session.return_value = fake_result

    with patch(
        "openminion.services.runtime.bootstrap.build_session_context_service",
        return_value=fake_service,
    ):
        result = rt.compact_history()

    assert result["compacted_count"] == 0
    assert result["summary_updated"] is False


def test_compact_history_propagates_service_exception() -> None:
    rt = _make_runtime()

    fake_service = MagicMock()
    fake_service.compact_session.side_effect = RuntimeError("compaction backend boom")

    with patch(
        "openminion.services.runtime.bootstrap.build_session_context_service",
        return_value=fake_service,
    ):
        with pytest.raises(RuntimeError, match="compaction backend boom"):
            rt.compact_history()


# ── Builder integration ─────────────────────────────────────────


def test_compact_history_uses_factory_with_api_runtime_inputs() -> None:
    rt = _make_runtime()

    fake_result = SimpleNamespace(
        compacted_count=2, summary_updated=True, archive_relative_path=""
    )
    fake_service = MagicMock()
    fake_service.compact_session.return_value = fake_result

    with patch(
        "openminion.services.runtime.bootstrap.build_session_context_service",
        return_value=fake_service,
    ) as factory_mock:
        rt.compact_history()

    factory_mock.assert_called_once()
    kwargs = factory_mock.call_args.kwargs
    assert kwargs["config"] is rt._rt.config
    assert kwargs["sessions"] is rt._rt.sessions
    assert kwargs["storage_path"] == rt._rt.storage_path
    assert kwargs["data_root"] == rt._rt.data_root


# ── Terminal-flow /compact slash dispatch ───────────────────────


def test_terminal_flow_compact_slash_in_catalog() -> None:
    from openminion.cli.tui.terminal.shell import _SLASH_COMMANDS

    assert "/compact" in _SLASH_COMMANDS


def test_terminal_flow_compact_renders_nothing_to_compact() -> None:
    import asyncio
    from openminion.cli.tui.terminal.shell import _handle_slash

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120)

    runtime = SimpleNamespace(
        compact_history=lambda: {
            "compacted_count": 0,
            "summary_updated": False,
            "archive_relative_path": "",
        }
    )

    asyncio.run(
        _handle_slash(
            "/compact",
            runtime=runtime,
            console=console,
            transcript=MagicMock(),
            overlay=MagicMock(),
            status_line=MagicMock(),
            working_dir="/tmp",
        )
    )
    assert "nothing to compact" in buf.getvalue().lower()


def test_terminal_flow_compact_renders_count_and_token_total() -> None:
    import asyncio
    from openminion.cli.tui.terminal.shell import _handle_slash

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120)

    runtime = SimpleNamespace(
        compact_history=lambda: {
            "compacted_count": 7,
            "summary_updated": True,
            "archive_relative_path": "",
            "session_total_tokens": 12345,
        }
    )

    asyncio.run(
        _handle_slash(
            "/compact",
            runtime=runtime,
            console=console,
            transcript=MagicMock(),
            overlay=MagicMock(),
            status_line=MagicMock(),
            working_dir="/tmp",
        )
    )
    out = buf.getvalue()
    assert "compacted 7 turns" in out
    assert "12345" in out


def test_terminal_flow_compact_surfaces_no_session_reason() -> None:
    import asyncio
    from openminion.cli.tui.terminal.shell import _handle_slash

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120)

    runtime = SimpleNamespace(
        compact_history=lambda: {
            "compacted_count": 0,
            "summary_updated": False,
            "reason": "no_session",
        }
    )

    asyncio.run(
        _handle_slash(
            "/compact",
            runtime=runtime,
            console=console,
            transcript=MagicMock(),
            overlay=MagicMock(),
            status_line=MagicMock(),
            working_dir="/tmp",
        )
    )
    assert "no active session" in buf.getvalue().lower()


def test_terminal_flow_compact_handles_runtime_without_method() -> None:
    import asyncio
    from openminion.cli.tui.terminal.shell import _handle_slash

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120)

    runtime = SimpleNamespace()  # no compact_history attribute

    asyncio.run(
        _handle_slash(
            "/compact",
            runtime=runtime,
            console=console,
            transcript=MagicMock(),
            overlay=MagicMock(),
            status_line=MagicMock(),
            working_dir="/tmp",
        )
    )
    assert "compact_history" in buf.getvalue()


def test_terminal_flow_compact_handles_exception_from_runtime() -> None:
    import asyncio
    from openminion.cli.tui.terminal.shell import _handle_slash

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120)

    def _raise() -> dict[str, Any]:
        raise RuntimeError("backend down")

    runtime = SimpleNamespace(compact_history=_raise)

    asyncio.run(
        _handle_slash(
            "/compact",
            runtime=runtime,
            console=console,
            transcript=MagicMock(),
            overlay=MagicMock(),
            status_line=MagicMock(),
            working_dir="/tmp",
        )
    )
    out = buf.getvalue()
    assert "/compact: error" in out
    assert "backend down" in out
