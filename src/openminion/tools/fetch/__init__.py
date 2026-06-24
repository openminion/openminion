from openminion.modules.tool.runtime.registrar import ToolModuleRegistrar

from .registrar import REGISTRAR
from .plugin import register
from .providers import register_provider

REGISTRAR: ToolModuleRegistrar

__all__ = ["REGISTRAR", "register", "register_provider"]
