from typing import Any

from openminion.modules.tool.framework import build_registrar

from .family import GIT_FAMILY

REGISTRAR: Any = build_registrar(GIT_FAMILY)
register = REGISTRAR.register

__all__ = ["REGISTRAR", "register"]
