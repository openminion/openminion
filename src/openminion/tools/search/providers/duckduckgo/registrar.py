from openminion.modules.tool.framework import build_registrar

from .family import SEARCH_DUCKDUCKGO_FAMILY

REGISTRAR = build_registrar(SEARCH_DUCKDUCKGO_FAMILY)

__all__ = ["REGISTRAR"]
