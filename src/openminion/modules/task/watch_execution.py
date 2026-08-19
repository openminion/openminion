from collections.abc import Callable
from typing import Any

from openminion.modules.tool.errors import ToolRuntimeError
from openminion.tools.task.constants import (
    DEFAULT_WATCH_TIMEOUT_SECONDS,
    WATCH_PAYLOAD_KEY,
)

WatchTurnRunner = Callable[..., dict[str, Any]]


def execute_watch_check_turn(
    *,
    runtime: Any,
    execute_turn: WatchTurnRunner,
    job: dict[str, Any],
    run: dict[str, Any],
    payload: dict[str, Any],
    watch: dict[str, Any],
) -> dict[str, Any]:
    profile_id = str(watch.get("check_profile_id", "") or "").strip()
    if not profile_id:
        return execute_turn(job=job, run=run, payload=payload)

    registry = getattr(runtime, "tools", None)
    exposure = getattr(registry, "exposure_service", None)
    profile = exposure.profile(profile_id) if exposure is not None else None
    if profile is None:
        return _preflight_failure(
            "watch check profile is unavailable",
            reason_code="watch_profile_unavailable",
        )
    if (
        profile.risk.tier != "read"
        or profile.risk.mutates_state
        or profile.risk.requires_approval
    ):
        return _preflight_failure(
            "watch check profile is not read-only",
            reason_code="watch_profile_not_read_only",
        )
    if not profile.tool_names.issubset(set(registry.list())):
        return _preflight_failure(
            "watch check profile tools are unavailable",
            reason_code="watch_profile_tools_unavailable",
        )

    target_id = str(watch.get("target_id", "") or "").strip()
    ops_service = getattr(runtime, "ops_service", None)
    if ops_service is None:
        return _preflight_failure(
            "operations target registry is unavailable",
            reason_code="watch_target_registry_unavailable",
        )
    try:
        target = ops_service.inspect_target(target_id)
    except KeyError:
        return _preflight_failure(
            "watch target is unavailable",
            reason_code="watch_target_unavailable",
        )

    current_watch = dict(watch)
    current_watch["allowed_tools"] = sorted(profile.tool_names)
    current_payload = dict(payload)
    current_payload[WATCH_PAYLOAD_KEY] = current_watch
    if profile.default_active:
        return execute_turn(job=job, run=run, payload=current_payload)

    session_id = str(current_payload.get("session_id", "") or "").strip()
    task_id = str(job.get("job_id", "") or "").strip()
    try:
        exposure.activate(
            profile_id,
            session_id=session_id,
            task_id=task_id,
            target_id=target_id,
            target_kind=str(getattr(target, "kind", "") or "").strip(),
            ttl_seconds=float(
                int(watch.get("timeout_seconds", DEFAULT_WATCH_TIMEOUT_SECONDS)) + 5
            ),
            activation_reason="watch_check",
            policy_source="task.watch",
        )
    except (KeyError, ToolRuntimeError) as exc:
        return _preflight_failure(
            f"watch check profile activation was refused: {exc}",
            reason_code="watch_profile_activation_refused",
        )
    try:
        return execute_turn(job=job, run=run, payload=current_payload)
    finally:
        exposure.deactivate(
            profile_id,
            session_id=session_id,
            task_id=task_id,
            target_id=target_id,
        )


def _preflight_failure(summary: str, *, reason_code: str) -> dict[str, Any]:
    return {
        "summary": summary,
        "error": True,
        "metadata": {"watch_preflight_reason": reason_code},
    }
