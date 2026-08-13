from typing import Any

from .family import GITOPS_FAMILY
from .interfaces import ALL_GITOPS_TOOLS
from .registrar import REGISTRAR as _REGISTRAR

REGISTRAR: Any = _REGISTRAR

__all__ = ["ALL_GITOPS_TOOLS", "GITOPS_FAMILY", "REGISTRAR"]
