from __future__ import annotations

import pytest

from openminion.modules.llm.providers.transport.client import (
    ProviderHTTPClient,
    _build_httpx_client,
)


def test_client_builds_with_bracketed_ipv6_no_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    # Regression: runtimes that publish NO_PROXY with bracketed IPv6 literals
    # made httpx raise InvalidURL at client construction, failing every
    # provider call before any request was sent.
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1,::1,[::1],[fd8b:4f84:7d32:99::1]")
    monkeypatch.setenv("no_proxy", "localhost,127.0.0.1,::1,[::1],[fd8b:4f84:7d32:99::1]")

    client = _build_httpx_client()
    client.close()

    pooled = ProviderHTTPClient()
    try:
        assert pooled._get_client() is pooled._get_client()
    finally:
        pooled.close()


def test_client_builds_with_plain_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1")
    monkeypatch.setenv("no_proxy", "localhost,127.0.0.1")

    client = _build_httpx_client()
    client.close()
