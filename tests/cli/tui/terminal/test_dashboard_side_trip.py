from __future__ import annotations

import asyncio
import io
from unittest.mock import patch

from rich.console import Console

from openminion.cli.tui.terminal.shell import _open_dashboard_side_trip
from openminion.cli.tui.terminal.transcript import TerminalTranscript
from openminion.cli.tui.presentation.models import MessageKind


class _FakeAPIRuntime:
    def __init__(self) -> None:
        self.agent_id = "alpha"
        self.session_id = "s1"
        self.provider_name = "openai"
        self.model_name = "gpt-4.1-mini"
        self.transport = "gateway"
        self.closed = False

    def close(self) -> None:
        self.closed = True

    async def send_message(self, text):
        yield f"reply to: {text}"


def _make_transcript() -> tuple[TerminalTranscript, io.StringIO]:
    buf = io.StringIO()
    return TerminalTranscript(Console(file=buf, force_terminal=False, width=80)), buf


def test_dashboard_side_trip_calls_launch_with_owns_runtime_false() -> None:
    captured = {}

    def _capture_launch(*, app_runtime, providers, owns_runtime, **kwargs):
        captured["owns_runtime"] = owns_runtime
        captured["app_runtime"] = app_runtime
        captured["providers"] = providers
        return 0

    transcript, _ = _make_transcript()
    runtime = _FakeAPIRuntime()
    with (
        patch("openminion.cli.commands.tui.launch_dashboard", _capture_launch),
        patch(
            "openminion.cli.tui.providers.runtime.OpenMinionRuntime",
            lambda r, **kw: ("wrapped", r),
        ),
        patch(
            "openminion.cli.parser.contracts.ProviderBundle.from_api_runtime",
            classmethod(lambda cls, r: ("bundle", r)),
        ),
    ):
        asyncio.run(
            _open_dashboard_side_trip(
                runtime=runtime, console=transcript._console, transcript=transcript
            )
        )
    assert captured.get("owns_runtime") is False, (
        "side-trip MUST pass owns_runtime=False so the helper does not "
        "close the borrowed APIRuntime"
    )
    # The wrapper carries our runtime as the second tuple element.
    assert captured["app_runtime"][1] is runtime


def test_dashboard_side_trip_uses_underlying_api_runtime_when_present() -> None:
    captured = {}

    class _Wrapper:
        def __init__(self, base) -> None:
            self.api_runtime = base

    base_runtime = _FakeAPIRuntime()

    def _capture_launch(*, app_runtime, providers, owns_runtime, **kwargs):
        captured["owns_runtime"] = owns_runtime
        captured["app_runtime"] = app_runtime
        captured["providers"] = providers
        return 0

    transcript, _ = _make_transcript()
    with (
        patch("openminion.cli.commands.tui.launch_dashboard", _capture_launch),
        patch(
            "openminion.cli.tui.providers.runtime.OpenMinionRuntime",
            lambda r, **kw: ("wrapped", r),
        ),
        patch(
            "openminion.cli.parser.contracts.ProviderBundle.from_api_runtime",
            classmethod(lambda cls, r: ("bundle", r)),
        ),
    ):
        asyncio.run(
            _open_dashboard_side_trip(
                runtime=_Wrapper(base_runtime),
                console=transcript._console,
                transcript=transcript,
            )
        )
    assert captured["app_runtime"][1] is base_runtime
    assert captured["providers"][1] is base_runtime


def test_dashboard_side_trip_helper_exception_surfaces_inline() -> None:
    def _failing_launch(**kwargs):
        raise RuntimeError("dashboard mount failed")

    transcript, _ = _make_transcript()
    runtime = _FakeAPIRuntime()
    with patch("openminion.cli.commands.tui.launch_dashboard", _failing_launch):
        asyncio.run(
            _open_dashboard_side_trip(
                runtime=runtime, console=transcript._console, transcript=transcript
            )
        )
    system = [m for m in transcript._messages if m.kind == MessageKind.SYSTEM]
    assert system, "helper failure must surface as inline SYSTEM message"
    assert "Dashboard side-trip not available" in system[-1].body
    # Runtime was NOT closed.
    assert runtime.closed is False


def test_borrowed_runtime_remains_usable_after_side_trip() -> None:
    transcript, _ = _make_transcript()
    runtime = _FakeAPIRuntime()

    def _ok_launch(**kwargs):
        return 0

    with (
        patch("openminion.cli.commands.tui.launch_dashboard", _ok_launch),
        patch(
            "openminion.cli.tui.providers.runtime.OpenMinionRuntime",
            lambda r, **kw: ("wrapped", r),
        ),
        patch(
            "openminion.cli.parser.contracts.ProviderBundle.from_api_runtime",
            classmethod(lambda cls, r: ("bundle", r)),
        ),
    ):
        asyncio.run(
            _open_dashboard_side_trip(
                runtime=runtime, console=transcript._console, transcript=transcript
            )
        )
    # Runtime was NOT closed — we can keep using it.
    assert runtime.closed is False


def test_dashboard_side_trip_announces_launch_in_transcript() -> None:
    transcript, buf = _make_transcript()
    runtime = _FakeAPIRuntime()
    with (
        patch("openminion.cli.commands.tui.launch_dashboard", lambda **kw: 0),
        patch(
            "openminion.cli.tui.providers.runtime.OpenMinionRuntime",
            lambda r, **kw: r,
        ),
        patch(
            "openminion.cli.parser.contracts.ProviderBundle.from_api_runtime",
            classmethod(lambda cls, r: r),
        ),
    ):
        asyncio.run(
            _open_dashboard_side_trip(
                runtime=runtime, console=transcript._console, transcript=transcript
            )
        )
    assert "launching dashboard" in buf.getvalue()
