from __future__ import annotations

from hashlib import sha1
from pathlib import Path
import re
import time
from typing import Callable

from .assertions import (
    assert_expected_markers,
    assert_focus_turn_completed,
    assert_no_terminal_crash,
    visible_text,
)
from .pty import PtySession
from .scenarios import FocusScenario

_COMPOSER_READY_RE = re.compile(
    r"Ask anything|Reply, or / for commands|input:\s*(?:send|queue next) message|"
    r"(?:^|\n)\s*❯\s*\Z"
)
_INLINE_APPROVAL_RE = re.compile(
    r"\[A\]\s*Allow once\s+\[S\]\s*Session allow\s+\[D\]\s*Deny"
)
_DONE_RE = re.compile(r"\bDone in \d+(?:m\d{2}s|s)\b")
_APPROVAL_PROMPT_PATTERN = (
    r"Policy confirmation required|Reply exactly yes to (?:allow once|confirm)|"
    r"session to allow this tool"
)
_APPROVAL_PATTERN = rf"{_APPROVAL_PROMPT_PATTERN}|Waiting for your reply"
_APPROVAL_PROMPT_RE = re.compile(_APPROVAL_PROMPT_PATTERN)
_APPROVAL_RE = re.compile(_APPROVAL_PATTERN)
_TURN_EVENT_RE = re.compile(rf"{_APPROVAL_PATTERN}|\bDone in \d+(?:m\d{{2}}s|s)\b")
_APPROVAL_RESOLVED_RE = re.compile(
    r"(?:^|\n)\s*(?:[❯>]\s*)?(?:yes|session|no)\s*(?:\n|$)|"
    r"(?:Approved\.|Approval denied\.)"
)
_ACTIVE_TURN_STATUS_RE = re.compile(
    r"(?:thinking…|responding\s*\||Analyzing request\.\.\.|Working\.\.\.)",
    re.IGNORECASE,
)
_COMPOSER_ECHO_PROBE_LENGTH = 48
_TRAILING_PUNCTUATION = ".,;:!?"


def _visible_offset(text: str, *, offset: int) -> int:
    return len(visible_text(text[:offset]))


def latest_turn_event(transcript: str, *, offset: int) -> re.Match[str] | None:
    visible_offset = _visible_offset(transcript, offset=offset)
    transcript = visible_text(transcript)
    match: re.Match[str] | None = None
    for match in _TURN_EVENT_RE.finditer(transcript, visible_offset):
        pass
    return match


def latest_done_event(transcript: str, *, offset: int) -> re.Match[str] | None:
    visible_offset = _visible_offset(transcript, offset=offset)
    transcript = visible_text(transcript)
    match: re.Match[str] | None = None
    for match in _DONE_RE.finditer(transcript, visible_offset):
        pass
    return match


def latest_approval_prompt(transcript: str, *, offset: int) -> re.Match[str] | None:
    visible_offset = _visible_offset(transcript, offset=offset)
    transcript = visible_text(transcript)
    match: re.Match[str] | None = None
    for match in _APPROVAL_PROMPT_RE.finditer(transcript, visible_offset):
        pass
    return match


def approval_prompt_needs_reply(transcript: str, *, offset: int) -> bool:
    visible_offset = _visible_offset(transcript, offset=offset)
    transcript = visible_text(transcript)
    approval_match = latest_approval_prompt(transcript, offset=visible_offset)
    if approval_match is None:
        return False
    after_prompt = transcript[approval_match.end() :]
    if _APPROVAL_RESOLVED_RE.search(after_prompt):
        return False
    return True


def active_approval_visible(screen_text: str) -> bool:
    return approval_prompt_needs_reply(screen_text, offset=0)


def active_turn_busy(screen_text: str) -> bool:
    """Return whether the current screen still shows a live turn status."""
    visible_lines = [line for line in screen_text.splitlines() if line.strip()]
    return _ACTIVE_TURN_STATUS_RE.search("\n".join(visible_lines[-8:])) is not None


def composer_echo_probe(text: str) -> str:
    """Return the text tail that remains visible in a one-line composer."""
    return text[-_COMPOSER_ECHO_PROBE_LENGTH:]


