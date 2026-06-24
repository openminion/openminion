from typing import TYPE_CHECKING

from .plugin import (
    ReactionsPlugin,
    clear_channel_adapters,
    emit_signal_reaction_received,
    register,
    register_channel_adapter,
    unregister_channel_adapter,
)
from .registrar import REGISTRAR as _REGISTRAR

if TYPE_CHECKING:
    from openminion.modules.tool.runtime.registrar import ToolModuleRegistrar

REGISTRAR: "ToolModuleRegistrar" = _REGISTRAR

__all__ = [
    "REGISTRAR",
    "ReactionsPlugin",
    "clear_channel_adapters",
    "emit_signal_reaction_received",
    "register",
    "register_channel_adapter",
    "unregister_channel_adapter",
]
