from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping, MutableMapping
from typing import Any

from .api import target_view
from .args import (
    EmptyArgs,
    JobArgs,
    LogsArgs,
    ObservationArgs,
    ProfileArgs,
    ServiceArgs,
    TargetArgs,
)
from .contracts import OperationRequest
from .interfaces import (
    TOOL_OPS_COMMAND_OBSERVE,
    TOOL_OPS_LOGS_QUERY,
    TOOL_OPS_SERVICE_INSPECT,
)
from .service import (
    OpsService,
    local_ops_service,
)


def _service(ctx: Any) -> OpsService:
    extras = getattr(ctx, "extras", None)
    if isinstance(extras, Mapping):
        configured = extras.get("ops_service")
        if isinstance(configured, OpsService):
            return configured
    service = local_ops_service()
    if isinstance(extras, MutableMapping):
        extras["ops_service"] = service
    return service


def _request(
    *,
    target_id: str,
    profile_id: str,
    tool_id: str,
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
        tool_id=tool_id,
    )


def _observed(service: OpsService, request: OperationRequest) -> dict[str, Any]:
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
    tool_id: str,
) -> Callable[[dict[str, Any], Any], dict[str, Any]]:
    def handler(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        parsed = ObservationArgs.model_validate(args)
        return _observed(
            _service(ctx),
            _request(
                target_id=parsed.target_id,
                profile_id=profile_id,
                tool_id=tool_id,
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
            tool_id=TOOL_OPS_SERVICE_INSPECT,
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
            tool_id=TOOL_OPS_LOGS_QUERY,
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
            tool_id=TOOL_OPS_COMMAND_OBSERVE,
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
