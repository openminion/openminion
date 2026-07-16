from __future__ import annotations

import asyncio
import io

from rich.console import Console

from openminion.cli.interactive.terminal.shell import _open_dashboard_side_trip
from openminion.cli.interactive.terminal.transcript import TerminalTranscript
from openminion.cli.presentation.models import MessageKind


def _make_transcript() -> tuple[TerminalTranscript, io.StringIO]:
    buf = io.StringIO()
    return TerminalTranscript(Console(file=buf, force_terminal=False, width=80)), buf


def test_dashboard_side_trip_shows_retirement_notice(monkeypatch) -> None:
    transcript, _ = _make_transcript()
    events: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "openminion.cli.status.surface.record_surface_event",
        lambda _runtime, *, surface, action: events.append((surface, action)),
    )
    asyncio.run(
        _open_dashboard_side_trip(
            runtime=object(), console=transcript._console, transcript=transcript
        )
    )
    system = [m for m in transcript._messages if m.kind == MessageKind.SYSTEM]
    assert len(system) == 1
    assert "dashboard was retired" in system[0].body
    assert "bare `openminion`" in system[0].body
    assert events == [("dashboard", "deprecation")]
