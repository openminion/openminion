import logging
from typing import Callable

from openminion.base.config import OpenMinionConfig
from openminion.modules.llm.providers.base import LLMProvider, ProviderError

ProviderBuilder = Callable[[OpenMinionConfig, logging.Logger], LLMProvider]


class ProviderRegistry:
    def __init__(self) -> None:
        self._builders: dict[str, ProviderBuilder] = {}

    def register(self, name: str, builder: ProviderBuilder) -> None:
        key = name.strip().lower()
        if not key:
            raise ProviderError("Provider name cannot be empty")
        self._builders[key] = builder

    def names(self) -> list[str]:
        return sorted(self._builders)

    def build(
        self, name: str, config: OpenMinionConfig, logger: logging.Logger
    ) -> LLMProvider:
        key = (name or "").strip().lower()
        builder = self._builders.get(key)
        if builder is None:
            available = ", ".join(self.names()) or "none"
            raise ProviderError(
                f"Unknown provider '{key}'. Supported providers: {available}"
            )
        return builder(config, logger)
