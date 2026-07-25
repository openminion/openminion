from typing import TYPE_CHECKING

from .family import GITOPS_FAMILY
from .interfaces import ALL_GITOPS_TOOLS
from .registrar import REGISTRAR as _REGISTRAR

if TYPE_CHECKING:
    from openminion.modules.tool.runtime.registrar import ToolModuleRegistrar

REGISTRAR: "ToolModuleRegistrar" = _REGISTRAR

__all__ = ["ALL_GITOPS_TOOLS", "GITOPS_FAMILY", "REGISTRAR"]
