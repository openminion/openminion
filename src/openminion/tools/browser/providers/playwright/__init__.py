"""Public exports for tools browser providers playwright."""

from .registrar import BrowserPlaywrightRegistrar, REGISTRAR as _REGISTRAR
from .plugin import provider_from_config, register
from .provider import PlaywrightProvider, PlaywrightProviderConfig

REGISTRAR: BrowserPlaywrightRegistrar = _REGISTRAR

__all__ = [
    "REGISTRAR",
    "PlaywrightProvider",
    "PlaywrightProviderConfig",
    "provider_from_config",
    "register",
]
