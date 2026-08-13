from .plugin import register
from .registrar import CodeRegistrar, REGISTRAR as _REGISTRAR

REGISTRAR: CodeRegistrar = _REGISTRAR

__all__ = ["REGISTRAR", "register"]
