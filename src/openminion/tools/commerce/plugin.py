"""Thin typed commerce tool handlers."""

from __future__ import annotations

from typing import Annotated, Any, Literal, NoReturn

from pydantic import BaseModel, ConfigDict, Field, RootModel

from openminion.modules.commerce.provider import (
    CommerceOutcomeUnknown,
    CommerceProviderError,
    RequestedItem,
)
from openminion.modules.tool import RuntimeContext
from openminion.modules.tool.contracts.schemas import ErrorCode
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.plugin_api import stable_invocation_hash
from openminion.modules.tool.runtime.dependencies import resolve_commerce_runtime

from .authorization import canonical_commerce_args


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


_PROVIDER_ERROR_MAP: dict[str, tuple[ErrorCode, str]] = {
    "NOT_FOUND": ("NOT_FOUND", "ORDER_NOT_FOUND"),
    "STALE_REVISION": ("INVALID_REQUEST", "STALE_PREPARATION"),
    "DIGEST_MISMATCH": ("INVALID_REQUEST", "STALE_PREPARATION"),
    "UNSUPPORTED_ORDER": ("INVALID_REQUEST", "UNSUPPORTED_ORDER"),
    "CHECKOUT_NOT_READY": ("INVALID_REQUEST", "CHECKOUT_NOT_READY"),
    "MERCHANT_MISMATCH": ("INVALID_RESPONSE", "MERCHANT_MISMATCH"),
    "PROVIDER_UNAVAILABLE": ("UPSTREAM_ERROR", "PROVIDER_UNAVAILABLE"),
}


def _raise_provider_error(exc: CommerceProviderError) -> NoReturn:
    code, commerce_code = _PROVIDER_ERROR_MAP.get(
        exc.code, ("UPSTREAM_ERROR", "PROVIDER_UNAVAILABLE")
    )
    raise ToolRuntimeError(
        code,
        str(exc),
        {"commerce_code": commerce_code},
    ) from exc


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
    return {
        "ok": True,
        "state": "succeeded",
        "data": inspection.model_dump(mode="json"),
    }


def _require_prepare_authorization(
    args: dict[str, Any], ctx: RuntimeContext
) -> None:
    authorization = ctx.policy_authorization
    expected_hash = stable_invocation_hash(
        tool="commerce",
        method="prepare_order",
        args=canonical_commerce_args(args),
    )
    if (
        authorization is None
        or authorization.tool != "commerce"
        or authorization.method != "prepare_order"
        or authorization.duration_type != "once"
        or authorization.subject_id != ctx.subject_id
        or authorization.session_id != ctx.session_id
        or authorization.invocation_hash != expected_hash
    ):
        raise ToolRuntimeError(
            "CONFIRM_REQUIRED",
            "Exact one-time commerce preparation approval is required.",
            {
                "commerce_code": "CONFIRM_REQUIRED",
                "mutation_kind": "prepare",
                "provider_attempts": 0,
                "recovery_required": False,
            },
        )


def _h_prepare_order(args: dict[str, Any], ctx: RuntimeContext) -> dict[str, Any]:
    runtime = resolve_commerce_runtime(ctx)
    _require_prepare_authorization(args, ctx)
    try:
        preparation = runtime.prepare_public(args)
    except CommerceOutcomeUnknown:
        return {
            "ok": True,
            "state": "outcome_unknown",
            "data": {
                "commerce_code": "OUTCOME_UNKNOWN",
                "mutation_kind": "prepare",
                "provider_attempts": 1,
                "recovery_required": True,
            },
        }
    except CommerceProviderError as exc:
        _raise_provider_error(exc)
    return {
        "ok": True,
        "state": preparation.state,
        "data": {
            **preparation.model_dump(mode="json"),
            "mutation_kind": "prepare",
            "provider_attempts": 1,
            "recovery_required": False,
        },
    }


__all__ = [
    "CheckoutInspectArgs",
    "CommerceInspectArgs",
    "CommercePrepareOrderArgs",
    "OrderActionsInspectArgs",
    "OrderInspectArgs",
    "ProductInspectArgs",
    "ShipmentInspectArgs",
]
