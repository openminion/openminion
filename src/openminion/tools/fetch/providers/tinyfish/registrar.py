"""TinyFish fetch provider registration."""

from typing import Any

from openminion.modules.tool.framework import build_registrar

from .family import FETCH_TINYFISH_FAMILY

REGISTRAR: Any = build_registrar(FETCH_TINYFISH_FAMILY)

__all__ = ["REGISTRAR"]
