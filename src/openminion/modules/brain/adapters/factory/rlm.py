from typing import Any

from .modes import raise_if_strict, use_local_when_service_missing


def _with_recursive_source(adapter: Any, source: str) -> Any:
    setattr(adapter, "recursive_source", source)
    return adapter


def create_rlm_adapter(mode: str = "auto", service: Any = None) -> Any:
    from openminion.modules.brain.adapters.recursive import LocalRLMAdapter

    if use_local_when_service_missing(mode, service):
        return _with_recursive_source(LocalRLMAdapter(), "local_mock")
    try:
        from ..recursive import RLMAdapter

        return _with_recursive_source(RLMAdapter(service), "real_rlm")
    except ImportError:
        raise_if_strict(mode)
        return _with_recursive_source(LocalRLMAdapter(), "local_mock")
