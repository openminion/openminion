"""Graph tool registrar."""

from openminion.modules.tool.framework import build_registrar

from .family import GRAPH_FAMILY

REGISTRAR = build_registrar(GRAPH_FAMILY)

__all__ = ["REGISTRAR"]
