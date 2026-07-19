"""Request shaping and validation for runtime ingress."""

from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from openminion.base.config import combine_run_profile_overrides, resolve_agent_identity
from openminion.services.runtime.manager import TurnRequest

from .payloads import (
    apply_inbound_overrides,
    apply_managed_meta as _apply_managed_meta,
    parse_forced_tools,
    parse_inbound_metadata,
    resolve_capability_category,
    resolve_deliver,
)
from .timeout import _parse_run_profile_overrides, resolve_timeout_seconds
from .types import RuntimeTurnRequest, TurnRequestError

if TYPE_CHECKING:
    from openminion.services.runtime.interfaces import RuntimeFacade


def runtime_turn_request_from_payload(
    *,
    runtime: "RuntimeFacade",
    payload: dict[str, Any],
    request_id: str | None = None,
) -> RuntimeTurnRequest:
    message = str(payload.get("message", "")).strip()
    if not message:
        raise TurnRequestError("`message` is required and must be a non-empty string.")

    explicit_category_raw = payload.get("capability_category")
    explicit_category = (
        str(explicit_category_raw).strip() if explicit_category_raw is not None else ""
    )
    agent_id_raw = payload.get("agent_id")
    requested_agent_id = (
        str(agent_id_raw).strip() if isinstance(agent_id_raw, str) else None
    )
    run_profile_overrides = _parse_run_profile_overrides(payload)
    effective_run_profile_overrides = combine_run_profile_overrides(
        getattr(runtime, "run_profile_overrides", None),
        run_profile_overrides,
    )
    agent_resolution = resolve_agent_identity(runtime.config, requested_agent_id)
    agent_profile = agent_resolution.profile
    timeout_seconds = resolve_timeout_seconds(
        payload=payload,
        default_seconds=runtime.config.gateway.api_turn_timeout_seconds,
        config=runtime.config,
        agent_id=agent_profile.name,
        run_profile_overrides=effective_run_profile_overrides,
    )
    inbound_metadata = _direct_inbound_metadata(runtime=runtime, payload=payload)
    return RuntimeTurnRequest(
        agent_id=agent_resolution.public_agent_id,
        profile_agent_id=agent_profile.name,
        message=message,
        channel=str(payload.get("channel", "")).strip()
        or agent_profile.default_channel,
        target=str(payload.get("target", "")).strip() or "api-user",
        timeout_seconds=timeout_seconds,
        session_id=_optional_text(payload.get("session_id")),
        request_id=request_id,
        idempotency_key=_optional_text(payload.get("idempotency_key")),
        inbound_metadata=(
            MappingProxyType(dict(inbound_metadata))
            if inbound_metadata is not None
            else None
        ),
        deliver=resolve_deliver(payload.get("deliver")),
        forced_tools=tuple(parse_forced_tools(payload.get("forced_tools")) or ()),
        capability_category=resolve_capability_category(
            explicit_category=explicit_category if explicit_category else None,
        ),
        run_profile_overrides=run_profile_overrides,
    )


def build_manager_turn_request(
    payload: dict[str, Any],
    *,
    default_agent_id: str,
) -> TurnRequest:
    trace_id = _optional_text(payload.get("trace_id")) or ""
    agent_id = _optional_text(payload.get("agent_id")) or default_agent_id
    session_id = _optional_text(payload.get("session_id")) or ""
    input_text_raw = payload.get("input_text")
    if not isinstance(input_text_raw, str):
        input_text_raw = payload.get("message")
    input_text = str(input_text_raw).strip() if isinstance(input_text_raw, str) else ""
    if not session_id:
        raise ValueError("`session_id` is required.")
    if not input_text and not _is_pae_idle_tick(payload):
        raise ValueError("`input_text` is required.")

    attachments_raw = payload.get("attachments", [])
    attachments: list[str] = []
    if isinstance(attachments_raw, list):
        attachments = [
            str(item).strip() for item in attachments_raw if str(item).strip()
        ]
    mode = _optional_text(payload.get("mode")) or "oneshot"
    return TurnRequest(
        trace_id=trace_id,
        agent_id=agent_id,
        session_id=session_id,
        input_text=input_text,
        attachments=attachments,
        mode=mode,
        stream=bool(payload.get("stream")),
        meta=_manager_meta_from_payload(payload),
    )


