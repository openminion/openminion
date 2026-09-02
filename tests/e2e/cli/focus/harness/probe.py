from __future__ import annotations

from hashlib import sha256
import json
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
_CONTENT_COMPOSER_RE = re.compile(r"Ask anything|Reply, or / for commands")
_LEGACY_INLINE_APPROVAL_RE = re.compile(
    r"\[A\]\s*Allow once\s+\[S\]\s*Session allow\s+\[D\]\s*Deny"
)
_COMPACT_INLINE_APPROVAL_RE = re.compile(
    r"\[y\]es\s*/\s*\[N\]o\s*/\s*\[a\]lways:",
    re.IGNORECASE,
)
_DONE_RE = re.compile(r"\bDone in \d+(?:m\d{2}s|s)\b")
_APPROVAL_PROMPT_PATTERN = (
    r"Policy confirmation required|High-risk action requires confirmation|"
    r"Reply exactly yes to (?:allow once|confirm)"
)
_SIDECAR_CONSENT_RE = re.compile(
    r"(?:Allow auto-start for PinchTab|Allow [a-z_ -]+ for sidecar '[^']+')\? "
    r"\[y/N\]:\s*$",
    re.IGNORECASE,
)
_APPROVAL_PATTERN = rf"{_APPROVAL_PROMPT_PATTERN}|Waiting for your reply"
_APPROVAL_PROMPT_RE = re.compile(_APPROVAL_PROMPT_PATTERN)
_APPROVAL_RE = re.compile(_APPROVAL_PATTERN)
_TURN_EVENT_RE = re.compile(rf"{_APPROVAL_PATTERN}|\bDone in \d+(?:m\d{{2}}s|s)\b")
_TERMINAL_FAILURE_RE = re.compile(
    r"EMPTY_PROVIDER_RESPONSE:|\bLLM error:\s*(?:PROVIDER_ERROR|EMPTY_PROVIDER_RESPONSE)",
    re.IGNORECASE,
)
_CONTINUATION_CUE_RE = re.compile(
    r"Continue\s+in\s+a\s+new\s+turn\s+to\s+resume\.",
    re.IGNORECASE,
)
_APPROVAL_RESOLVED_RE = re.compile(
    r"(?:^|\n)\s*(?:[❯>]\s*)?(?:yes|session|a|always|no)\s*(?:\n|$)|"
    r"(?:Approved\.|Approval denied\.)"
)
_ACTIVE_TURN_STATUS_RE = re.compile(
    r"(?:thinking…|responding\s*\||Analyzing request\.\.\.|Working\.\.\.)",
    re.IGNORECASE,
)
_COMPOSER_ECHO_PROBE_LENGTH = 48
_TRAILING_PUNCTUATION = ".,;:!?"


def _config_uses_echo_agent(config_path: Path, agent_id: str) -> bool:
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    agents = payload.get("agents")
    if not isinstance(agents, dict):
        return False
    agent = agents.get(agent_id)
    if not isinstance(agent, dict):
        return False
    return str(agent.get("provider", "") or "").strip() == "echo"


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


def latest_done_after_submission(
    transcript: str,
    submission_probe: str,
) -> re.Match[str] | None:
    """Return completion rendered after the latest submitted composer input."""
    trailing = screen_after_submission(transcript, submission_probe)
    if trailing is None:
        return None
    match: re.Match[str] | None = None
    for match in _DONE_RE.finditer(trailing):
        pass
    return match


def latest_terminal_failure(transcript: str, *, offset: int) -> re.Match[str] | None:
    visible_offset = _visible_offset(transcript, offset=offset)
    return _TERMINAL_FAILURE_RE.search(visible_text(transcript), visible_offset)


def continuation_cue_present(transcript: str) -> bool:
    return _CONTINUATION_CUE_RE.search(visible_text(transcript)) is not None


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
    return (
        inline_approval_menu(screen_text) is not None
        or sidecar_consent_prompt_visible(screen_text)
        or approval_prompt_needs_reply(screen_text, offset=0)
    )


