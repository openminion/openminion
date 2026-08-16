from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping, MutableMapping
from typing import Any

from .api import target_view
from .args import (
    EmptyArgs,
    CommandPlanArgs,
    CommandRunArgs,
    JobArgs,
    LogsArgs,
    ObservationArgs,
    PortOwnerArgs,
    ProcessArgs,
    ProfileArgs,
    ServiceArgs,
    TargetArgs,
)
from .contracts import OperationRequest
from .interfaces import (
    TOOL_OPS_COMMAND_OBSERVE,
    TOOL_OPS_LOGS_QUERY,
    TOOL_OPS_NETWORK_PORT_OWNER,
    TOOL_OPS_PROCESS_INSPECT,
    TOOL_OPS_SERVICE_INSPECT,
)
from .service import (
    OpsService,
    local_ops_service,
)


def _service(ctx: Any) -> OpsService:
    configured = getattr(ctx, "ops_service", None)
    if isinstance(configured, OpsService):
        return configured
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


def _process_inspect(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    parsed = ProcessArgs.model_validate(args)
    return _observed(
        _service(ctx),
        _request(
            target_id=parsed.target_id,
            profile_id="process.inspect",
            tool_id=TOOL_OPS_PROCESS_INSPECT,
            timeout_seconds=parsed.timeout_seconds,
            parameters={"pid": parsed.pid},
            session_id=_session_id(ctx),
        ),
    )


def _network_port_owner(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    parsed = PortOwnerArgs.model_validate(args)
    return _observed(
        _service(ctx),
        _request(
            target_id=parsed.target_id,
            profile_id="network.port_owner",
            tool_id=TOOL_OPS_NETWORK_PORT_OWNER,
            timeout_seconds=parsed.timeout_seconds,
            parameters={"port": parsed.port, "protocol": parsed.protocol},
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


def _command_plan(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    parsed = CommandPlanArgs.model_validate(args)
    plan = _service(ctx).plan_command(
        target_id=parsed.target_id,
        argv=parsed.argv,
        cwd=parsed.cwd,
        timeout_seconds=parsed.timeout_seconds,
        session_id=_session_id(ctx),
        idempotency_key=parsed.idempotency_key,
    )
    return {
        "ok": True,
        "content": f"Command plan {plan.plan_id} is ready for review.",
        "data": plan.model_dump(mode="json"),
        "verified": True,
    }


def _command_run(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    parsed = CommandRunArgs.model_validate(args)
    if not bool(getattr(ctx, "confirm", False)):
        raise PermissionError("command plan requires operator approval")
    job = _service(ctx).run_plan(
        plan_id=parsed.plan_id,
        plan_hash=parsed.plan_hash,
        approval_id=_approval_id(ctx, parsed.plan_hash),
    )
    return {
        "ok": job.status == "succeeded",
        "content": f"Command job {job.job_id} finished with status {job.status}.",
        "data": job.model_dump(mode="json"),
        "verified": job.status == "succeeded",
    }


def _session_id(ctx: Any) -> str:
    direct = str(getattr(ctx, "session_id", "") or "").strip()
    if direct:
        return direct
    extras = getattr(ctx, "extras", None)
    return str(extras.get("session_id", "")) if isinstance(extras, Mapping) else ""


def _approval_id(ctx: Any, plan_hash: str) -> str:
    policy = getattr(ctx, "policy", None)
    raw = getattr(policy, "raw", {})
    metadata = raw.get("context_metadata", {}) if isinstance(raw, Mapping) else {}
    grant_id = (
        str(metadata.get("confirmation_grant_id", "") or "").strip()
        if isinstance(metadata, Mapping)
        else ""
    )
    return grant_id or f"interactive-{plan_hash[:16]}"


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
