from typing import TYPE_CHECKING

from .plugin import collect_host_metrics, register
from .registrar import REGISTRAR as _REGISTRAR

if TYPE_CHECKING:
    from openminion.modules.tool.runtime.registrar import ToolModuleRegistrar

REGISTRAR: "ToolModuleRegistrar" = _REGISTRAR


__all__ = ["REGISTRAR", "collect_host_metrics", "register"]
