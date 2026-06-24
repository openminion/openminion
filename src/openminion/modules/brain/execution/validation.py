from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from openminion.modules.brain.runtime.recovery import (
    TCRPContext,
    TCRPRetryBudget,
    validate_payload,
)
from openminion.modules.tool.runtime.argument_repair import (
    missing_simple_required_fields,
    tool_family_for_argument_repair,
)
from openminion.modules.tool.contracts.model_ids import (
    MODEL_EXEC_RUN,
    MODEL_WEATHER,
    MODEL_WEB_FETCH,
    MODEL_WEB_SEARCH,
)

from ..constants import (
    BRAIN_ACTION_STATUS_BLOCKED,
    BRAIN_ACTION_STATUS_FAILED,
    BRAIN_ACTION_STATUS_NEEDS_USER,
    BRAIN_ACTION_STATUS_RETRY,
    BRAIN_ACTION_STATUS_SUCCESS,
    BRAIN_ACTION_STATUS_TIMEOUT,
    BRAIN_COMMAND_KIND_TOOL,
    BRAIN_COMMAND_KIND_ASK_USER,
    BRAIN_JOB_STATUS_PENDING,
    BRAIN_JOB_STATUS_RUNNING,
)
from ..schemas import (
    ActionError,
    ActionMetrics,
    ActionResult,
    ArtifactRef,
    Command,
    JobHandle,
)
from ..tools.parser import normalize_tool_name_for_brain

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..runner import BrainRunner


@dataclass(frozen=True)
class ForcedToolGuard:
    reason_code: str
    question: str
    missing_fields: tuple[str, ...] = ()
    action: str = BRAIN_COMMAND_KIND_ASK_USER
    source: str = "forced_tool_argument_repair"
    metadata: dict[str, Any] = field(default_factory=dict)


def _validate_optional_payload(
    raw_payload: Any,
    *,
    model: type[ActionError] | type[ActionMetrics],
    provider: str,
    channel_name: str,
) -> ActionError | ActionMetrics | None:
    if not isinstance(raw_payload, dict):
        return None
    validation = validate_payload(
        raw_payload,
        model=model,
        ctx=TCRPContext(channel_name=f"{provider}.{channel_name}"),
        retry_budget=TCRPRetryBudget(
            channel_name=f"{provider}.{channel_name}",
            max_retries=0,
        ),
    )
    return validation.structured_payload


def validate_tool_args(
    runner: "BrainRunner",
    *,
    command: Command,
    state=None,
) -> dict[str, Any] | None:
    del runner
    if command.kind != BRAIN_COMMAND_KIND_TOOL:
        return None

    family = tool_family_for_argument_repair(getattr(command, "tool_name", ""))
    if family is None:
        return None

    missing = list(
        missing_simple_required_fields(
            tool_name=str(getattr(command, "tool_name", "") or ""),
            arguments=getattr(command, "args", {}) or {},
        )
    )
    if not missing:
        return None

    missing_csv = ", ".join(missing)
    if family == MODEL_WEATHER:
        return {
            "message": "Which location should I check weather for?",
            "missing": missing,
            "reason_code": "weather_location_required",
            "suggestion": "Provide a city or location name.",
            "source": "bounded_argument_repair",
        }
    if family == MODEL_WEB_SEARCH:
        return {
            "message": (
                "I need the missing `query` value before I can run this tool command."
            ),
            "missing": missing,
            "reason_code": "search_query_required",
            "suggestion": "Provide the search query in natural language or explicit args.",
            "source": "bounded_argument_repair",
        }
    return {
        "message": f"Missing required tool arguments: {missing_csv}",
        "missing": missing,
        "reason_code": "tool_arg_validation_failed",
        "suggestion": "",
        "source": "bounded_argument_repair",
    }


def _build_forced_tool_guard(*, tool_name: str) -> ForcedToolGuard | None:
    normalized_tool_name = normalize_tool_name_for_brain(tool_name) or str(tool_name)
    family = tool_family_for_argument_repair(normalized_tool_name)
    if family == MODEL_WEATHER:
        return ForcedToolGuard(
            reason_code="weather_location_required",
            question="Which location should I check weather for?",
            missing_fields=("location",),
        )
    if normalized_tool_name == MODEL_WEB_FETCH:
        return ForcedToolGuard(
            reason_code="web_fetch_url_required",
            question="Which URL should I fetch?",
            missing_fields=("url",),
        )
    if normalized_tool_name == MODEL_EXEC_RUN:
        return ForcedToolGuard(
            reason_code="exec_run_command_required",
            question="Which command should I run?",
            missing_fields=("command",),
        )
    return None


