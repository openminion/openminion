from openminion.modules.tool.framework import build_registrar

from .family import K8S_FAMILY

REGISTRAR = build_registrar(K8S_FAMILY)
