from __future__ import annotations

from openminion.tools.fetch.interfaces import (
    FETCH_PLUGIN_INTERFACE_VERSION,
    FetchProviderProtocol,
    ProviderCapabilities,
    ProviderResult,
)


class _DummyProvider(FetchProviderProtocol):
    name = "dummy"
    capabilities: ProviderCapabilities = {
        "render": ["none"],
        "extract": ["none"],
        "formats": ["text/plain"],
    }

    def fetch(self, request: dict, ctx=None) -> ProviderResult:
        del ctx
        return {
            "ok": True,
            "final_url": str(request.get("url", "")),
            "status_code": 200,
            "content_type": "text/plain",
            "content_bytes": 0,
            "raw_body": b"",
            "warnings": [],
        }


def test_interface_version_marker() -> None:
    assert FETCH_PLUGIN_INTERFACE_VERSION == "v1"


def test_provider_protocol_shape() -> None:
    provider = _DummyProvider()
    result = provider.fetch({"url": "https://example.com"})
    assert provider.name == "dummy"
    assert "none" in provider.capabilities["render"]
    assert result["ok"] is True
    assert result["status_code"] == 200
