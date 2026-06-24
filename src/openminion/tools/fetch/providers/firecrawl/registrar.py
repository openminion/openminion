"""Firecrawl fetch provider registration."""

from typing import Any

from openminion.modules.tool.framework import build_registrar

from .family import FETCH_FIRECRAWL_FAMILY

REGISTRAR: Any = build_registrar(FETCH_FIRECRAWL_FAMILY)

__all__ = ["REGISTRAR"]
