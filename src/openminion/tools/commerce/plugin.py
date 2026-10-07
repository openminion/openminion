"""Thin typed commerce tool handlers."""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal, NoReturn, cast

from pydantic import BaseModel, ConfigDict, Field, RootModel, field_validator

from openminion.tools.commerce.provider import (
    CommerceOutcomeUnknown,
    CommerceHandoff,
    CommerceProviderError,
    OrderActionPreparation,
    OrderPreparation,
    RequestedItem,
)
from openminion.modules.tool import RuntimeContext
from openminion.modules.tool.contracts.schemas import (
    TOOL_ERROR_CONFIRM_REQUIRED,
    ErrorCode,
)
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.diagnostics.events import emit_tool_execution_event
from openminion.modules.telemetry.events.catalog import TOOL_COMMERCE_ACTION
from openminion.modules.tool.plugin_api import stable_invocation_hash

from .authorization import canonical_commerce_args
from .confirmation import commerce_tool_result_lines
from .constants import COMMERCE_LOCAL_SUBJECT_ID
from .identity import commerce_digest
from .runtime import CommerceRuntime


def resolve_commerce_runtime(context: RuntimeContext) -> CommerceRuntime:
    service = context.tool_resources.get("commerce")
    if service is None:
        raise ToolRuntimeError(
            "DEPENDENCY_MISSING",
            "Commerce runtime is not available.",
            {"commerce_code": "COMMERCE_RUNTIME_UNAVAILABLE"},
        )
    if context.subject_id != COMMERCE_LOCAL_SUBJECT_ID:
        raise ToolRuntimeError(
            "POLICY_DENIED",
            "Commerce execution requires the trusted local subject.",
            {"commerce_code": "SUBJECT_UNAVAILABLE"},
        )
    return cast(CommerceRuntime, service)


def _result(method: str, state: str, data: dict[str, Any]) -> dict[str, Any]:
    payload = {"ok": True, "state": state, "data": data}
    payload["content"] = "\n".join(
        commerce_tool_result_lines(f"commerce.{method}", data)
    )
    payload["requires_user_takeover"] = state == "handoff_required"
    return payload


class _StrictArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProductInspectArgs(_StrictArgs):
    kind: Literal["product"]
    product_id: str = Field(min_length=1)
    offer_id: str | None = Field(default=None, min_length=1)


class CheckoutInspectArgs(_StrictArgs):
    kind: Literal["checkout"]
    preparation_ref: str = Field(min_length=1)


class OrderInspectArgs(_StrictArgs):
    kind: Literal["order"]
    local_order_ref: str = Field(min_length=1)


class ShipmentInspectArgs(_StrictArgs):
    kind: Literal["shipment"]
    local_order_ref: str = Field(min_length=1)
    shipment_id: str = Field(min_length=1)


class OrderActionsInspectArgs(_StrictArgs):
    kind: Literal["order_actions"]
    local_order_ref: str = Field(min_length=1)
    request_id: str | None = Field(default=None, min_length=1)


InspectRequest = Annotated[
    ProductInspectArgs
    | CheckoutInspectArgs
    | OrderInspectArgs
    | ShipmentInspectArgs
    | OrderActionsInspectArgs,
    Field(discriminator="kind"),
]


class CommerceInspectArgs(RootModel[InspectRequest]):
    root: InspectRequest


class CommercePrepareOrderArgs(_StrictArgs):
    items: tuple[RequestedItem, ...] = Field(min_length=1)
    promotion_code: str | None = Field(default=None, min_length=1)

    @field_validator("items", mode="before")
    @classmethod
    def decode_items(cls, value: Any) -> Any:
        return json.loads(value) if isinstance(value, str) else value


def canonical_prepare_args(args: dict[str, Any]) -> dict[str, Any]:
    parsed = CommercePrepareOrderArgs.model_validate(canonical_commerce_args(args))
    return canonical_commerce_args(parsed.model_dump())


class CommercePlaceOrderArgs(_StrictArgs):
    preparation_ref: str = Field(min_length=1)
    preparation: OrderPreparation
    preparation_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class CommercePrepareOrderActionArgs(_StrictArgs):
    local_order_ref: str = Field(min_length=1)
    order_revision: str = Field(min_length=1)
    kind: Literal[
        "cancel", "partial_cancel", "return", "partial_return", "refund_request"
    ]
    line_item_ids: tuple[str, ...] = ()
    quantity: int | None = Field(default=None, ge=1)
    reason: str | None = Field(default=None, min_length=1, max_length=120)
    refund_method: Literal["original_payment_method", "store_credit"] | None = None


class CommerceApplyOrderActionArgs(_StrictArgs):
    action_ref: str = Field(min_length=1)
    preparation: OrderActionPreparation
    action_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


