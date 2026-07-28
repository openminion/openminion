from dataclasses import dataclass, field
import json
from pathlib import Path
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


@dataclass(frozen=True)
class MemoryContextReviewViewModel:
    schema_version: str
    session_id: str
    trace_count: int
    included: tuple[str, ...]
    dropped: tuple[str, ...]
    truncated: tuple[str, ...]
    budget_reasons: tuple[str, ...]
    canary_summary: Mapping[str, Any] = field(default_factory=dict)
    calibration_summary: Mapping[str, Any] = field(default_factory=dict)
    degraded_reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "session_id": self.session_id,
            "trace_count": self.trace_count,
            "included": list(self.included),
            "dropped": list(self.dropped),
            "truncated": list(self.truncated),
            "budget_reasons": list(self.budget_reasons),
            "canary_summary": dict(self.canary_summary),
            "calibration_summary": dict(self.calibration_summary),
            "degraded_reasons": list(self.degraded_reasons),
        }


def status_from_payload(
    status: PhaseStatus | Mapping[str, Any] | None,
) -> PhaseStatus:
    from openminion.modules.brain.diagnostics.status import coerce_phase_status

    return coerce_phase_status(status)


def build_memory_context_review(
    payload: Mapping[str, Any],
    *,
    canary_path: str | Path | None = None,
    calibration_path: str | Path | None = None,
    artifacts_dir: str | Path | None = None,
) -> MemoryContextReviewViewModel:
    included: list[str] = []
    dropped: list[str] = []
    truncated: list[str] = []
    reasons: list[str] = []
    degraded: list[str] = []
    traces = list(payload.get("traces", []) or [])
    for item in traces:
        if not isinstance(item, Mapping):
            degraded.append("trace:invalid_shape")
            continue
        trace = dict(item.get("decision_trace", {}) or {})
        for decision in trace.get("decisions", []) or []:
            if not isinstance(decision, Mapping):
                continue
            segment_id = _safe_text(decision.get("segment_id"))
            action = _safe_text(decision.get("action"))
            reason = _safe_text(decision.get("reason_code"))
            if reason:
                reasons.append(reason)
            if action == "included":
                included.append(segment_id)
            elif action == "dropped":
                dropped.append(segment_id)
            if action == "truncated" or bool(decision.get("truncated", False)):
                truncated.append(segment_id)
    canary_source, canary_lookup_reason = _resolve_summary_path(
        canary_path,
        artifacts_dir=artifacts_dir,
        filename_token="canary",
        expected_version="memory-context-operational-canary.v1",
    )
    calibration_source, calibration_lookup_reason = _resolve_summary_path(
        calibration_path,
        artifacts_dir=artifacts_dir,
        filename_token="calibration",
        expected_version="context-budget-calibration.v1",
    )
    canary, canary_reason = _optional_summary(
        canary_source,
        expected_version="memory-context-operational-canary.v1",
    )
    calibration, calibration_reason = _optional_summary(
        calibration_source,
        expected_version="context-budget-calibration.v1",
    )
    degraded.extend(
        item
        for item in (
            canary_lookup_reason,
            calibration_lookup_reason,
            canary_reason,
            calibration_reason,
        )
        if item
    )
    return MemoryContextReviewViewModel(
        schema_version="memory-context-review.v1",
        session_id=_safe_text(payload.get("session_id")),
        trace_count=len(traces),
        included=tuple(_redact_values(included)),
        dropped=tuple(_redact_values(dropped)),
        truncated=tuple(_redact_values(truncated)),
        budget_reasons=tuple(_redact_values(reasons)),
        canary_summary=canary,
        calibration_summary=calibration,
        degraded_reasons=tuple(degraded),
    )


def render_memory_context_review(view: MemoryContextReviewViewModel) -> str:
    lines = [
        f"context review: session={view.session_id or '-'} traces={view.trace_count}",
        f"- included: {', '.join(view.included) or '-'}",
        f"- dropped: {', '.join(view.dropped) or '-'}",
        f"- truncated: {', '.join(view.truncated) or '-'}",
        f"- reasons: {', '.join(view.budget_reasons) or '-'}",
    ]
    if view.canary_summary:
        lines.append(f"- canary: {_summary_text(view.canary_summary)}")
    if view.calibration_summary:
        lines.append(f"- calibration: {_summary_text(view.calibration_summary)}")
    for reason in view.degraded_reasons:
        lines.append(f"- degraded: {reason}")
    return "\n".join(lines)


def _optional_summary(
    path: str | Path | None, *, expected_version: str
) -> tuple[dict[str, Any], str]:
    if path is None or not str(path).strip():
        return {}, ""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {}, f"{expected_version}:unreadable:{exc.__class__.__name__}"
    if not isinstance(payload, Mapping):
        return {}, f"{expected_version}:invalid_shape"
    report_version = str(payload.get("report_version", "") or "")
    if report_version != expected_version:
        return {}, f"{expected_version}:wrong_version:{report_version or 'missing'}"
    summary = payload.get("summary", {})
    return dict(summary if isinstance(summary, Mapping) else {}), ""


def _resolve_summary_path(
    path: str | Path | None,
    *,
    artifacts_dir: str | Path | None,
    filename_token: str,
    expected_version: str,
) -> tuple[str | Path | None, str]:
    if path is not None and str(path).strip():
        return path, ""
    if artifacts_dir is None or not str(artifacts_dir).strip():
        return path, ""
    root = Path(artifacts_dir).expanduser()
    if not root.exists() or not root.is_dir():
        return None, f"{expected_version}:artifact_dir_unavailable"
    matches: list[Path] = []
    for candidate in root.rglob("*.json"):
        if filename_token not in candidate.name.lower():
            continue
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, Mapping) and payload.get("report_version") == expected_version:
            matches.append(candidate)
    if not matches:
        return None, f"{expected_version}:artifact_not_found"
    return max(matches, key=lambda item: (item.stat().st_mtime_ns, str(item))), ""


def _summary_text(summary: Mapping[str, Any]) -> str:
    parts = [f"{key}={_safe_text(value)}" for key, value in sorted(summary.items())]
    return " ".join(parts) or "-"


def _safe_text(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    if "secret" in lowered or "token" in lowered or "password" in lowered:
        return "[redacted]"
    return text


def _redact_values(values: list[str]) -> list[str]:
    redacted: list[str] = []
    for value in values:
        text = _safe_text(value)
        if text:
            redacted.append(text)
    return redacted


__all__ = [
    "PhaseStatusSignature",
    "PhaseStatusViewModel",
    "MemoryContextReviewViewModel",
    "build_signature",
    "build_memory_context_review",
    "is_hidden_progress_payload",
    "render_memory_context_review",
    "status_from_payload",
]
