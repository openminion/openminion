from __future__ import annotations

from typing import TYPE_CHECKING

from .plugin import register
from .registrar import REGISTRAR as _REGISTRAR

if TYPE_CHECKING:
    from openminion.modules.tool.runtime.registrar import ToolModuleRegistrar

REGISTRAR: "ToolModuleRegistrar" = _REGISTRAR

__all__ = ["REGISTRAR", "register"]
