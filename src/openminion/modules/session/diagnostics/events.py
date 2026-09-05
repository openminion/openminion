import logging
from typing import Any

from openminion.modules.telemetry.events.module import make_module_emitters

_LOGGER = logging.getLogger(__name__)
_MODULE_ID = "openminion-session"
_ALLOWED_OPERATIONS = frozenset(
    (
        "turn_start",
        "llm_pack",
        "tool_loop",
        "retry",
        "turn_finish",
        "project_launch",
        "project_launch_denied",
        "project_checks",
    )
)

_emitters = make_module_emitters(
    module_id=_MODULE_ID,
    allowed_operations=_ALLOWED_OPERATIONS,
    logger=_LOGGER,
)
emit_module_telemetry = _emitters.emit_module_telemetry
emit_session_operation = _emitters.emit_operation


def record_project_check_events(
    *,
    runtime: Any,
    result: Any,
) -> None:
    run = result.run
    project_run = result.project_run
    for facts in result.check_events:
        outcome = str(facts["overall_result"])
        status = {
            "pending": "waiting",
            "failure": "failed",
            "success": "ok",
            "cancelled": "cancelled",
            "expired": "expired",
        }[outcome]
        payload = {
            "autonomy_run_id": run.run_id,
            "project_run_id": project_run.project_run_id,
            "task_id": project_run.task_id,
            **facts,
        }
        runtime.sessions.append_event(
            session_id=run.session_id,
            event_type=f"project.checks.{outcome}",
            actor_type="system",
            actor_id=project_run.execution_selectors.agent_id,
            task_id=project_run.task_id,
            payload=payload,
            status=status,
            redaction="bounded",
        )
        emit_session_operation(
            telemetryctl=runtime.telemetry_service,
            session_id=run.session_id,
            turn_id=(
                f"project-check:{project_run.project_run_id}:"
                f"{facts['check_count']}:{outcome}"
            ),
            operation="project_checks",
            status=status,
            extra=payload,
        )


def project_check_metadata(
    events: tuple[dict[str, object], ...],
) -> dict[str, object]:
    waiting = bool(events and events[-1].get("overall_result") == "pending")
    return {
        "detail_code": "waiting_for_checks" if waiting else None,
        "check_events": list(events),
    }


__all__ = [
    "emit_module_telemetry",
    "emit_session_operation",
    "project_check_metadata",
    "record_project_check_events",
]
