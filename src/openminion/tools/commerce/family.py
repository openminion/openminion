"""Declarative commerce tool family."""

from functools import partial

from openminion.modules.tool.exposure import ToolExposureProfile, ToolRiskAnnotations
from openminion.modules.tool.framework import ToolDecl, ToolFamilySpec

from .authorization import (
    authorize_commerce_call,
    canonical_commerce_args,
    confirmation_preview,
)

from .interfaces import (
    TOOL_COMMERCE_APPLY_ORDER_ACTION,
    TOOL_COMMERCE_INSPECT,
    TOOL_COMMERCE_PLACE_ORDER,
    TOOL_COMMERCE_PREPARE_ORDER,
    TOOL_COMMERCE_PREPARE_ORDER_ACTION,
    ALL_COMMERCE_TOOLS,
)
from .plugin import (
    CommerceApplyOrderActionArgs,
    CommerceInspectArgs,
    CommercePlaceOrderArgs,
    CommercePrepareOrderArgs,
    CommercePrepareOrderActionArgs,
    _h_apply_order_action,
    _h_inspect,
    _h_place_order,
    _h_prepare_order,
    _h_prepare_order_action,
    canonical_prepare_args,
)

COMMERCE_FAMILY = ToolFamilySpec(
    module_id="commerce",
    min_scope_default="READ_ONLY",
    common_tags=("plugin", "commerce"),
    common_capabilities=("commerce",),
    exposure_profiles=(
        ToolExposureProfile(
            profile_id="commerce_order_care",
            title="Commerce order care",
            summary="Inspect, prepare, place, and manage subject-owned orders.",
            tool_names=frozenset(ALL_COMMERCE_TOOLS),
            risk=ToolRiskAnnotations(
                tier="apply",
                requires_approval=True,
                mutates_state=True,
            ),
            evidence_expectations=(
                "preserve exact approval, order, action, and provider-attempt facts",
            ),
            stop_rules=(
                "stop for unsupported merchant actions or unresolved outcomes",
            ),
            activation_hint=(
                "Activate for a trusted local order task with commerce configured."
            ),
        ),
    ),
    tools=(
        ToolDecl(
            name=TOOL_COMMERCE_INSPECT,
            args_model=CommerceInspectArgs,
            handler=_h_inspect,
            description=(
                "Inspect one product, prepared checkout, local order, shipment, "
                "or order-action surface for the configured merchant."
            ),
            idempotent=True,
            capabilities=("read_only",),
        ),
        ToolDecl(
            name=TOOL_COMMERCE_PREPARE_ORDER,
            canonical_args=canonical_prepare_args,
            confirmation_preview=partial(
                confirmation_preview, tool_name=TOOL_COMMERCE_PREPARE_ORDER
            ),
            policy_authorizer=authorize_commerce_call,
            args_model=CommercePrepareOrderArgs,
            handler=_h_prepare_order,
            description=(
                "After exact one-time approval, create or refresh one reversible "
                "merchant checkout without placing an order or capturing payment."
            ),
            min_scope="WRITE_SAFE",
            dangerous=True,
            idempotent=True,
            block_under_readonly=True,
            capabilities=("write_safe", "external_account"),
        ),
        ToolDecl(
            name=TOOL_COMMERCE_PLACE_ORDER,
            canonical_args=canonical_commerce_args,
            confirmation_preview=partial(
                confirmation_preview, tool_name=TOOL_COMMERCE_PLACE_ORDER
            ),
            policy_authorizer=authorize_commerce_call,
            args_model=CommercePlaceOrderArgs,
            handler=_h_place_order,
            description=(
                "After exact one-time approval, place the subject-owned prepared "
                "order once using its exact reviewed facts."
            ),
            min_scope="POWER_USER",
            dangerous=True,
            idempotent=False,
            block_under_readonly=True,
            capabilities=("write", "external_account", "financial"),
        ),
        ToolDecl(
            name=TOOL_COMMERCE_PREPARE_ORDER_ACTION,
            args_model=CommercePrepareOrderActionArgs,
            handler=_h_prepare_order_action,
            description=(
                "Inspect exact merchant terms for one supported cancellation, "
                "return, or refund request without creating it."
            ),
            idempotent=True,
            capabilities=("read_only", "external_account"),
        ),
        ToolDecl(
            name=TOOL_COMMERCE_APPLY_ORDER_ACTION,
            canonical_args=canonical_commerce_args,
            confirmation_preview=partial(
                confirmation_preview, tool_name=TOOL_COMMERCE_APPLY_ORDER_ACTION
            ),
            policy_authorizer=authorize_commerce_call,
            args_model=CommerceApplyOrderActionArgs,
            handler=_h_apply_order_action,
            description=(
                "After exact one-time approval, submit one prepared order action "
                "using its reviewed merchant terms."
            ),
            min_scope="POWER_USER",
            dangerous=True,
            idempotent=False,
            block_under_readonly=True,
            capabilities=("write", "external_account", "financial"),
        ),
    ),
)

__all__ = ["COMMERCE_FAMILY"]
