from openminion.modules.tool.framework import build_registrar

from .family import SEARCH_TINYFISH_FAMILY

REGISTRAR = build_registrar(SEARCH_TINYFISH_FAMILY)

__all__ = ["REGISTRAR"]