def screen_after_submission(screen_text: str, submission_probe: str) -> str | None:
    """Return screen content rendered after the latest submitted input."""
    trimmed_probe = submission_probe.rstrip(_TRAILING_PUNCTUATION)
    words = trimmed_probe.split()
    if not words:
        return None
    pattern_text = r"\s+".join(re.escape(word) for word in words)
    punctuation = submission_probe[len(trimmed_probe) :]
    if punctuation:
        pattern_text += rf"(?:\s*{re.escape(punctuation)})?"
    pattern = re.compile(pattern_text)
    matches = list(pattern.finditer(screen_text))
    if not matches:
        return None
    return screen_text[matches[-1].end() :]


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
        session_id: str,
        include_project_context: bool = True,
    ) -> None:
        self.python_bin = python_bin
        self.openminion_root = openminion_root
        self.framework_root = framework_root
        self.data_root = data_root
        self.config_path = config_path
        self.agent_id = agent_id
        self.workdir = workdir
        self.session_id = session_id
        self.include_project_context = include_project_context

    def for_workdir(
        self,
        workdir: Path,
        *,
        include_project_context: bool | None = None,
    ) -> "FocusProbe":
        return FocusProbe(
            python_bin=self.python_bin,
            openminion_root=self.openminion_root,
            framework_root=self.framework_root,
            data_root=self.data_root,
            config_path=self.config_path,
            agent_id=self.agent_id,
            workdir=workdir,
            session_id=self.session_id,
            include_project_context=(
                self.include_project_context
                if include_project_context is None
                else include_project_context
            ),
        )

    def command(self) -> tuple[str, ...]:
        command = (
            str(self.python_bin),
            "-m",
            "openminion",
            "--config",
            str(self.config_path),
            "focus",
            "--agent",
            self.agent_id,
            "--session",
            self.session_id,
            "--dir",
            str(self.workdir),
            "--no-update-check",
            "--progress",
            "minimal",
        )
        if not self.include_project_context:
            command += ("--no-context",)
        return command

    def environment(self) -> dict[str, str]:
        return {
            "OPENMINION_HOME": str(self.framework_root),
            "OPENMINION_DATA_ROOT": str(self.data_root),
            "PYTHONPATH": "src",
            "OPENMINION_SHOW_RESPONSE_TIME": "1",
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
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            transcript = session.transcript
            if _COMPOSER_READY_RE.search(session.screen_text):
                assert_no_terminal_crash(transcript)
                return transcript
            time.sleep(0.05)
        transcript = session.transcript
        raise AssertionError(
            "timed out waiting for the enabled Focus composer\n"
            f"{visible_text(transcript)[-2000:]}"
        )

    def run_slash(self, session: PtySession, command: str, *, marker: str) -> str:
        offset = len(session.transcript)
        session.send(command)
        time.sleep(0.1)
        session.send("\r")
        transcript = session.wait_for_after(
            re.escape(marker), offset=offset, timeout=60
        )
        assert_no_terminal_crash(transcript)
        return transcript

    @staticmethod
    def _wait_for_composer(session: PtySession, *, timeout: float = 15.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if _COMPOSER_READY_RE.search(session.screen_text):
                return
            time.sleep(0.05)
        raise AssertionError(
            "timed out waiting for the Focus composer to accept input\n"
            f"{session.screen_text[-2000:]}"
        )

    @classmethod
    def _submit_composer_line(cls, session: PtySession, text: str) -> str:
        """Submit through the composer only after Textual exposes an input state."""
        cls._wait_for_composer(session)
        session.send(text)
        echo_probe = composer_echo_probe(text)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if screen_after_submission(session.screen_text, echo_probe) is not None:
                composer_screen = session.screen_text
                session.send("\r")
                break
            time.sleep(0.05)
        else:
            raise AssertionError(
                f"Focus composer did not echo submitted text {text!r}\n"
                f"{session.screen_text[-2000:]}"
            )

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            screen_text = session.screen_text
            if (
                screen_text != composer_screen
                and screen_after_submission(screen_text, echo_probe) is not None
            ):
                return echo_probe
            time.sleep(0.05)
        raise AssertionError(
            f"Focus did not render submitted text {text!r}\n"
            f"{session.screen_text[-2000:]}"
        )

    @staticmethod
    def _submit_inline_approval(session: PtySession, reply: str) -> None:
        decision = str(reply or "").strip().lower()
        key = {"yes": "a", "session": "s", "no": "d"}.get(decision)
        if key is None:
            raise AssertionError(f"unsupported approval reply: {reply!r}")
        approval_screen = session.screen_text
        session.send(key)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            screen_text = session.screen_text
            if (
                screen_text != approval_screen
                and _INLINE_APPROVAL_RE.search(screen_text) is None
            ):
                return
            time.sleep(0.05)
        raise AssertionError(
            f"Focus inline approval did not resolve\n{session.screen_text[-2000:]}"
        )

    def run_turn(self, session: PtySession, scenario: FocusScenario) -> str:
        turn_offset = len(session.visible_transcript)
        self._submit_composer_line(session, scenario.prompt)
        event_offset = len(session.visible_transcript)
        approvals = 0
        deadline = time.monotonic() + scenario.timeout
        while time.monotonic() < deadline:
            time.sleep(0.1)
            transcript = session.visible_transcript
            screen_text = session.screen_text
            done_match = latest_done_event(transcript, offset=event_offset)
            approval_needs_reply = approval_prompt_needs_reply(
                transcript,
                offset=event_offset,
            )
            approval_visible = active_approval_visible(screen_text)
            inline_approval_visible = (
                _INLINE_APPROVAL_RE.search(screen_text) is not None
            )
            if inline_approval_visible:
                assert scenario.requires_approval, transcript[-2000:]
                approvals += 1
                assert approvals <= scenario.max_auto_approvals, transcript[-2000:]
                self._submit_inline_approval(session, scenario.approval_reply)
                event_offset = len(session.visible_transcript)
                continue
            if approval_needs_reply or approval_visible:
                assert scenario.requires_approval, transcript[-2000:]
                approvals += 1
                assert approvals <= scenario.max_auto_approvals, transcript[-2000:]
                self._submit_composer_line(session, scenario.approval_reply)
                event_offset = len(session.visible_transcript)
                continue
            if (
                done_match is not None
                and not approval_visible
                and not active_turn_busy(screen_text)
            ):
                break
        else:
            raise AssertionError(
                "timed out waiting for the current Focus turn to complete\n"
                f"{session.screen_text[-2000:]}"
            )
        final_turn_slice = session.visible_transcript[turn_offset:]
        assert_focus_turn_completed(final_turn_slice)
        assert_expected_markers(
            final_turn_slice, scenario.prompt, scenario.expected_markers
        )
        return final_turn_slice


def focus_session_id(*, data_root: Path, node_name: str) -> str:
    digest = sha1(str(data_root).encode("utf-8")).hexdigest()[:12]
    label = re.sub(r"[^A-Za-z0-9-]+", "-", node_name).strip("-")[:48]
    return f"focus-e2e-{label or 'session'}-{digest}"
