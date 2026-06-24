from typing import TYPE_CHECKING

from .family import TODO_FAMILY
from .registrar import REGISTRAR as _REGISTRAR, register

if TYPE_CHECKING:
    from openminion.modules.tool.runtime.registrar import ToolModuleRegistrar

REGISTRAR: "ToolModuleRegistrar" = _REGISTRAR

__all__ = ["TODO_FAMILY", "REGISTRAR", "register"]
