from typing import Any

from .modes import mode_is_local, raise_if_strict


def _sanitize_llm_config(config: Any) -> Any:
    if not isinstance(config, dict):
        return {} if config is None else config
    return {key: value for key, value in config.items() if value is not None}


def create_llm_adapter(
    mode: str = "auto",
    config: Any = None,
    telemetryctl: Any | None = None,
) -> Any:
    from openminion.modules.brain.adapters.llm import LocalLLMAdapter

    if mode_is_local(mode):
        return LocalLLMAdapter()
    try:
        from openminion.modules.llm.runtime.client import LLMCTL

        llmctl = LLMCTL.from_config(
            _sanitize_llm_config(config),
            telemetryctl=telemetryctl,
        )
        from ..llm import LlmctlAdapter

        return LlmctlAdapter(llmctl.client())
    except ImportError:
        raise_if_strict(mode)
        return LocalLLMAdapter()
