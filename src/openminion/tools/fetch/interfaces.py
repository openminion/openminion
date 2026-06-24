from typing import Any, Protocol, TypedDict

FETCH_PLUGIN_INTERFACE_VERSION = "v1"


class FetchRequest(TypedDict, total=False):
    url: str
    method: str
    headers: dict[str, str]
    timeout_ms: int
    max_bytes: int
    max_redirects: int
    follow_redirects: bool
    accept: str
    prefer_backend: str
    render: str
    extract: dict[str, Any]
    provider_options: dict[str, Any]


class FetchArtifactRefs(TypedDict, total=False):
    raw_body: str
    extracted_text: str
    metadata_json: str


class FetchResponse(TypedDict, total=False):
    ok: bool
    final_url: str
    status_code: int
    content_type: str
    backend: str
    text_preview: str
    warnings: list[str]
    verified: bool
    artifacts: FetchArtifactRefs
    error: dict[str, Any]


class ProviderCapabilities(TypedDict):
    render: list[str]
    extract: list[str]
    formats: list[str]


class ProviderResult(TypedDict, total=False):
    ok: bool
    final_url: str
    status_code: int
    headers: dict[str, str]
    content_type: str
    content_bytes: int
    raw_body: bytes | str
    extracted_text: str
    title: str
    language: str
    warnings: list[str]
    meta: dict[str, Any]
    error: dict[str, Any]


class FetchProviderProtocol(Protocol):
    name: str
    capabilities: ProviderCapabilities

    def fetch(
        self, request: FetchRequest, ctx: Any | None = None
    ) -> ProviderResult: ...


__all__ = [
    "FETCH_PLUGIN_INTERFACE_VERSION",
    "FetchArtifactRefs",
    "FetchProviderProtocol",
    "FetchRequest",
    "FetchResponse",
    "ProviderCapabilities",
    "ProviderResult",
]
