from openminion.modules.tool import ToolModuleRegistrar

from .family import CLOUD_OPS_FAMILY
from .interfaces import ALL_CLOUD_OPS_TOOLS
from .registrar import REGISTRAR as _REGISTRAR

REGISTRAR: ToolModuleRegistrar = _REGISTRAR

__all__ = ["ALL_CLOUD_OPS_TOOLS", "CLOUD_OPS_FAMILY", "REGISTRAR"]
