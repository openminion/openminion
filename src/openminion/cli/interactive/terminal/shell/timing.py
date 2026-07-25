from __future__ import annotations

from typing import Any

from openminion.base.config.env import resolve_environment_config
from openminion.cli.constants import OPENMINION_SHOW_PHASE_TIMING_ENV
from openminion.cli.interactive.terminal.transcript import TerminalTranscript
from openminion.cli.presentation.models import ChatMessage, MessageKind
from openminion.cli.presentation.timing_report import (
    format_chat_phase_timing_report,
)

__all__ = ["push_phase_timing_report_if_enabled"]


def push_phase_timing_report_if_enabled(
    *,
    runtime: Any,
    transcript: TerminalTranscript,
) -> None:
    if not resolve_environment_config().get_bool(OPENMINION_SHOW_PHASE_TIMING_ENV, False):
        return
    payload_getter = getattr(runtime, "last_chat_phase_timing_payload", None)
    payload = payload_getter() if callable(payload_getter) else None
    report = format_chat_phase_timing_report(payload)
    if report:
        transcript.push_message(
            ChatMessage(kind=MessageKind.SYSTEM, sender="system", body=report)
        )
