from .registrar import FetchRegistrar, REGISTRAR as _REGISTRAR
from .plugin import register
from .providers import register_provider

REGISTRAR: FetchRegistrar = _REGISTRAR

__all__ = ["REGISTRAR", "register", "register_provider"]
