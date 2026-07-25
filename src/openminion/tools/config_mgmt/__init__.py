from typing import TYPE_CHECKING

from .family import CONFIG_MGMT_FAMILY
from .interfaces import ALL_CONFIG_MGMT_TOOLS
from .registrar import REGISTRAR as _REGISTRAR

if TYPE_CHECKING:
    from openminion.modules.tool.runtime.registrar import ToolModuleRegistrar

REGISTRAR: "ToolModuleRegistrar" = _REGISTRAR

__all__ = ["ALL_CONFIG_MGMT_TOOLS", "CONFIG_MGMT_FAMILY", "REGISTRAR"]
