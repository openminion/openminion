from importlib import import_module
from typing import Any

__all__ = [
    "LlmctlAdapter",
    "LocalLLMAdapter",
    "_extract_structured_output",
    "normalize",
    "provider_retry_override_table",
    "request",
    "resolve_provider_retry_override",
]

_EXPORT_MAP = {
    "LlmctlAdapter": (".runtime", "LlmctlAdapter"),
    "LocalLLMAdapter": (".local", "LocalLLMAdapter"),
    "_extract_structured_output": (".runtime", "_extract_structured_output"),
    "provider_retry_override_table": (
        ".overrides",
        "provider_retry_override_table",
    ),
    "resolve_provider_retry_override": (
        ".overrides",
        "resolve_provider_retry_override",
    ),
}

_MODULE_EXPORTS = {
    "normalize": ".normalize",
    "request": ".request",
}


def __getattr__(name: str) -> Any:
    target = _EXPORT_MAP.get(name)
    if target is not None:
        module_name, attr_name = target
        module = import_module(module_name, __name__)
        value = getattr(module, attr_name)
        globals()[name] = value
        return value
    module_name = _MODULE_EXPORTS.get(name)
    if module_name is not None:
        module = import_module(module_name, __name__)
        globals()[name] = module
        return module
    raise AttributeError(name)
