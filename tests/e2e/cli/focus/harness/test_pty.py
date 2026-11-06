from __future__ import annotations

from pathlib import Path

import pytest

from tests.e2e.cli.focus.harness.pty import PtySession, _ForkProcess

pytestmark = pytest.mark.e2e


@pytest.fixture
def terminal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PtySession:
    session = PtySession(argv=(), cwd=tmp_path, rows=24, cols=100)
    session._master_fd = 123
    session._process = _ForkProcess(pid=456)
    monkeypatch.setattr(PtySession, "_set_window_size", lambda *args: None)
    monkeypatch.setattr(PtySession, "_read_available", lambda *args, **kwargs: None)
    monkeypatch.setattr("tests.e2e.cli.focus.harness.pty.os.kill", lambda *args: None)
    transcript = "\r\n".join(f"transcript {row}" for row in range(1, 13))
    session._consume_output(
        (transcript + "\r\n❯ draft\r\n◆ status\x1b[1A\r\x1b[2C").encode()
    )
    assert session.cursor_position == (13, 3)
    return session


@pytest.mark.parametrize("pause_after", ["shrink", "expand"])
def test_resize_keeps_cursor_with_composer_during_split_redraw(
    terminal: PtySession, pause_after: str
) -> None:
    terminal.resize(rows=18, cols=72)
    if pause_after == "expand":
        terminal._consume_output(b"\x1b[2D\x1b[J")
        terminal._consume_output("❯ draft\r\n◆ status\x1b[1A\r\x1b[2C".encode())
        terminal.resize(rows=42, cols=120)

    # prompt-toolkit flushes erasure separately from the replacement composer.
    terminal._consume_output(b"\x1b[2D\x1b[J")
    assert not any(row.startswith(("❯", "◆")) for row in terminal.screen_lines)
    terminal._consume_output("❯ draft\r\n◆ status\x1b[1A\r\x1b[2C".encode())
    rows = tuple(row for row in terminal.screen_lines if row)
    assert rows == tuple(f"transcript {row}" for row in range(7, 13)) + (
        "❯ draft",
        "◆ status",
    )
    assert terminal.cursor_position == (len(rows) - 1, 3)


@pytest.mark.parametrize(
    "rows, cols, cursor", [(18, 72, (7, 3)), (42, 120, (13, 3)), (24, 100, (13, 3))]
)
def test_resize_refreshes_visible_snapshot_without_child_output(
    terminal: PtySession, rows: int, cols: int, cursor: tuple[int, int]
) -> None:
    terminal.resize(rows=rows, cols=cols)
    assert terminal.cursor_position == cursor
    assert len(terminal.screen_lines) == rows
    assert terminal.screen_text == "\n".join(terminal.screen_lines).rstrip()
    assert terminal.visible_transcript.endswith(terminal.screen_text)


def test_resize_clamps_cursor_when_its_row_and_column_are_clipped(
    terminal: PtySession,
) -> None:
    terminal._consume_output(b"\x1b[2;95H")
    terminal.resize(rows=18, cols=72)
    assert terminal.cursor_position == (1, 72)


def test_screen_snapshot_does_not_mix_repaint_frames(terminal, monkeypatch):
    reads = []

    def read(session, *, timeout):
        reads.append(timeout)
        if len(reads) == 2:
            session._consume_output(b"\x1b[2D\x1b[J")

    monkeypatch.setattr(PtySession, "_read_available", read)
    rows, cursor = terminal.screen_snapshot
    assert rows[12:14] == ("❯ draft", "◆ status")
    assert cursor == (13, 3)
    assert reads == [0.05]
    assert not any(row.startswith("◆") for row in terminal.screen_lines)


def test_layout_asserts_accepted_frame_before_next_erase(terminal, monkeypatch):
    from tests.e2e.cli.focus.test_local import _assert_flow_layout

    reads = []

    def read(session, *, timeout):
        reads.append(timeout)
        if len(reads) > 1:
            session._consume_output(b"\x1b[2D\x1b[J")

    monkeypatch.setattr(PtySession, "_read_available", read)
    _assert_flow_layout(terminal, rows=24)
    assert reads == [0.05, 0.05]
    assert not any(row.startswith("◆") for row in terminal._screen_display_lines())


@pytest.mark.parametrize("fault", ["footer", "prompt", "cursor", "height"])
def test_layout_rejects_persistent_invalid_frame(terminal, monkeypatch, fault):
    from tests.e2e.cli.focus import test_local

    if fault == "footer":
        terminal._consume_output(b"\x1b[14;1H\x1b[2K")
    elif fault == "prompt":
        terminal._consume_output(b"\x1b[13;1H\x1b[2K")
    elif fault == "cursor":
        terminal._consume_output(b"\x1b[1;1H")
    ticks = iter([0.0, 0.0, 6.0])
    monkeypatch.setattr(test_local.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(test_local.time, "sleep", lambda _: None)
    with pytest.raises(AssertionError):
        test_local._assert_flow_layout(terminal, rows=18 if fault == "height" else 24)


def test_layout_waits_for_complete_frame_after_erase(terminal, monkeypatch):
    from tests.e2e.cli.focus import test_local

    terminal._consume_output(b"\x1b[2D\x1b[J")
    reads = []

    def read(session, *, timeout):
        reads.append(timeout)
        if len(reads) == 2:
            session._consume_output("❯ draft\r\n◆ status\x1b[1A\r\x1b[2C".encode())

    monkeypatch.setattr(PtySession, "_read_available", read)
    monkeypatch.setattr(test_local.time, "sleep", lambda _: None)
    test_local._assert_flow_layout(terminal, rows=24)
    assert reads == [0.05, 0.05, 0.05]
