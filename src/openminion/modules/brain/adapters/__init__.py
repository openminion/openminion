from importlib import import_module
from typing import Any

__all__ = [
    "ContextCtlAdapter",
    "LlmctlAdapter",
    "RLMAdapter",
    "SessctlAdapter",
    "create_session_adapter",
]

_EXPORT_MAP = {
    "create_session_adapter": (".factory", "create_session_adapter"),
    "SessctlAdapter": (".session", "SessctlAdapter"),
    "ContextCtlAdapter": (".context", "ContextCtlAdapter"),
    "LlmctlAdapter": (".llm", "LlmctlAdapter"),
    "RLMAdapter": (".rlm", "RLMAdapter"),
}


def __getattr__(name: str) -> Any:
    target = _EXPORT_MAP.get(name)
    if target is None:
        raise AttributeError(name)
    module_name, attr_name = target
    module = import_module(module_name, __name__)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value
