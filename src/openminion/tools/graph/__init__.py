"""Provider-neutral graph tool family."""

from typing import TYPE_CHECKING

from .family import GRAPH_FAMILY
from .interfaces import ALL_GRAPH_TOOLS
from .registrar import REGISTRAR as _REGISTRAR

if TYPE_CHECKING:
    from openminion.modules.tool.runtime.registrar import ToolModuleRegistrar

REGISTRAR: "ToolModuleRegistrar" = _REGISTRAR

__all__ = ["ALL_GRAPH_TOOLS", "GRAPH_FAMILY", "REGISTRAR"]
