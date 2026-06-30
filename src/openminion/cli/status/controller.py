import time
from typing import Any, Callable, Mapping

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
    status_from_payload,
)


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
        self, status: PhaseStatus | Mapping[str, Any] | None
    ) -> PhaseStatusViewModel | None:
        phase_status = status_from_payload(status)
        signature = build_signature(phase_status)
        if signature == self._last_signature:
            return None
        self._last_signature = signature
        return self._to_view_model(phase_status, signature)

    def view_model_for(
        self, status: PhaseStatus | Mapping[str, Any] | None
    ) -> PhaseStatusViewModel:
        """Return a view model without touching dedup state.

        Useful for parity tests and for shells that want a view model for
        the initial render without consuming the dedup slot.
        """

        phase_status = status_from_payload(status)
        signature = build_signature(phase_status)
        return self._to_view_model(phase_status, signature)

    def snapshot_elapsed_text(self) -> str | None:
        elapsed_seconds = self.elapsed_seconds()
        if elapsed_seconds is None:
            return None
        return format_elapsed_time(elapsed_seconds)

    def refresh_view_with_live_elapsed(
        self, view: PhaseStatusViewModel
    ) -> PhaseStatusViewModel:
        """Return a copy of ``view`` with `elapsed_text` set from the"""
        elapsed = self.snapshot_elapsed_text()
        if elapsed is None:
            return view
        # Frozen dataclass — rebuild via field copy.
        from dataclasses import replace

        return replace(view, elapsed_text=elapsed)

    def _to_view_model(
        self,
        status: PhaseStatus,
        signature: PhaseStatusSignature,
    ) -> PhaseStatusViewModel:
        primary = format_primary_status_text(
            status, fallback_label=self._fallback_label
        )
        mode_label = str(status.mode_label or "").strip() or None
        raw_tool_name = str(status.tool_name or "").strip() or None
        tool_name = display_name_for_tool_name(raw_tool_name) if raw_tool_name else None
        status_key = str(status.status_key or "").strip()
        terminal = bool(status.terminal) or status_key in {
            "completed",
            "error",
        }
        show_spinner = not terminal or status_key in _SHOW_SPINNER_TERMINAL_KEYS
        # Elapsed is a live render concern (spinner ticks update it between
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
