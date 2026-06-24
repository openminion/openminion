from typing import TYPE_CHECKING, Any

from .registrar import REGISTRAR as _REGISTRAR

if TYPE_CHECKING:
    from openminion.modules.tool.runtime.registrar import ToolModuleRegistrar

REGISTRAR: "ToolModuleRegistrar" = _REGISTRAR


def register(*args: Any, **kwargs: Any):
    from .plugin import register as register_impl

    return register_impl(*args, **kwargs)


def register_provider(*args: Any, **kwargs: Any):
    from .providers import register_provider as register_provider_impl

    return register_provider_impl(*args, **kwargs)


__all__ = ["REGISTRAR", "register", "register_provider"]
