"""Declarative commerce tool family."""

from openminion.modules.tool.framework import ToolDecl, ToolFamilySpec

from .interfaces import (
    TOOL_COMMERCE_INSPECT,
    TOOL_COMMERCE_PLACE_ORDER,
    TOOL_COMMERCE_PREPARE_ORDER,
)
from .plugin import (
    CommerceInspectArgs,
    CommercePlaceOrderArgs,
    CommercePrepareOrderArgs,
    _h_inspect,
    _h_place_order,
    _h_prepare_order,
)

COMMERCE_FAMILY = ToolFamilySpec(
    module_id="commerce",
    min_scope_default="READ_ONLY",
    common_tags=("plugin", "commerce"),
    common_capabilities=("commerce",),
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
    ),
)

__all__ = ["COMMERCE_FAMILY"]