def sidecar_consent_prompt_visible(screen_text: str) -> bool:
    return _SIDECAR_CONSENT_RE.search(screen_text) is not None


def inline_approval_menu(screen_text: str) -> str | None:
    compact_matches = [
        match
        for match in _COMPACT_INLINE_APPROVAL_RE.finditer(screen_text)
        if not _compact_approval_answered(screen_text, match=match)
    ]
    if compact_matches:
        latest_compact = compact_matches[-1]
        if (
            _compact_approval_inline_status_follows(
                screen_text,
                match=latest_compact,
            )
            or _compact_approval_active_tool_follows(
                screen_text,
                match=latest_compact,
            )
            or not _interactive_surface_follows(
                screen_text, offset=latest_compact.end()
            )
        ):
            return "compact"
    legacy_matches = list(_LEGACY_INLINE_APPROVAL_RE.finditer(screen_text))
    if legacy_matches and not _interactive_surface_follows(
        screen_text, offset=legacy_matches[-1].end()
    ):
        return "legacy"
    return None


def _compact_approval_answered(
    screen_text: str,
    *,
    match: re.Match[str],
) -> bool:
    trailing = screen_text[match.end() :]
    return (
        re.search(
            r"(?:^|\r?\n)[ \t]*(?:y|yes|a|always|n|no)(?:[ \t]*\r?\n|[ \t]*$)",
            trailing,
            re.IGNORECASE,
        )
        is not None
    )


def _compact_approval_submitted(
    screen_text: str,
    *,
    match: re.Match[str],
) -> bool:
    trailing = screen_text[match.end() :]
    return (
        re.search(
            r"(?:^|\r?\n)[ \t]*(?:y|yes|a|always|n|no)[ \t]*\r?\n",
            trailing,
            re.IGNORECASE,
        )
        is not None
    )


def _compact_approval_inline_status_follows(
    screen_text: str,
    *,
    match: re.Match[str],
) -> bool:
    trailing = screen_text[match.end() :]
    return re.match(r"[ \t]+(?:●|•)\s+Running\b", trailing) is not None


def _compact_approval_active_tool_follows(
    screen_text: str,
    *,
    match: re.Match[str],
) -> bool:
    trailing = screen_text[match.end() :]
    tool_statuses = list(
        re.finditer(
            r"(?:^|\n)\s*(?:❯\s*)?(?:●|•)\s+(?P<running>Running\b)?",
            trailing,
        )
    )
    return bool(tool_statuses and tool_statuses[-1].group("running"))


def inline_approval_fingerprint(screen_text: str) -> str | None:
    """Identify the active approval generation without relying on redraw count."""
    menu = inline_approval_menu(screen_text)
    if menu is None:
        return None
    pattern = (
        _COMPACT_INLINE_APPROVAL_RE if menu == "compact" else _LEGACY_INLINE_APPROVAL_RE
    )
    matches = list(pattern.finditer(screen_text))
    prefix = screen_text[: matches[-1].start()]
    prompt_lines = [line.strip() for line in prefix.splitlines() if line.strip()]
    prompt = prompt_lines[-1] if prompt_lines else ""
    prompt = _COMPACT_INLINE_APPROVAL_RE.sub("", prompt).strip()
    prompt = _LEGACY_INLINE_APPROVAL_RE.sub("", prompt).strip()
    return f"{menu}:{prompt}"


def _interactive_surface_follows(screen_text: str, *, offset: int) -> bool:
    trailing = screen_text[offset:]
    return bool(
        _CONTENT_COMPOSER_RE.search(trailing)
        or _DONE_RE.search(trailing)
        or re.search(r"(?:^|\n)Delegation:", trailing)
        or _COMPACT_INLINE_APPROVAL_RE.search(trailing)
        or _LEGACY_INLINE_APPROVAL_RE.search(trailing)
        or re.search(r"(?:^|\n)\s*(?:❯\s*)?●\s", trailing)
    )


