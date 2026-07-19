"""Service adapters that bind brain semantics to lower runtime contracts."""

from .run_verification import (
    TERMINAL_STATE_PROVENANCE_FIELD,
    TERMINAL_STATE_PROVENANCE_TYPED,
    bind_run_terminal_event,
    derive_run_terminal_state,
)

__all__ = [
    "TERMINAL_STATE_PROVENANCE_FIELD",
    "TERMINAL_STATE_PROVENANCE_TYPED",
    "bind_run_terminal_event",
    "derive_run_terminal_state",
]
