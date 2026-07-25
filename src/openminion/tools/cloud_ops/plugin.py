from openminion.tools.ops.specialized import make_handler

from .args import (
    SsmInventoryArgs,
    SsmCommandStatusArgs,
)

_h_ssm_inventory = make_handler("cloud_ops", "ssm_inventory", SsmInventoryArgs)
_h_ssm_command_status = make_handler(
    "cloud_ops", "ssm_command_status", SsmCommandStatusArgs
)
