import time
from typing import Any, Callable, Mapping

from openminion.cli.ux.verbosity import VerbosityLevel
from openminion.modules.brain.diagnostics.status import PhaseStatus
from openminion.modules.tool.contracts.display_names import (
    display_name_for_tool_name,
)
from .formatting import (
    DEFAULT_FALLBACK_LABEL,
    format_elapsed_time,
    format_primary_status_text,
)
from .models import (
    PhaseStatusSignature,
    PhaseStatusViewModel,
    build_signature,
    is_hidden_progress_payload,
    status_from_payload,
)
from .public_messages import format_public_status_text


_SHOW_SPINNER_TERMINAL_KEYS = frozenset({"waiting_for_user"})


class PhaseStatusController:
    def __init__(
        self,
        *,
        fallback_label: str = DEFAULT_FALLBACK_LABEL,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._fallback_label = fallback_label
        self._clock = clock
        self._last_signature: PhaseStatusSignature | None = None
        self._started_at: float | None = None

    @property
    def fallback_label(self) -> str:
        return self._fallback_label

    @property
    def is_turn_active(self) -> bool:
        return self._started_at is not None

    @property
    def last_signature(self) -> PhaseStatusSignature | None:
        return self._last_signature

    def start_turn(self) -> None:
        self._started_at = self._clock()
        self._last_signature = None

    def end_turn(self) -> None:
        self._started_at = None
        self._last_signature = None

    def elapsed_seconds(self) -> float | None:
        if self._started_at is None:
            return None
        return max(0.0, float(self._clock() - self._started_at))

    def update(
        self,
        status: PhaseStatus | Mapping[str, Any] | None,
        *,
        verbosity: VerbosityLevel = "normal",
    ) -> PhaseStatusViewModel | None:
        if is_hidden_progress_payload(status if isinstance(status, Mapping) else None):
            return None
        phase_status = status_from_payload(status)
        signature = (*build_signature(phase_status), verbosity)
        if signature == self._last_signature:
            return None
        self._last_signature = signature
        return self._to_view_model(phase_status, signature, verbosity=verbosity)

    def view_model_for(
        self,
        status: PhaseStatus | Mapping[str, Any] | None,
        *,
        verbosity: VerbosityLevel = "normal",
    ) -> PhaseStatusViewModel:
        """Return a view model without touching dedup state.

        Useful for parity tests and for shells that want a view model for
        the initial render without consuming the dedup slot.
        """

        hidden_payload = status if isinstance(status, Mapping) else None
        phase_status = status_from_payload(
            None if is_hidden_progress_payload(hidden_payload) else status
        )
        signature = (*build_signature(phase_status), verbosity)
        return self._to_view_model(phase_status, signature, verbosity=verbosity)

    def snapshot_elapsed_text(self) -> str | None:
        elapsed_seconds = self.elapsed_seconds()
        if elapsed_seconds is None:
            return None
        return format_elapsed_time(elapsed_seconds)

    def refresh_view_with_live_elapsed(
        self, view: PhaseStatusViewModel
    ) -> PhaseStatusViewModel:
        """Return a copy of ``view`` with the current elapsed text."""
        elapsed = self.snapshot_elapsed_text()
        if elapsed is None:
            return view
        from dataclasses import replace

        return replace(view, elapsed_text=elapsed)

    def _to_view_model(
        self,
        status: PhaseStatus,
        signature: PhaseStatusSignature,
        *,
        verbosity: VerbosityLevel = "normal",
    ) -> PhaseStatusViewModel:
        primary = (
            format_primary_status_text(status, fallback_label=self._fallback_label)
            if verbosity == "verbose"
            else format_public_status_text(status)
        )
        mode_label = str(status.mode_label or "").strip() or None
        raw_tool_name = str(status.tool_name or "").strip() or None
        tool_name = display_name_for_tool_name(raw_tool_name) if raw_tool_name else None
        status_key = str(status.status_key or "").strip()
        terminal = bool(status.terminal) or status_key in {
            "completed",
            "stopped",
            "error",
        }
        show_spinner = not terminal or status_key in _SHOW_SPINNER_TERMINAL_KEYS
        return PhaseStatusViewModel(
            status_key=status_key,
            primary_text=primary,
            elapsed_text=None,
            mode_label=mode_label,
            tool_name=tool_name,
            show_spinner=show_spinner,
            terminal=terminal,
            signature=signature,
        )


__all__ = ["PhaseStatusController"]
