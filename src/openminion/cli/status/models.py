from dataclasses import dataclass
from typing import Any, Mapping

from openminion.modules.brain.diagnostics.status import PhaseStatus


PhaseStatusSignature = tuple[Any, ...]
_HIDDEN_VISIBILITY_VALUES = frozenset({"hidden", "internal", "private"})


def is_hidden_progress_payload(status: Mapping[str, Any] | None) -> bool:
    if not isinstance(status, Mapping):
        return False
    visibility = str(status.get("visibility", "") or "").strip().lower()
    return visibility in _HIDDEN_VISIBILITY_VALUES


def build_signature(status: PhaseStatus) -> PhaseStatusSignature:
    return (
        status.status_key,
        status.label,
        status.mode,
        status.mode_state,
        status.mode_label,
        status.step_index,
        status.step_total,
        status.mode_step_index,
        status.mode_step_total,
        status.llm_call_count,
        status.llm_call_limit,
        status.total_input_tokens_used,
        status.total_output_tokens_used,
        status.total_tokens_used,
        status.token_usage_estimated,
        status.tool_name,
        status.progress_phase,
        status.detail_text,
        status.terminal,
    )


@dataclass(frozen=True)
class PhaseStatusViewModel:
    status_key: str
    primary_text: str
    elapsed_text: str | None
    mode_label: str | None
    tool_name: str | None
    show_spinner: bool
    terminal: bool
    signature: PhaseStatusSignature

    @property
    def display_label(self) -> str:
        return (
            f"{self.elapsed_text} | {self.primary_text}"
            if self.elapsed_text
            else self.primary_text
        )


def status_from_payload(
    status: PhaseStatus | Mapping[str, Any] | None,
) -> PhaseStatus:
    from openminion.modules.brain.diagnostics.status import coerce_phase_status

    return coerce_phase_status(status)


__all__ = [
    "PhaseStatusSignature",
    "PhaseStatusViewModel",
    "build_signature",
    "is_hidden_progress_payload",
    "status_from_payload",
]
