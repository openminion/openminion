import logging
from dataclasses import dataclass, field
from typing import Any

_log = logging.getLogger(__name__)


EXPECTED_TRAILERS_METADATA_KEY = "expected_trailers"


TRAILER_LANE_APD = "apd"
TRAILER_LANE_MACC = "macc"
TRAILER_LANE_SWSC = "swsc"
TRAILER_LANE_PTC = "ptc"
TRAILER_LANE_MRP = "mrp"
TRAILER_LANE_DELEGATION_CONTEXT = "delegation_context"
TRAILER_LANE_DELEGATION_RESULT = "delegation_result_summary"

_LANE_RESPONSE_FIELDS: dict[str, tuple[str, ...]] = {
    TRAILER_LANE_APD: (
        "task_plan",
        "task_plan_step_completed",
        "task_plan_step_blocked",
        "task_plan_revision",
        "task_plan_abandoned",
        "task_plan_completed",
    ),
    TRAILER_LANE_MACC: ("confident_complete",),
    TRAILER_LANE_SWSC: ("session_work_summary",),
    TRAILER_LANE_PTC: ("pending_turn_context",),
    TRAILER_LANE_MRP: ("meta_rule_preference",),
    TRAILER_LANE_DELEGATION_CONTEXT: ("delegation_context",),
    TRAILER_LANE_DELEGATION_RESULT: ("delegation_result_summary",),
}
_TYPED_SIGNAL_SOURCES_TELEMETRY_KEY = "typed_signal_sources"
_UNKNOWN_SIGNAL_SOURCE = "unknown"


@dataclass
class TrailerPostprocessResult:
    """Structural summary of the trailer events recorded for a turn."""

    expected_lanes: list[str] = field(default_factory=list)
    emitted_lanes: list[str] = field(default_factory=list)
    route: str = ""
    feedback_pending: bool = False


