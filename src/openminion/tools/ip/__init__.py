from typing import TYPE_CHECKING

from .providers import register_provider
from .registrar import REGISTRAR as _REGISTRAR

if TYPE_CHECKING:
    from openminion.modules.tool.registry import ToolRegistry
    from openminion.modules.tool.runtime.registrar import ToolModuleRegistrar

REGISTRAR: "ToolModuleRegistrar" = _REGISTRAR


def register(registry: "ToolRegistry") -> None:
    from .plugin import register as register_impl

    register_impl(registry)


__all__ = ["REGISTRAR", "register", "register_provider"]