def normalize_execution_result(
    *,
    command_id: str,
    raw: dict[str, Any],
    provider: str,
) -> tuple[ActionResult, JobHandle | None]:
    status = str(raw.get("status", BRAIN_ACTION_STATUS_SUCCESS))
    async_job = _normalize_async_job(
        command_id=command_id,
        raw=raw,
        provider=provider,
        status=status,
    )
    if async_job is not None:
        return async_job

    artifacts = _normalize_artifact_refs(raw.get("artifact_refs"))
    error_obj = _normalize_action_error(raw_error=raw.get("error"), provider=provider)
    metrics = _normalize_action_metrics(
        raw_metrics=raw.get("metrics"), provider=provider
    )

    action_result = ActionResult(
        command_id=command_id,
        status=_normalized_action_status(status),
        summary=str(raw.get("summary", "")),
        outputs=raw.get("outputs") if isinstance(raw.get("outputs"), dict) else {},
        artifact_refs=artifacts,
        memory_refs=[str(x) for x in raw.get("memory_refs", [])]
        if isinstance(raw.get("memory_refs"), list)
        else [],
        error=error_obj,
        metrics=metrics,
    )
    return action_result, None


def _normalize_async_job(
    *,
    command_id: str,
    raw: dict[str, Any],
    provider: str,
    status: str,
) -> tuple[ActionResult, JobHandle] | None:
    if status not in {
        BRAIN_JOB_STATUS_PENDING,
        BRAIN_JOB_STATUS_RUNNING,
    } or not raw.get("task_id"):
        return None
    job = JobHandle(
        task_id=str(raw["task_id"]),
        command_id=command_id,
        provider=BRAIN_COMMAND_KIND_TOOL
        if provider == BRAIN_COMMAND_KIND_TOOL
        else "a2actl",
        status=BRAIN_JOB_STATUS_RUNNING
        if status == BRAIN_JOB_STATUS_RUNNING
        else BRAIN_JOB_STATUS_PENDING,
        poll_after_ms=int(raw.get("poll_after_ms", 1000)),
    )
    result = ActionResult(
        command_id=command_id,
        status=BRAIN_ACTION_STATUS_SUCCESS,
        summary=f"Async task started: {job.task_id}",
    )
    return result, job


def _normalize_artifact_refs(raw_artifacts: Any) -> list[ArtifactRef]:
    artifacts: list[ArtifactRef] = []
    if not isinstance(raw_artifacts, list):
        return artifacts
    for item in raw_artifacts:
        if isinstance(item, str):
            artifacts.append(ArtifactRef(ref=item))
            continue
        if not isinstance(item, dict) or not item.get("ref"):
            continue
        ref = str(item.get("ref", "")).strip()
        if not ref:
            continue
        label = item.get("label")
        normalized_label = str(label).strip() if isinstance(label, str) else None
        normalized_meta = (
            dict(item.get("meta")) if isinstance(item.get("meta"), dict) else {}
        )
        for key, value in item.items():
            if key not in {"ref", "label", "meta"}:
                normalized_meta.setdefault(str(key), value)
        artifacts.append(
            ArtifactRef(ref=ref, label=normalized_label or None, meta=normalized_meta)
        )
    return artifacts


def _normalize_action_error(*, raw_error: Any, provider: str) -> ActionError | None:
    if not (
        isinstance(raw_error, dict)
        and raw_error.get("code")
        and raw_error.get("message")
    ):
        return None
    return _validate_optional_payload(
        raw_error,
        model=ActionError,
        provider=provider,
        channel_name="action_error",
    )


def _normalize_action_metrics(
    *, raw_metrics: Any, provider: str
) -> ActionMetrics | None:
    return _validate_optional_payload(
        raw_metrics,
        model=ActionMetrics,
        provider=provider,
        channel_name="action_metrics",
    )


def _normalized_action_status(status: str) -> str:
    return (
        status
        if status
        in {
            BRAIN_ACTION_STATUS_SUCCESS,
            BRAIN_ACTION_STATUS_RETRY,
            BRAIN_ACTION_STATUS_FAILED,
            BRAIN_ACTION_STATUS_BLOCKED,
            BRAIN_ACTION_STATUS_NEEDS_USER,
            BRAIN_ACTION_STATUS_TIMEOUT,
        }
        else BRAIN_ACTION_STATUS_FAILED
    )


def budget_blocked_result(*, command_id: str, budget_name: str) -> ActionResult:
    return ActionResult(
        command_id=command_id,
        status=BRAIN_ACTION_STATUS_BLOCKED,
        summary=f"Budget exhausted: {budget_name}",
        error=ActionError(
            code="BUDGET_EXCEEDED",
            message=f"Budget exceeded for {budget_name}.",
            details={"reason_code": "budget_exceeded", "budget": budget_name},
        ),
    )
