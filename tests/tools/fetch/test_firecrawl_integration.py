from __future__ import annotations

from typing import Any

import pytest

from openminion.tools.fetch.plugin import (
    _choose_provider_name,
    _h_get,
    _hinted_backend_order,
)


class _FailIfCalledProvider:
    name = "firecrawl"
    capabilities = {"render": ["dom"], "extract": ["text"], "formats": ["markdown"]}

    def __init__(self) -> None:
        self.calls = 0

    def fetch(self, request: dict[str, Any], _ctx: Any) -> dict[str, Any]:
        self.calls += 1
        return {"ok": True, "backend": self.name}


class _FacadeRegistryWithFirecrawl:
    def __init__(self) -> None:
        self.firecrawl = _FailIfCalledProvider()

    def list_names(self) -> list[str]:
        return ["core-http", "firecrawl"]

    def get(self, name: str) -> Any:
        assert name == "firecrawl"
        return self.firecrawl

    def list(self) -> list[Any]:
        return [self.firecrawl]


def test_choose_provider_routes_to_firecrawl_on_hint() -> None:
    chosen = _choose_provider_name(
        {
            "url": "https://example.com",
            "provider_options": {"firecrawl": {"formats": ["markdown"]}},
        },
        available={"core-http", "firecrawl"},
    )
    assert chosen == "firecrawl"


def test_choose_provider_falls_back_to_core_http_when_firecrawl_absent() -> None:
    chosen = _choose_provider_name(
        {
            "url": "https://example.com",
            "provider_options": {"firecrawl": {"formats": ["markdown"]}},
        },
        available={"core-http"},
    )
    assert chosen == "core-http"


def test_hinted_backend_order_includes_firecrawl_when_hinted() -> None:
    ordered = _hinted_backend_order(
        {"provider_options": {"firecrawl": {"formats": ["markdown"]}}},
        available={"core-http", "firecrawl"},
    )
    assert "firecrawl" in ordered


def test_choose_provider_keeps_core_http_as_default_with_no_hint() -> None:
    chosen = _choose_provider_name(
        {"url": "https://example.com"},
        available={"core-http", "firecrawl"},
    )
    assert chosen == "core-http"


def test_facade_blocks_ssrf_url_before_firecrawl_provider_is_invoked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # FFC-02 + FFC-03: the shared URL-policy check must fire before Firecrawl
    # gets a chance to make its remote API call. A loopback URL with Firecrawl
    # explicitly selected returns SSRF_BLOCKED and the provider is never called.
    registry = _FacadeRegistryWithFirecrawl()
    monkeypatch.setattr(
        "openminion.tools.fetch.plugin._ensure_provider_registry",
        lambda: registry,
    )

    payload = _h_get(
        {
            "url": "http://127.0.0.1:8080/",
            "prefer_backend": "firecrawl",
        },
        None,
    )

    assert payload["ok"] is False
    assert payload["error"]["code"] == "SSRF_BLOCKED"
    assert registry.firecrawl.calls == 0


def test_facade_blocks_non_http_scheme_before_firecrawl_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _FacadeRegistryWithFirecrawl()
    monkeypatch.setattr(
        "openminion.tools.fetch.plugin._ensure_provider_registry",
        lambda: registry,
    )

    payload = _h_get(
        {
            "url": "file:///etc/passwd",
            "prefer_backend": "firecrawl",
        },
        None,
    )

    assert payload["ok"] is False
    assert payload["error"]["code"] == "SCHEME_NOT_ALLOWED"
    assert registry.firecrawl.calls == 0


def test_bootstrap_entries_order_firecrawl_after_scrapling() -> None:
    # bootstrap order must be fetch -> scrapling -> firecrawl
    from openminion.modules.tool.bootstrap.entries import _TOOL_BOOTSTRAP_ENTRIES

    fetch_entries = [
        entry.module_name
        for entry in _TOOL_BOOTSTRAP_ENTRIES
        if entry.module_name.startswith("openminion.tools.fetch")
    ]
    assert "openminion.tools.fetch" in fetch_entries
    assert "openminion.tools.fetch.providers.firecrawl" in fetch_entries
    assert "openminion.tools.fetch.providers.scrapling" in fetch_entries

    idx_fetch = fetch_entries.index("openminion.tools.fetch")
    idx_scrapling = fetch_entries.index("openminion.tools.fetch.providers.scrapling")
    idx_firecrawl = fetch_entries.index("openminion.tools.fetch.providers.firecrawl")

    assert idx_fetch < idx_scrapling < idx_firecrawl
