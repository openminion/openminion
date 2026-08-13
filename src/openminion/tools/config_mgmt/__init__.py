from openminion.modules.tool import ToolModuleRegistrar

from .family import CONFIG_MGMT_FAMILY
from .interfaces import ALL_CONFIG_MGMT_TOOLS
from .registrar import REGISTRAR as _REGISTRAR

REGISTRAR: ToolModuleRegistrar = _REGISTRAR

__all__ = ["ALL_CONFIG_MGMT_TOOLS", "CONFIG_MGMT_FAMILY", "REGISTRAR"]
