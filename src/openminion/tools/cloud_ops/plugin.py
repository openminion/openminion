from typing import Any, cast

from openminion.tools.ops.contracts import SsmTarget
from openminion.tools.ops.service import OpsService
from openminion.tools.ops.specialized import make_live_handler
from openminion.tools.ops.transports import SsmTransport

from .args import SsmCommandStatusArgs, SsmInventoryArgs


def _live(operation: str):
    def handler(args: Any, ctx: Any) -> dict[str, Any]:
        service = getattr(ctx, "ops_service", None)
        if not isinstance(service, OpsService):
            raise RuntimeError("configured operations service is unavailable")
        target_id = str(args.target_id)
        target = cast(SsmTarget, service.inspect_target(target_id))
        transport = cast(
            SsmTransport,
            service.transport_for(target_id, expected_kind="ssm"),
        )
        return transport.inspect_resource(
            target,
            operation,
            args.model_dump(mode="json"),
        )

    return handler


_h_ssm_inventory = make_live_handler(
    "cloud_ops", "ssm_inventory", SsmInventoryArgs, _live("ssm_inventory")
)
_h_ssm_command_status = make_live_handler(
    "cloud_ops",
    "ssm_command_status",
    SsmCommandStatusArgs,
    _live("ssm_command_status"),
)
