"""Serper search provider registration."""

from openminion.modules.tool.framework import build_registrar

from .family import SEARCH_SERPER_FAMILY

REGISTRAR = build_registrar(SEARCH_SERPER_FAMILY)

__all__ = ["REGISTRAR"]
