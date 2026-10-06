"""Commerce tool registrar."""

from openminion.modules.tool.framework import build_registrar

from .family import COMMERCE_FAMILY

REGISTRAR = build_registrar(COMMERCE_FAMILY)

__all__ = ["REGISTRAR"]