_PROVIDER_ERROR_MAP: dict[str, tuple[ErrorCode, str]] = {
    "NOT_FOUND": ("NOT_FOUND", "ORDER_NOT_FOUND"),
    "STALE_REVISION": ("INVALID_REQUEST", "STALE_PREPARATION"),
    "STALE_PREPARATION": ("INVALID_REQUEST", "STALE_PREPARATION"),
    "DIGEST_MISMATCH": ("INVALID_REQUEST", "STALE_PREPARATION"),
    "UNSUPPORTED_ORDER": ("INVALID_REQUEST", "UNSUPPORTED_ORDER"),
    "CHECKOUT_NOT_READY": ("INVALID_REQUEST", "CHECKOUT_NOT_READY"),
    "MERCHANT_MISMATCH": ("INVALID_RESPONSE", "MERCHANT_MISMATCH"),
    "PROVIDER_UNAVAILABLE": ("UPSTREAM_ERROR", "PROVIDER_UNAVAILABLE"),
    "ACTION_INELIGIBLE": ("INVALID_REQUEST", "UNSUPPORTED_ORDER"),
    "ACTION_ALREADY_OPEN": ("INVALID_REQUEST", "ACTION_ALREADY_OPEN"),
}


def _raise_provider_error(exc: CommerceProviderError) -> NoReturn:
    code, commerce_code = _PROVIDER_ERROR_MAP.get(
        exc.code, ("UPSTREAM_ERROR", "PROVIDER_UNAVAILABLE")
    )
    raise ToolRuntimeError(
        code,
        "The commerce provider could not complete the request.",
        {"commerce_code": commerce_code},
    ) from exc


def _emit_action(
    ctx: RuntimeContext,
    *,
    action: str,
    outcome: str,
    order_value: object,
) -> None:
    authorization = ctx.policy_authorization
    if authorization is None:
        return
    emit_tool_execution_event(
        ctx=ctx,
        event_type=TOOL_COMMERCE_ACTION,
        status=outcome,
        payload={
            "tool_name": ctx.tool_name or "commerce",
            "action": action,
            "outcome": outcome,
            "subject_id": ctx.subject_id,
            "policy_approval_id": authorization.approval_id,
            "policy_grant_id": authorization.grant_id,
            "invocation_id": authorization.invocation_hash,
            "order_id": commerce_digest(order_value),
            "attempt_id": commerce_digest(
                {
                    "invocation_id": authorization.invocation_hash,
                    "tool_call_id": ctx.tool_call_id,
                    "action": action,
                }
            ),
            "task_id": ctx.project_task_id or "",
        },
    )


def _h_inspect(args: dict[str, Any], ctx: RuntimeContext) -> dict[str, Any]:
    runtime = resolve_commerce_runtime(ctx)
    try:
        inspection = runtime.inspect_public(args)
    except PermissionError as exc:
        raise ToolRuntimeError(
            "POLICY_DENIED",
            str(exc),
            {"commerce_code": "ORDER_ACCESS_DENIED"},
        ) from exc
    except LookupError as exc:
        raise ToolRuntimeError(
            "NOT_FOUND",
            str(exc),
            {"commerce_code": "ORDER_NOT_FOUND"},
        ) from exc
    except CommerceProviderError as exc:
        _raise_provider_error(exc)
    return _result("inspect", "succeeded", inspection.model_dump(mode="json"))


def _require_authorization(
    args: dict[str, Any], ctx: RuntimeContext, *, method: str, mutation: str
) -> None:
    authorization = ctx.policy_authorization
    expected_hash = stable_invocation_hash(
        tool="commerce",
        method=method,
        args=canonical_commerce_args(args),
    )
    if (
        authorization is None
        or authorization.tool != "commerce"
        or authorization.method != method
        or authorization.duration_type != "once"
        or authorization.subject_id != ctx.subject_id
        or authorization.session_id != ctx.session_id
        or authorization.invocation_hash != expected_hash
    ):
        raise ToolRuntimeError(
            TOOL_ERROR_CONFIRM_REQUIRED,
            f"Exact one-time commerce {mutation} approval is required.",
            {
                "commerce_code": TOOL_ERROR_CONFIRM_REQUIRED,
                "mutation_kind": {
                    "place_order": "place",
                    "apply_order_action": "order_action",
                }.get(method, "prepare"),
                "provider_attempts": 0,
                "recovery_required": False,
            },
        )


def _h_prepare_order(args: dict[str, Any], ctx: RuntimeContext) -> dict[str, Any]:
    runtime = resolve_commerce_runtime(ctx)
    _require_authorization(args, ctx, method="prepare_order", mutation="preparation")
    try:
        preparation = runtime.prepare_public(args)
    except CommerceOutcomeUnknown:
        _emit_action(
            ctx,
            action="prepare_order",
            outcome="outcome_unknown",
            order_value=args,
        )
        return _result(
            "prepare_order",
            "outcome_unknown",
            {
                "commerce_code": "OUTCOME_UNKNOWN",
                "mutation_kind": "prepare",
                "provider_attempts": 1,
                "recovery_required": True,
            },
        )
    except CommerceProviderError as exc:
        _emit_action(ctx, action="prepare_order", outcome="failed", order_value=args)
        _raise_provider_error(exc)
    _emit_action(
        ctx,
        action="prepare_order",
        outcome="accepted",
        order_value=preparation.preparation_ref,
    )
    return _result(
        "prepare_order",
        preparation.state,
        {
            **preparation.model_dump(mode="json"),
            "mutation_kind": "prepare",
            "provider_attempts": 1,
            "recovery_required": False,
        },
    )


