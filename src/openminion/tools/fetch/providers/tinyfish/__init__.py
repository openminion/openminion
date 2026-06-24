from typing import Any

from .family import FETCH_TINYFISH_FAMILY
from .plugin import register_fetch_provider
from .provider import TinyFishFetchProvider
from .registrar import REGISTRAR

REGISTRAR: Any

__all__ = [
    "FETCH_TINYFISH_FAMILY",
    "REGISTRAR",
    "TinyFishFetchProvider",
    "register_fetch_provider",
]
