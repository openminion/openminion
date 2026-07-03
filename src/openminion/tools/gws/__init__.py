from typing import TYPE_CHECKING

from openminion.base.version import OPENMINION_VERSION

from .plugin import GwsToolPlugin, TOOL_DESCRIPTOR, register
from .registrar import REGISTRAR as _REGISTRAR

if TYPE_CHECKING:
    from openminion.modules.tool.runtime.registrar import ToolModuleRegistrar

REGISTRAR: "ToolModuleRegistrar" = _REGISTRAR

__all__ = ["REGISTRAR", "GwsToolPlugin", "TOOL_DESCRIPTOR", "register"]

__version__ = OPENMINION_VERSION
