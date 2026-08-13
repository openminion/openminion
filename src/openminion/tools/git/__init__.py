from typing import Any

from .family import GIT_FAMILY
from .registrar import REGISTRAR as _REGISTRAR, register

REGISTRAR: Any = _REGISTRAR

__all__ = ["GIT_FAMILY", "REGISTRAR", "register"]
