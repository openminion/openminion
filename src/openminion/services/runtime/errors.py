class RuntimeBootstrapError(RuntimeError):
    """Runtime composition failed before a usable service was built."""


class PluginActivationError(RuntimeBootstrapError):
    """Plugin activation failed a runtime trust or security policy."""


__all__ = ["PluginActivationError", "RuntimeBootstrapError"]
