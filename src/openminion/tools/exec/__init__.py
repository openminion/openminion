from openminion.modules.tool import ToolModuleRegistrar

from .registrar import REGISTRAR as _REGISTRAR

REGISTRAR: ToolModuleRegistrar = _REGISTRAR

__all__ = ["REGISTRAR"]
