from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping, MutableMapping
from typing import Any

from openminion.modules.system_operations.schemas import OperationRequest
from openminion.modules.system_operations.api import target_view
from openminion.modules.system_operations.service import (
    SystemOperationsService,
    local_operations_service,
)
from openminion.modules.tool.registry import ToolRegistry, ToolSpec

from .interfaces import (
    TOOL_OPS_COMMAND_OBSERVE,
    TOOL_OPS_HOST_SNAPSHOT,
    TOOL_OPS_JOB_CANCEL,
    TOOL_OPS_JOB_INSPECT,
    TOOL_OPS_LOGS_QUERY,
    TOOL_OPS_NETWORK_INSPECT,
    TOOL_OPS_SERVICE_INSPECT,
    TOOL_OPS_TARGET_INSPECT,
    TOOL_OPS_TARGET_LIST,
)
from .schemas import (
    EmptyArgs,
    JobArgs,
    LogsArgs,
    ObservationArgs,
    ProfileArgs,
    ServiceArgs,
    TargetArgs,
)


def _service(ctx: Any) -> SystemOperationsService:
    extras = getattr(ctx, "extras", None)
    if isinstance(extras, Mapping):
        configured = extras.get("system_operations_service")
        if isinstance(configured, SystemOperationsService):
            return configured
    service = local_operations_service()
    if isinstance(extras, MutableMapping):
        extras["system_operations_service"] = service
    return service


def _request(
    *,
    target_id: str,
    profile_id: str,
    timeout_seconds: float,
    parameters: Mapping[str, str | int | bool] | None = None,
    session_id: str = "",
) -> OperationRequest:
    return OperationRequest(
        operation_id=f"observe-{uuid.uuid4().hex}",
        target_id=target_id,
        profile_id=profile_id,
        parameters=dict(parameters or {}),
        timeout_seconds=timeout_seconds,
        session_id=session_id,
    )


def _observed(
    service: SystemOperationsService, request: OperationRequest
) -> dict[str, Any]:
    evidence = service.observe(request)
    return {
        "ok": evidence.claim_status == "observed",
        "content": evidence.stdout_preview or evidence.reason,
        "data": evidence.model_dump(mode="json"),
        "verified": evidence.claim_status == "observed",
    }


def _target_list(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    EmptyArgs.model_validate(args)
    targets = _service(ctx).list_targets()
    return {"ok": True, "data": {"targets": [target_view(item) for item in targets]}}


def _target_inspect(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    parsed = TargetArgs.model_validate(args)
    target = _service(ctx).inspect_target(parsed.target_id)
    return {"ok": True, "data": target_view(target)}


def _profile(
    profile_id: str,
) -> Callable[[dict[str, Any], Any], dict[str, Any]]:
    def handler(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        parsed = ObservationArgs.model_validate(args)
        return _observed(
            _service(ctx),
            _request(
                target_id=parsed.target_id,
                profile_id=profile_id,
                timeout_seconds=parsed.timeout_seconds,
                session_id=_session_id(ctx),
            ),
        )

    return handler


def _service_inspect(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    parsed = ServiceArgs.model_validate(args)
    return _observed(
        _service(ctx),
        _request(
            target_id=parsed.target_id,
            profile_id="service.inspect",
            timeout_seconds=parsed.timeout_seconds,
            parameters={"service": parsed.service},
            session_id=_session_id(ctx),
        ),
    )


def _logs_query(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    parsed = LogsArgs.model_validate(args)
    return _observed(
        _service(ctx),
        _request(
            target_id=parsed.target_id,
            profile_id="logs.query",
            timeout_seconds=parsed.timeout_seconds,
            parameters={"service": parsed.service, "limit": parsed.limit},
            session_id=_session_id(ctx),
        ),
    )


def _command_observe(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    parsed = ProfileArgs.model_validate(args)
    return _observed(
        _service(ctx),
        _request(
            target_id=parsed.target_id,
            profile_id=parsed.profile_id,
            timeout_seconds=parsed.timeout_seconds,
            session_id=_session_id(ctx),
        ),
    )


def _session_id(ctx: Any) -> str:
    extras = getattr(ctx, "extras", None)
    return str(extras.get("session_id", "")) if isinstance(extras, Mapping) else ""


def _job_inspect(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    parsed = JobArgs.model_validate(args)
    job = _service(ctx).inspect_job(
        parsed.job_id,
        target_id=parsed.target_id,
        session_id=parsed.session_id,
    )
    return {"ok": True, "data": job.model_dump(mode="json")}


def _job_cancel(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    parsed = JobArgs.model_validate(args)
    job = _service(ctx).cancel_job(
        parsed.job_id,
        target_id=parsed.target_id,
        session_id=parsed.session_id,
    )
    return {"ok": True, "data": job.model_dump(mode="json")}


def register(registry: ToolRegistry) -> None:
    specs = (
        (TOOL_OPS_TARGET_LIST, EmptyArgs, _target_list),
        (TOOL_OPS_TARGET_INSPECT, TargetArgs, _target_inspect),
        (TOOL_OPS_HOST_SNAPSHOT, ObservationArgs, _profile("host.snapshot")),
        (TOOL_OPS_SERVICE_INSPECT, ServiceArgs, _service_inspect),
        (TOOL_OPS_LOGS_QUERY, LogsArgs, _logs_query),
        (TOOL_OPS_NETWORK_INSPECT, ObservationArgs, _profile("network.inspect")),
        (TOOL_OPS_COMMAND_OBSERVE, ProfileArgs, _command_observe),
        (TOOL_OPS_JOB_INSPECT, JobArgs, _job_inspect),
        (TOOL_OPS_JOB_CANCEL, JobArgs, _job_cancel),
    )
    for name, args_model, handler in specs:
        control_tool = name == TOOL_OPS_JOB_CANCEL
        registry.add(
            ToolSpec(
                name=name,
                args_model=args_model,
                min_scope="READ_ONLY",
                handler=handler,
                dangerous=False,
                idempotent=True,
                tags=(
                    "plugin",
                    "ops",
                    "operation_control" if control_tool else "observation",
                ),
                capabilities=(
                    "operation_control" if control_tool else "read_only",
                    "system_operations",
                    "evidence",
                ),
            )
        )
