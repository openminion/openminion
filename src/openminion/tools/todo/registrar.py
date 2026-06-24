from typing import Any

from openminion.modules.tool.framework import build_registrar

from .family import TODO_FAMILY

REGISTRAR: Any = build_registrar(TODO_FAMILY)
register = REGISTRAR.register

__all__ = ["REGISTRAR", "register"]
