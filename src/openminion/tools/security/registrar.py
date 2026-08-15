"""Security tool-family registrar."""

from openminion.modules.tool.framework import build_registrar

from .family import SECURITY_FAMILY

REGISTRAR = build_registrar(SECURITY_FAMILY)
