from openminion.modules.tool.framework import build_registrar

from .family import GITOPS_FAMILY

REGISTRAR = build_registrar(GITOPS_FAMILY)
