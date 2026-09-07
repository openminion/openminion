class RuntimeBootstrapError(RuntimeError):
    """Runtime composition failed before a usable service was built."""


class PluginActivationError(RuntimeBootstrapError):
    """Plugin activation failed a runtime trust or security policy."""

    def __init__(
        self,
        *,
        plugin_id: str,
        stage: str,
        reason_code: str,
    ) -> None:
        super().__init__("Plugin activation failed.")
        self.plugin_id = plugin_id
        self.stage = stage
        self.reason_code = reason_code


__all__ = ["PluginActivationError", "RuntimeBootstrapError"]