def runtime_turn_request_from_manager_request(
    *,
    runtime: "RuntimeFacade",
    request: TurnRequest,
) -> RuntimeTurnRequest:
    meta = dict(request.meta or {})
    run_profile_overrides = _parse_run_profile_overrides(meta)
    effective_run_profile_overrides = combine_run_profile_overrides(
        getattr(runtime, "run_profile_overrides", None),
        run_profile_overrides,
    )
    agent_resolution = resolve_agent_identity(runtime.config, request.agent_id)
    agent_profile = agent_resolution.profile
    timeout_payload: dict[str, Any] = {}
    if "timeout_seconds" in meta:
        timeout_payload["timeout_seconds"] = meta.get("timeout_seconds")
    inbound_metadata = _managed_inbound_metadata(runtime=runtime, meta=meta)
    return RuntimeTurnRequest(
        agent_id=agent_resolution.public_agent_id,
        profile_agent_id=agent_profile.name,
        message=str(request.input_text or "").strip(),
        channel=str(meta.get("channel", "")).strip() or agent_profile.default_channel,
        target=str(meta.get("user", "")).strip() or "api-user",
        timeout_seconds=resolve_timeout_seconds(
            payload=timeout_payload,
            default_seconds=runtime.config.gateway.api_turn_timeout_seconds,
            config=runtime.config,
            agent_id=agent_profile.name,
            run_profile_overrides=effective_run_profile_overrides,
        ),
        session_id=str(request.session_id or "").strip() or None,
        request_id=str(request.trace_id or "").strip() or None,
        idempotency_key=str(meta.get("idempotency_key", "")).strip() or None,
        inbound_metadata=(
            MappingProxyType(dict(inbound_metadata))
            if inbound_metadata is not None
            else None
        ),
        deliver=resolve_deliver(meta.get("deliver")),
        forced_tools=tuple(parse_forced_tools(meta.get("forced_tools")) or ()),
        capability_category=resolve_capability_category(
            explicit_category=meta.get("capability_category"),
        ),
        run_profile_overrides=run_profile_overrides,
    )


def apply_workspace_root(
    *,
    inbound_metadata: dict[str, str] | None,
    runtime_workspace_root: Any,
) -> dict[str, str] | None:
    if runtime_workspace_root and (
        inbound_metadata is None or not inbound_metadata.get("workspace_root")
    ):
        updated = dict(inbound_metadata or {})
        updated["workspace_root"] = str(runtime_workspace_root)
        return updated
    return inbound_metadata


def _direct_inbound_metadata(
    *,
    runtime: "RuntimeFacade",
    payload: dict[str, Any],
) -> dict[str, str] | None:
    inbound_metadata = parse_inbound_metadata(
        payload.get("inbound_metadata"),
        error_factory=TurnRequestError,
    )
    inbound_metadata = apply_inbound_overrides(
        inbound_metadata=inbound_metadata,
        payload=payload,
    )
    return apply_workspace_root(
        inbound_metadata=inbound_metadata,
        runtime_workspace_root=getattr(runtime, "tool_workspace_root", None),
    )


def _managed_inbound_metadata(
    *,
    runtime: "RuntimeFacade",
    meta: dict[str, Any],
) -> dict[str, str] | None:
    inbound_metadata = parse_inbound_metadata(
        meta.get("inbound_metadata"),
        error_factory=TurnRequestError,
    )
    inbound_metadata = apply_inbound_overrides(
        inbound_metadata=inbound_metadata,
        payload=meta,
    )
    inbound_metadata = _apply_managed_meta(inbound_metadata=inbound_metadata, meta=meta)
    return apply_workspace_root(
        inbound_metadata=inbound_metadata,
        runtime_workspace_root=getattr(runtime, "tool_workspace_root", None),
    )


def _optional_text(value: Any) -> str | None:
    return str(value).strip() if isinstance(value, str) and str(value).strip() else None


def _is_pae_idle_tick(payload: dict[str, Any]) -> bool:
    cron_meta = payload.get("cron") if isinstance(payload, dict) else None
    meta_raw = payload.get("meta") if isinstance(payload, dict) else None
    return (
        isinstance(cron_meta, dict)
        and str(cron_meta.get("pae_idle_tick", "")).strip().lower() == "true"
    ) or (
        isinstance(meta_raw, dict)
        and str(meta_raw.get("pae_idle_tick", "")).strip().lower() == "true"
    )


def _manager_meta_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    meta_raw = payload.get("meta")
    if isinstance(meta_raw, dict):
        meta.update(meta_raw)
    _lift_payload_fields(meta=meta, payload=payload)
    return meta


def _lift_payload_fields(*, meta: dict[str, Any], payload: dict[str, Any]) -> None:
    for key in ("conversation_id", "thread_id", "attach_id", "resume", "reset_session"):
        if key not in meta and key in payload and payload.get(key) is not None:
            value = payload.get(key)
            meta[key] = (
                "true" if value is True else "false" if value is False else value
            )
    for payload_key, meta_key in (("channel", "channel"), ("user", "user")):
        value = _optional_text(payload.get(payload_key))
        if value:
            meta[meta_key] = value
    for key in (
        "timeout_seconds",
        "inbound_metadata",
        "deliver",
        "forced_tools",
        "capability_category",
    ):
        if key in payload:
            meta[key] = payload.get(key)
    idempotency_key = _optional_text(payload.get("idempotency_key"))
    if idempotency_key:
        meta["idempotency_key"] = idempotency_key
    for key in ("override_provider", "override_model", "override_system_prompt"):
        if key not in meta:
            value = _optional_text(payload.get(key))
            if value:
                meta[key] = value
