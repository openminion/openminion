"""Reusable focus-shell E2E harness."""

from .assertions import assert_focus_turn_completed, assert_no_terminal_crash
from .probe import FocusProbe
from .pty import PtySession
from .scenarios import FocusScenario

__all__ = [
    "FocusProbe",
    "FocusScenario",
    "PtySession",
    "assert_focus_turn_completed",
    "assert_no_terminal_crash",
]
