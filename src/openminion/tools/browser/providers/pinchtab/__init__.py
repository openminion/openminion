from .registrar import BrowserPinchTabRegistrar, REGISTRAR as _REGISTRAR
from .plugin import PinchTabPlugin, provider_id, register_browser_provider

REGISTRAR: BrowserPinchTabRegistrar = _REGISTRAR

__all__ = ["REGISTRAR", "PinchTabPlugin", "provider_id", "register_browser_provider"]
