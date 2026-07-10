from __future__ import annotations

from pathlib import Path
import re
import time

from tests.e2e.tui.focus.harness import PtySession


_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _plain(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _fake_session(openminion_root: Path, *, cols: int = 140) -> PtySession:
    return PtySession(
        argv=(
            str(openminion_root / ".venv" / "bin" / "python3.11"),
            "tests/e2e/tui/focus/fake_terminal_focus_app.py",
        ),
        cwd=openminion_root,
        env={
            "PYTHONPATH": "src",
            "PYTHONDONTWRITEBYTECODE": "1",
            "OPENMINION_SHOW_RESPONSE_TIME": "1",
            "OPENMINION_FOCUS_BACKEND": "terminal",
        },
        rows=36,
        cols=cols,
    )


def test_fake_terminal_focus_typeahead_progress_and_fifo(openminion_root: Path) -> None:
    with _fake_session(openminion_root) as session:
        session.wait_for("❯", timeout=20)
        session.type_line("slow queue")
        time.sleep(0.6)
        session.type_line("queued follow-up")

        transcript = session.wait_for(
            re.escape("Queued message (1 pending)."), timeout=5
        )
        transcript = session.wait_for("first reply complete", timeout=10)
        transcript = session.wait_for("queued reply complete", timeout=10)
        plain = _plain(transcript)

        assert plain.count("slow queue") == 1
        normalized = plain.replace("\r", "")
        assert normalized.count("❯ queued follow-up") == 1
        assert "slow queue\n\n❯" in normalized
        assert re.search(r"Done in \d+s\n\n", normalized)
        assert plain.find("first reply complete") < plain.find("queued reply complete")
        assert "Queued message (1 pending)." in plain
        assert "Running queued message: queued follow-up" in plain
        assert "type to queue › Loading memory context" not in plain
        assert "type to queue › Analyzing request" not in plain
        assert "esc interrupts · type to queue" not in plain
        assert not re.search(r"type to queue\s*›\s*[✻⠂]", plain), plain[-2000:]


def test_fake_terminal_focus_interrupt_preserves_queue(openminion_root: Path) -> None:
    with _fake_session(openminion_root) as session:
        session.wait_for("❯", timeout=20)
        session.type_line("slow interrupt")
        time.sleep(0.6)
        session.type_line("queued follow-up")
        session.wait_for(re.escape("Queued message (1 pending)."), timeout=5)
        session.send("\x1b")

        transcript = session.wait_for("Preserved 1 queued message", timeout=10)
        time.sleep(0.3)
        assert "queued reply complete" not in _plain(session.transcript)

        session.type_line("/queue")
        transcript = session.wait_for("1. queued follow-up", timeout=5)
        session.type_line("/queue run-next")
        transcript = session.wait_for("queued reply complete", timeout=10)
        plain = _plain(transcript)

        assert "Interrupted current turn." in plain
        assert "Preserved 1 queued message" in plain
        assert plain.count("queued follow-up") >= 1


def test_fake_terminal_focus_collapses_repeated_tool_events(
    openminion_root: Path,
) -> None:
    with _fake_session(openminion_root) as session:
        session.wait_for("❯", timeout=20)
        turn_offset = len(session.transcript)
        session.type_line("repeat tools")

        transcript = session.wait_for("repeat tool proof complete", timeout=10)
        plain = _plain(transcript[turn_offset:])
        normalized = " ".join(plain.split())

        assert plain.count("tool_budget_calls_exceeded") == 1
        assert "2 repeated tool results collapsed" in plain
        assert "web.search(MSFT stock) ×1" in plain
        assert "web.search(MSFT stock) failed ×1" in normalized