def inline_approval_key(screen_text: str, reply: str) -> str:
    menu = inline_approval_menu(screen_text)
    decision = str(reply or "").strip().lower()
    keys = {
        "compact": {"yes": "yes", "session": "a", "no": "no"},
        "legacy": {"yes": "a", "session": "s", "no": "d"},
    }
    key = keys.get(menu or "", {}).get(decision)
    if key is None:
        raise AssertionError(
            f"unsupported approval reply {reply!r} for {menu or 'unknown'} menu"
        )
    return key


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
    characters = [character for character in trimmed_probe if not character.isspace()]
    if not characters:
        return None
    pattern_text = r"\s*".join(re.escape(character) for character in characters)
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

    def for_session(self, session_id: str) -> "FocusProbe":
        return FocusProbe(
            python_bin=self.python_bin,
            openminion_root=self.openminion_root,
            framework_root=self.framework_root,
            data_root=self.data_root,
            config_path=self.config_path,
            agent_id=self.agent_id,
            workdir=self.workdir,
            session_id=session_id,
            include_project_context=self.include_project_context,
        )

    def uses_echo_agent(self) -> bool:
        return _config_uses_echo_agent(self.config_path, self.agent_id)

    def command(self) -> tuple[str, ...]:
        command = (
            str(self.python_bin),
            "-m",
            "openminion",
            "--config",
            str(self.config_path),
            "--agent",
            self.agent_id,
            "--session",
            self.session_id,
            "--dir",
            str(self.workdir),
            "--no-update-check",
            "--allow-unsandboxed-exec",
            "--progress",
            "minimal",
        )
        if self.uses_echo_agent():
            command += ("--demo",)
        if not self.include_project_context:
            command += ("--no-context",)
        return command

    def environment(self) -> dict[str, str]:
        home_root = self.data_root.parent / "home-roots" / self.session_id
        return {
            "OPENMINION_HOME": str(home_root),
            "OPENMINION_DATA_ROOT": str(self.data_root),
            "OPENMINION_GENERATED_ROOT": str(self.data_root / "runtime"),
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

    def run_slash_turn(
        self,
        session: PtySession,
        command: str,
        *,
        marker: str | None,
        timeout: int = 240,
        requires_approval: bool = False,
        max_auto_approvals: int = 5,
        approval_reply: str = "yes",
    ) -> str:
        """Run a slash command through the same approval loop as a prompt turn."""
        turn_offset = len(session.visible_transcript)
        session.send(command)
        time.sleep(0.1)
        session.send("\r")
        event_offset = len(session.visible_transcript)
        approvals = 0
        marker_re = re.compile(marker) if marker is not None else None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            time.sleep(0.1)
            transcript = session.visible_transcript
            screen_text = session.screen_text
            approval_needs_reply = approval_prompt_needs_reply(
                transcript,
                offset=event_offset,
            )
            approval_visible = active_approval_visible(screen_text)
            inline_approval_visible = inline_approval_menu(screen_text) is not None
            sidecar_consent_visible = sidecar_consent_prompt_visible(screen_text)
            if sidecar_consent_visible:
                assert requires_approval, transcript[-2000:]
                approvals += 1
                assert approvals <= max_auto_approvals, transcript[-2000:]
                self._submit_sidecar_consent(session, approval_reply)
                event_offset = len(session.visible_transcript)
                continue
            if inline_approval_visible:
                assert requires_approval, transcript[-2000:]
                approvals += 1
                assert approvals <= max_auto_approvals, transcript[-2000:]
                self._submit_inline_approval(session, approval_reply)
                event_offset = len(session.visible_transcript)
                continue
            if approval_needs_reply or approval_visible:
                assert requires_approval, transcript[-2000:]
                approvals += 1
                assert approvals <= max_auto_approvals, transcript[-2000:]
                self._submit_composer_line(session, approval_reply)
                event_offset = len(session.visible_transcript)
                continue
            marker_seen = marker_re is None or marker_re.search(
                transcript[turn_offset:]
            )
            approval_satisfied = not requires_approval or approvals > 0
            if (
                marker_seen
                and approval_satisfied
                and _COMPOSER_READY_RE.search(screen_text)
                and not active_turn_busy(screen_text)
            ):
                break
        else:
            marker_label = "<composer-ready>" if marker is None else repr(marker)
            raise AssertionError(
                f"timed out waiting for slash command marker {marker_label}\n"
                f"{session.screen_text[-2000:]}"
            )
        transcript = session.visible_transcript[turn_offset:]
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
        if "\n" in text or "\r" in text:
            session.send_bracketed_paste(text)
        else:
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
        approval_screen = session.screen_text
        approval_fingerprint = inline_approval_fingerprint(approval_screen)
        menu = inline_approval_menu(approval_screen)
        if menu == "compact":
            stable_polls = 0
            settle_deadline = time.monotonic() + 3.0
            while time.monotonic() < settle_deadline:
                time.sleep(0.05)
                current_screen = session.screen_text
                current_fingerprint = inline_approval_fingerprint(current_screen)
                if "Approval required:" in str(current_fingerprint or ""):
                    approval_screen = current_screen
                    approval_fingerprint = current_fingerprint
                    break
                if current_screen == approval_screen:
                    stable_polls += 1
                    if stable_polls >= 3 and "Approval required:" in str(
                        approval_fingerprint or ""
                    ):
                        break
                else:
                    approval_screen = current_screen
                    approval_fingerprint = inline_approval_fingerprint(approval_screen)
                    stable_polls = 0
            approval_fingerprint = inline_approval_fingerprint(approval_screen)
            menu = inline_approval_menu(approval_screen)
        key = inline_approval_key(approval_screen, reply)
        if menu == "compact":
            session.send(f"{key}\r")
        else:
            session.send(key)
        deadline = time.monotonic() + 5.0
        clear_polls = 0
        while time.monotonic() < deadline:
            screen_text = session.screen_text
            visible_transcript = session.visible_transcript
            if menu == "compact":
                screen_matches = list(_COMPACT_INLINE_APPROVAL_RE.finditer(screen_text))
                if screen_matches and _compact_approval_answered(
                    screen_text,
                    match=screen_matches[-1],
                ):
                    return
                compact_matches = list(
                    _COMPACT_INLINE_APPROVAL_RE.finditer(visible_transcript)
                )
                if (
                    compact_matches
                    and _compact_approval_submitted(
                        visible_transcript,
                        match=compact_matches[-1],
                    )
                    and _interactive_surface_follows(
                        visible_transcript,
                        offset=compact_matches[-1].end(),
                    )
                ):
                    return
            current_fingerprint = inline_approval_fingerprint(screen_text)
            if current_fingerprint is None:
                if menu == "compact":
                    compact_matches = list(
                        _COMPACT_INLINE_APPROVAL_RE.finditer(screen_text)
                    )
                    if (
                        compact_matches
                        and not _interactive_surface_follows(
                            screen_text,
                            offset=compact_matches[-1].end(),
                        )
                        and not _compact_approval_submitted(
                            screen_text,
                            match=compact_matches[-1],
                        )
                    ):
                        clear_polls = 0
                        time.sleep(0.05)
                        continue
                clear_polls += 1
                if clear_polls >= 3:
                    return
            else:
                clear_polls = 0
                if current_fingerprint != approval_fingerprint:
                    return
            time.sleep(0.05)
        raise AssertionError(
            f"Focus inline approval did not resolve\n{session.screen_text[-2000:]}"
        )

    @staticmethod
    def _submit_sidecar_consent(session: PtySession, reply: str) -> None:
        decision = str(reply or "").strip().lower()
        session.send("n\r" if decision in {"no", "deny", "denied"} else "y\r")
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if not sidecar_consent_prompt_visible(session.screen_text):
                return
            time.sleep(0.05)
        raise AssertionError(
            f"Focus sidecar consent did not resolve\n{session.screen_text[-2000:]}"
        )

    def run_turn(self, session: PtySession, scenario: FocusScenario) -> str:
        turn_offset = len(session.visible_transcript)
        self._submit_composer_line(session, scenario.prompt)
        event_offset = len(session.visible_transcript)
        approvals = 0
        continuations = 0
        completion_probe: str | None = None
        deadline = time.monotonic() + scenario.timeout
        while time.monotonic() < deadline:
            time.sleep(0.1)
            transcript = session.visible_transcript
            screen_text = session.screen_text
            done_match = (
                latest_done_after_submission(transcript, completion_probe)
                if completion_probe is not None
                else latest_done_event(transcript, offset=event_offset)
            )
            failure_match = latest_terminal_failure(transcript, offset=event_offset)
            approval_needs_reply = approval_prompt_needs_reply(
                transcript,
                offset=event_offset,
            )
            approval_visible = active_approval_visible(screen_text)
            inline_approval_visible = inline_approval_menu(screen_text) is not None
            sidecar_consent_visible = sidecar_consent_prompt_visible(screen_text)
            if sidecar_consent_visible:
                assert scenario.requires_approval, transcript[-2000:]
                approvals += 1
                assert approvals <= scenario.max_auto_approvals, transcript[-2000:]
                self._submit_sidecar_consent(session, scenario.approval_reply)
                event_offset = len(session.visible_transcript)
                continue
            if inline_approval_visible:
                assert scenario.requires_approval, transcript[-2000:]
                approvals += 1
                assert approvals <= scenario.max_auto_approvals, transcript[-2000:]
                self._submit_inline_approval(session, scenario.approval_reply)
                approval_probe = composer_echo_probe(scenario.approval_reply)
                completion_probe = (
                    approval_probe
                    if screen_after_submission(session.screen_text, approval_probe)
                    is not None
                    else None
                )
                event_offset = len(session.visible_transcript)
                continue
            if approval_needs_reply or approval_visible:
                assert scenario.requires_approval, transcript[-2000:]
                approvals += 1
                assert approvals <= scenario.max_auto_approvals, transcript[-2000:]
                completion_probe = self._submit_composer_line(
                    session, scenario.approval_reply
                )
                event_offset = len(session.visible_transcript)
                continue
            if failure_match is not None:
                failure_slice = visible_text(transcript)[failure_match.start() :]
                raise AssertionError(
                    "Focus turn ended with a terminal provider failure\n"
                    f"{failure_slice[-2000:]}"
                )
            if done_match is not None and not approval_visible:
                completed_segment = transcript[event_offset:]
                if (
                    continuation_cue_present(completed_segment)
                    and continuations < scenario.max_auto_continuations
                ):
                    continuations += 1
                    completion_probe = self._submit_composer_line(
                        session,
                        "continue",
                    )
                    event_offset = len(session.visible_transcript)
                    deadline = time.monotonic() + scenario.timeout
                    continue
                break
        else:
            raise AssertionError(
                "timed out waiting for the current Focus turn to complete\n"
                f"{session.screen_text[-2000:]}"
            )
        final_turn_slice = session.visible_transcript[turn_offset:]
        assert_focus_turn_completed(final_turn_slice)
        if not self.uses_echo_agent():
            assert_expected_markers(
                final_turn_slice, scenario.prompt, scenario.expected_markers
            )
        return final_turn_slice


def focus_session_id(*, data_root: Path, node_name: str) -> str:
    digest = sha256(str(data_root).encode("utf-8")).hexdigest()[:32]
    label = re.sub(r"[^A-Za-z0-9-]+", "-", node_name).strip("-")[:48]
    return f"focus-e2e-{label or 'session'}-{digest}"
