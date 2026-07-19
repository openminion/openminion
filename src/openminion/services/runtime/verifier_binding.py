"""Compatibility imports for module-owned task-run verification."""

from openminion.modules.task.run import append_run_state_event
from openminion.services.brain.adapters.run_verification import (
    TERMINAL_STATE_PROVENANCE_FIELD,
    TERMINAL_STATE_PROVENANCE_TYPED,
    bind_run_terminal_event,
    derive_run_terminal_state,
)

__all__ = [
    "TERMINAL_STATE_PROVENANCE_FIELD",
    "TERMINAL_STATE_PROVENANCE_TYPED",
    "append_run_state_event",
    "bind_run_terminal_event",
    "derive_run_terminal_state",
]
