from openminion.modules.tool.runtime.registrar import ToolModuleRegistrar

from .registrar import REGISTRAR
from .plugin import register
from .provider import provider

REGISTRAR: ToolModuleRegistrar

__all__ = ["REGISTRAR", "register", "provider"]
