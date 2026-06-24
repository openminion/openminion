from openminion.modules.tool.runtime.registrar import ToolModuleRegistrar

from .registrar import REGISTRAR

from .plugin import PinchTabPlugin, provider_id, register_browser_provider

REGISTRAR: ToolModuleRegistrar

__all__ = ["REGISTRAR", "PinchTabPlugin", "provider_id", "register_browser_provider"]