class TrailerPostprocessService:
    """Record typed trailer lanes as canonical session events."""

    def __init__(self, *, logger: logging.Logger | None = None) -> None:
        self._log = logger or _log

    def process(
        self,
        *,
        response: Any = None,
        emitted_payloads: dict[str, Any] | None = None,
        session_api: Any,
        session_id: str,
        agent_id: str,
        trace_id: str = "",
        route: str = "",
        request_metadata: dict[str, Any] | None = None,
    ) -> TrailerPostprocessResult:
        """Emit expected/emitted trailer measurements for a completed turn."""
        append_event = self._resolve_append_event(session_api)
        expected_lanes = self._extract_expected_lanes(request_metadata)
        if response is not None:
            emitted_lanes = self._scan_emitted_lanes_from_response(response)
            emitted_sources = self._scan_emitted_sources_from_response(
                response=response,
                emitted_lanes=emitted_lanes,
            )
        else:
            emitted_lanes = self._scan_emitted_lanes_from_payloads(
                emitted_payloads or {}
            )
            emitted_sources = {lane: [_UNKNOWN_SIGNAL_SOURCE] for lane in emitted_lanes}

        feedback_pending = False
        missing_lanes = [lane for lane in expected_lanes if lane not in emitted_lanes]

        if append_event is not None:
            if expected_lanes:
                self._emit_event(
                    append_event,
                    session_id=session_id,
                    agent_id=agent_id,
                    trace_id=trace_id,
                    event_type="trailer.expected",
                    payload={
                        "lanes": list(expected_lanes),
                        "route": str(route or "").strip(),
                    },
                )
            self._emit_event(
                append_event,
                session_id=session_id,
                agent_id=agent_id,
                trace_id=trace_id,
                event_type="trailer.emitted",
                payload={
                    "lanes": list(emitted_lanes),
                    "route": str(route or "").strip(),
                    "sources": emitted_sources,
                },
            )

            if expected_lanes and missing_lanes:
                feedback_payload = self._build_missing_trailer_feedback(
                    missing_lanes=missing_lanes,
                    route=str(route or "").strip(),
                )
                self._emit_event(
                    append_event,
                    session_id=session_id,
                    agent_id=agent_id,
                    trace_id=trace_id,
                    event_type="trailer.feedback_pending",
                    payload=feedback_payload,
                )
                feedback_pending = True

        return TrailerPostprocessResult(
            expected_lanes=list(expected_lanes),
            emitted_lanes=list(emitted_lanes),
            route=str(route or "").strip(),
            feedback_pending=feedback_pending,
        )

    _LANE_FEEDBACK_HINTS: dict[str, str] = {
        TRAILER_LANE_APD: (
            "The prior decide guidance expected a <task_plan> control trailer "
            "for this multi-turn task. No typed trailer was detected in your "
            "previous response. On your next response, emit the typed "
            "<task_plan>{...}</task_plan> block after the user-facing answer."
        ),
        TRAILER_LANE_MACC: (
            "The prior guidance expected a <confident_complete> trailer. Emit "
            "the typed control block on your next response when the task is "
            "complete."
        ),
        TRAILER_LANE_SWSC: (
            "The prior guidance expected a <session_work_summary> trailer "
            "after a significant milestone. Emit the typed control block on "
            "your next response."
        ),
        TRAILER_LANE_PTC: (
            "The prior guidance expected pending_turn_context for a short "
            "follow-up continuation, including referential follow-ups after "
            "plans or itineraries. Populate the typed pending_turn_context "
            "field with the original request and active work summary."
        ),
        TRAILER_LANE_MRP: (
            "The prior guidance expected meta_rule_preference for a reusable "
            "threshold or policy preference. Populate the typed "
            "meta_rule_preference field with rule, preferred_value, and "
            "reasoning."
        ),
        TRAILER_LANE_DELEGATION_CONTEXT: (
            "The prior guidance expected delegation_context before delegated "
            "work. Populate the typed delegation_context field with a bounded "
            "summary and any relevant artifacts or intent id."
        ),
        TRAILER_LANE_DELEGATION_RESULT: (
            "The prior guidance expected delegation_result_summary after "
            "delegated work. Populate the typed delegation_result_summary "
            "field with summary, status, and produced artifacts when present."
        ),
    }

    def _build_missing_trailer_feedback(
        self, *, missing_lanes: list[str], route: str
    ) -> dict[str, Any]:
        hints = [
            self._LANE_FEEDBACK_HINTS.get(lane, f"Expected trailer missing: {lane}.")
            for lane in missing_lanes
        ]
        return {
            "kind": "missing_trailer",
            "missing_lanes": list(missing_lanes),
            "route": route,
            "hints": hints,
        }

    def _resolve_append_event(self, session_api: Any) -> Any | None:
        append_event = getattr(session_api, "append_event", None)
        return append_event if callable(append_event) else None

    def _extract_expected_lanes(
        self, request_metadata: dict[str, Any] | None
    ) -> list[str]:
        if not isinstance(request_metadata, dict):
            return []
        raw = request_metadata.get(EXPECTED_TRAILERS_METADATA_KEY)
        if not isinstance(raw, (list, tuple)):
            return []
        lanes: list[str] = []
        for entry in raw:
            text = str(entry or "").strip().lower()
            if text and text not in lanes:
                lanes.append(text)
        return lanes

    def _scan_emitted_lanes_from_response(self, response: Any) -> list[str]:
        if response is None:
            return []
        emitted: list[str] = []
        for lane, fields in _LANE_RESPONSE_FIELDS.items():
            if any(self._response_field_populated(response, name) for name in fields):
                emitted.append(lane)
        return emitted

    def _scan_emitted_sources_from_response(
        self, *, response: Any, emitted_lanes: list[str]
    ) -> dict[str, list[str]]:
        telemetry = getattr(response, "telemetry", None)
        raw_sources = {}
        if isinstance(telemetry, dict):
            raw = telemetry.get(_TYPED_SIGNAL_SOURCES_TELEMETRY_KEY)
            raw_sources = dict(raw) if isinstance(raw, dict) else {}
        sources_by_lane: dict[str, list[str]] = {}
        for lane in emitted_lanes:
            sources: list[str] = []
            for field_name in _LANE_RESPONSE_FIELDS.get(lane, ()):
                if not self._response_field_populated(response, field_name):
                    continue
                source = str(raw_sources.get(field_name) or "").strip()
                if source and source not in sources:
                    sources.append(source)
            sources_by_lane[lane] = sources or [_UNKNOWN_SIGNAL_SOURCE]
        return sources_by_lane

    def _scan_emitted_lanes_from_payloads(self, payloads: dict[str, Any]) -> list[str]:
        if not isinstance(payloads, dict) or not payloads:
            return []
        emitted: list[str] = []
        for lane, fields in _LANE_RESPONSE_FIELDS.items():
            if any(self._payload_populated(payloads.get(name)) for name in fields):
                emitted.append(lane)
        return emitted

    @staticmethod
    def _response_field_populated(response: Any, field_name: str) -> bool:
        value = getattr(response, field_name, None)
        return TrailerPostprocessService._payload_populated(value)

    @staticmethod
    def _payload_populated(value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, dict):
            return bool(value)
        if isinstance(value, (list, tuple, set)):
            return bool(value)
        return True

    def _emit_event(
        self,
        append_event: Any,
        *,
        session_id: str,
        agent_id: str,
        trace_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        try:
            append_event(
                session_id,
                event_type,
                payload,
                actor_type="system",
                actor_id=agent_id,
                trace={"trace_id": trace_id} if str(trace_id or "").strip() else None,
                importance=1,
                redaction="none",
                status="ok",
            )
        except Exception as exc:  # noqa: BLE001
            self._log.debug(
                "trailer postprocess event emit failed: type=%s error=%s",
                event_type,
                exc,
            )


__all__ = [
    "EXPECTED_TRAILERS_METADATA_KEY",
    "TRAILER_LANE_APD",
    "TRAILER_LANE_DELEGATION_CONTEXT",
    "TRAILER_LANE_DELEGATION_RESULT",
    "TRAILER_LANE_MACC",
    "TRAILER_LANE_MRP",
    "TRAILER_LANE_PTC",
    "TRAILER_LANE_SWSC",
    "TrailerPostprocessResult",
    "TrailerPostprocessService",
]
