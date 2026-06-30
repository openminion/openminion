from __future__ import annotations

from pathlib import Path
import re

from .assertions import assert_focus_turn_completed, assert_no_terminal_crash
from .pty import PtySession
from .scenarios import FocusScenario

_PROMPT_RE = re.compile(r"❯|Ask anything|/ for commands")
_DONE_RE = re.compile(r"\bDone in \d+(?:m\d{2}s|s)\b")
_APPROVAL_RE = re.compile(r"Reply exactly yes to confirm|Policy confirmation required")


class FocusProbe:
    def __init__(
        self,
        *,
        python_bin: Path,
        openminion_root: Path,
        framework_root: Path,
        config_path: Path,
        agent_id: str,
        workdir: Path,
    ) -> None:
        self.python_bin = python_bin
        self.openminion_root = openminion_root
        self.framework_root = framework_root
        self.config_path = config_path
        self.agent_id = agent_id
        self.workdir = workdir

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
            "OPENMINION_DATA_ROOT": str(self.framework_root / ".openminion"),
            "PYTHONPATH": "src",
            "OPENMINION_SHOW_RESPONSE_TIME": "1",
            "OPENMINION_FOCUS_BACKEND": "terminal",
            "PYTHONDONTWRITEBYTECODE": "1",
        }

    def session(self, *, rows: int = 42, cols: int = 140) -> PtySession:
        return PtySession(
            argv=self.command(),
            cwd=self.openminion_root,
            env=self.environment(),
            rows=rows,
            cols=cols,
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
        session.type_line(scenario.prompt)
        transcript = session.wait_for(_DONE_RE, timeout=scenario.timeout)
        if scenario.requires_approval and _APPROVAL_RE.search(transcript):
            session.type_line("yes")
            transcript = session.wait_for(_DONE_RE, timeout=scenario.timeout)
        assert_focus_turn_completed(transcript)
        visible = transcript.lower()
        for marker in scenario.expected_markers:
            assert marker.lower() in visible, marker
        return transcript
