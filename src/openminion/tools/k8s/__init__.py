from typing import TYPE_CHECKING

from .family import K8S_FAMILY
from .interfaces import ALL_K8S_TOOLS
from .registrar import REGISTRAR as _REGISTRAR

if TYPE_CHECKING:
    from openminion.modules.tool.runtime.registrar import ToolModuleRegistrar

REGISTRAR: "ToolModuleRegistrar" = _REGISTRAR

__all__ = ["ALL_K8S_TOOLS", "K8S_FAMILY", "REGISTRAR"]