def _h_place_order(args: dict[str, Any], ctx: RuntimeContext) -> dict[str, Any]:
    runtime = resolve_commerce_runtime(ctx)
    _require_authorization(args, ctx, method="place_order", mutation="placement")
    try:
        authorization = ctx.policy_authorization
        assert authorization is not None
        result = runtime.place_public(
            args,
            authorization_hash=authorization.invocation_hash,
            caller_agent_id=str(ctx.agent_id or ""),
        )
    except PermissionError as exc:
        raise ToolRuntimeError(
            "POLICY_DENIED",
            str(exc),
            {"commerce_code": "ORDER_ACCESS_DENIED"},
        ) from exc
    except CommerceOutcomeUnknown:
        _emit_action(
            ctx,
            action="place_order",
            outcome="outcome_unknown",
            order_value=args["preparation_ref"],
        )
        return _result(
            "place_order",
            "outcome_unknown",
            {
                "commerce_code": "OUTCOME_UNKNOWN",
                "mutation_kind": "place",
                "provider_attempts": 1,
                "recovery_required": True,
            },
        )
    except CommerceProviderError as exc:
        _emit_action(
            ctx,
            action="place_order",
            outcome="failed",
            order_value=args["preparation_ref"],
        )
        _raise_provider_error(exc)
    data = result.model_dump(mode="json")
    _emit_action(
        ctx,
        action="place_order",
        outcome="accepted",
        order_value=data.get("order_ref") or args["preparation_ref"],
    )
    if data.get("state") == "handoff_required":
        return _result("place_order", "handoff_required", data)
    return _result(
        "place_order",
        result.state,
        {
            **data,
            "mutation_kind": "place",
            "provider_attempts": 1,
            "recovery_required": False,
        },
    )


def _h_prepare_order_action(
    args: dict[str, Any], ctx: RuntimeContext
) -> dict[str, Any]:
    runtime = resolve_commerce_runtime(ctx)
    try:
        preparation = runtime.prepare_action_public(args)
    except PermissionError as exc:
        raise ToolRuntimeError(
            "POLICY_DENIED",
            str(exc),
            {"commerce_code": "ORDER_ACCESS_DENIED"},
        ) from exc
    except CommerceProviderError as exc:
        _raise_provider_error(exc)
    return _result(
        "prepare_order_action",
        (
            "handoff_required"
            if isinstance(preparation, CommerceHandoff)
            else "prepared"
        ),
        preparation.model_dump(mode="json"),
    )


def _h_apply_order_action(args: dict[str, Any], ctx: RuntimeContext) -> dict[str, Any]:
    runtime = resolve_commerce_runtime(ctx)
    _require_authorization(
        args,
        ctx,
        method="apply_order_action",
        mutation="order action",
    )
    try:
        authorization = ctx.policy_authorization
        assert authorization is not None
        result = runtime.apply_action_public(
            args,
            authorization_hash=authorization.invocation_hash,
        )
    except PermissionError as exc:
        raise ToolRuntimeError(
            "POLICY_DENIED",
            str(exc),
            {"commerce_code": "ORDER_ACCESS_DENIED"},
        ) from exc
    except CommerceOutcomeUnknown:
        _emit_action(
            ctx,
            action="apply_order_action",
            outcome="outcome_unknown",
            order_value=args["action_ref"],
        )
        return _result(
            "apply_order_action",
            "outcome_unknown",
            {
                "commerce_code": "OUTCOME_UNKNOWN",
                "mutation_kind": "order_action",
                "provider_attempts": 1,
                "recovery_required": True,
            },
        )
    except CommerceProviderError as exc:
        _emit_action(
            ctx,
            action="apply_order_action",
            outcome="failed",
            order_value=args["action_ref"],
        )
        _raise_provider_error(exc)
    _emit_action(
        ctx,
        action="apply_order_action",
        outcome=result.state,
        order_value=result.order_ref,
    )
    return _result(
        "apply_order_action",
        result.state,
        {
            **result.model_dump(mode="json"),
            "mutation_kind": "order_action",
            "provider_attempts": 1,
            "recovery_required": result.state == "outcome_unknown",
        },
    )


__all__ = [
    "CheckoutInspectArgs",
    "CommerceApplyOrderActionArgs",
    "CommerceInspectArgs",
    "CommercePlaceOrderArgs",
    "CommercePrepareOrderArgs",
    "CommercePrepareOrderActionArgs",
    "OrderActionsInspectArgs",
    "OrderInspectArgs",
    "ProductInspectArgs",
    "ShipmentInspectArgs",
]
