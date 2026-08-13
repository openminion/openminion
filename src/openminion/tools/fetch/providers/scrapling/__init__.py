from .plugin import register
from .provider import provider
from .registrar import FetchScraplingRegistrar, REGISTRAR as _REGISTRAR

REGISTRAR: FetchScraplingRegistrar = _REGISTRAR

__all__ = ["REGISTRAR", "register", "provider"]
