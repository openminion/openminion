from typing import Any, Protocol, TypedDict

FETCH_SCRAPLING_PLUGIN_INTERFACE_VERSION = "v1"


class ProviderCapabilities(TypedDict):
    render: list[str]
    extract: list[str]
    anti_bot: list[str]
    concurrency: list[str]


class FetchProviderProtocol(Protocol):
    name: str
    capabilities: ProviderCapabilities

    def fetch(
        self, request: dict[str, Any], ctx: Any | None = None
    ) -> dict[str, Any]: ...


__all__ = [
    "FETCH_SCRAPLING_PLUGIN_INTERFACE_VERSION",
    "ProviderCapabilities",
    "FetchProviderProtocol",
]
