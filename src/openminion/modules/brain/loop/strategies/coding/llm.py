from typing import Any

from openminion.modules.brain.loop.tools.runtime import (
    DefaultAdaptiveToolLoopLLMRuntime,
    _unwrap_llm_client,
)
from openminion.modules.brain.loop.tools.contracts import (
    AdaptiveToolLoopRuntimeUnavailableError,
)

from .contracts import CodingRuntimeUnavailableError


class DefaultCodingLLMRuntime(DefaultAdaptiveToolLoopLLMRuntime):
    @classmethod
    def from_adapter(cls, llm_adapter: Any) -> "DefaultCodingLLMRuntime":
        client = _unwrap_llm_client(llm_adapter)
        if client is None:
            raise CodingRuntimeUnavailableError(
                "Coding mode requires a raw LLMClient.complete(...) or "
                f"OpenMinionLLMClient.call(...) path but "
                f"ctx.llm_adapter ({type(llm_adapter).__name__!r}) does not expose one. "
                "Ensure the brain is wired with a LlmctlAdapter that holds an LLMClient "
                "or OpenMinionLLMClient."
            )
        try:
            return cls(client)
        except AdaptiveToolLoopRuntimeUnavailableError as exc:
            raise CodingRuntimeUnavailableError(str(exc)) from exc
