from __future__ import annotations

from pathlib import Path
import re
from typing import Callable

from .assertions import (
    assert_expected_markers,
    assert_focus_turn_completed,
    assert_no_terminal_crash,
)
from .pty import PtySession
from .scenarios import FocusScenario

_PROMPT_RE = re.compile(r"❯|Ask anything|/ for commands")
_DONE_RE = re.compile(r"\bDone in \d+(?:m\d{2}s|s)\b")
_APPROVAL_RE = re.compile(r"Reply exactly yes to confirm|Policy confirmation required")
_TURN_EVENT_RE = re.compile(
    r"Reply exactly yes to confirm|Policy confirmation required|\bDone in \d+(?:m\d{2}s|s)\b"
)


class FocusProbe:
    def __init__(
        self,
        *,
        python_bin: Path,
        openminion_root: Path,
        framework_root: Path,
        data_root: Path,
        config_path: Path,
        agent_id: str,
        workdir: Path,
    ) -> None:
        self.python_bin = python_bin
        self.openminion_root = openminion_root
        self.framework_root = framework_root
        self.data_root = data_root
        self.config_path = config_path
        self.agent_id = agent_id
        self.workdir = workdir

    def for_workdir(self, workdir: Path) -> "FocusProbe":
        return FocusProbe(
            python_bin=self.python_bin,
            openminion_root=self.openminion_root,
            framework_root=self.framework_root,
            data_root=self.data_root,
            config_path=self.config_path,
            agent_id=self.agent_id,
            workdir=workdir,
        )

    def command(self) -> tuple[str, ...]:
        return (
            str(self.python_bin),
            "-m",
            "openminion",
            "--config",
            str(self.config_path),
            "focus",
            "--agent",
            self.agent_id,
            "--dir",
            str(self.workdir),
            "--terminal",
            "--no-update-check",
            "--progress",
            "minimal",
        )

    def environment(self) -> dict[str, str]:
        return {
            "OPENMINION_HOME": str(self.framework_root),
            "OPENMINION_DATA_ROOT": str(self.data_root),
            "PYTHONPATH": "src",
            "OPENMINION_SHOW_RESPONSE_TIME": "1",
            "OPENMINION_FOCUS_BACKEND": "terminal",
            "PYTHONDONTWRITEBYTECODE": "1",
        }

    def session(
        self,
        *,
        rows: int = 42,
        cols: int = 140,
        on_transcript_update: Callable[[str], None] | None = None,
    ) -> PtySession:
        return PtySession(
            argv=self.command(),
            cwd=self.openminion_root,
            env=self.environment(),
            rows=rows,
            cols=cols,
            on_transcript_update=on_transcript_update,
        )

    def wait_ready(self, session: PtySession) -> str:
        transcript = session.wait_for(_PROMPT_RE, timeout=60)
        assert_no_terminal_crash(transcript)
        return transcript

    def run_slash(self, session: PtySession, command: str, *, marker: str) -> str:
        session.type_line(command)
        transcript = session.wait_for(re.escape(marker), timeout=60)
        assert_no_terminal_crash(transcript)
        return transcript

    def run_turn(self, session: PtySession, scenario: FocusScenario) -> str:
        turn_offset = len(session.transcript)
        session.type_line(scenario.prompt)
        wait_offset = turn_offset
        approvals = 0
        while True:
            match = session.wait_for_match_after(
                _TURN_EVENT_RE,
                offset=wait_offset,
                timeout=scenario.timeout,
            )
            transcript = session.transcript
            turn_slice = transcript[wait_offset:]
            if _DONE_RE.fullmatch(match.group(0)) and not _APPROVAL_RE.search(
                turn_slice
            ):
                break
            if not _APPROVAL_RE.search(turn_slice):
                wait_offset = turn_offset
                continue
            assert scenario.requires_approval, transcript[-2000:]
            approvals += 1
            assert approvals <= scenario.max_auto_approvals, transcript[-2000:]
            wait_offset = len(transcript)
            session.type_line(scenario.approval_reply)
        final_turn_slice = session.transcript[turn_offset:]
        assert_focus_turn_completed(turn_slice)
        assert_expected_markers(
            final_turn_slice, scenario.prompt, scenario.expected_markers
        )
        return transcript
