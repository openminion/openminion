"""Regression tests for malformed proxy configuration in the provider transport.

A bracketed IPv6 literal without a port in no_proxy ("[::1]") makes httpx
raise InvalidURL while the shared client is being constructed. Construction
sits inside urlopen's error normalization, and the transport retries with a
locally corrected proxy configuration for that client instance only; the
process environment is never modified.
"""

from __future__ import annotations

import http.server
import os
import threading
from collections.abc import Iterator
from urllib import error as urllib_error
from urllib import request as urllib_request

import httpx
import pytest

from openminion.modules.llm.providers.transport.client import (
    ProviderHTTPClient,
    _sanitize_no_proxy_value,
)

_MALFORMED_NO_PROXY = "localhost,127.0.0.1,::1,[::1],[fd8b:4f84::1]"

_PROXY_VARS = (
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "REQUEST_METHOD",
)


@pytest.fixture(autouse=True)
def _clean_proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in _PROXY_VARS:
        monkeypatch.delenv(var, raising=False)


def _set_env(monkeypatch: pytest.MonkeyPatch, name: str, value: str) -> None:
    monkeypatch.setenv(name, value)
    monkeypatch.setenv(name.upper(), value)


@pytest.fixture()
def recording_proxy() -> Iterator[tuple[str, list[str]]]:
    """A local HTTP proxy that records what it receives and answers 502."""
    seen: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            seen.append(self.path)
            self.send_response(502)
            self.end_headers()

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", seen
    finally:
        server.shutdown()
        server.server_close()


def _request(url: str) -> urllib_request.Request:
    return urllib_request.Request(url, method="GET")


def test_sanitize_rewrites_bracketed_ipv6_literals() -> None:
    sanitized, changed = _sanitize_no_proxy_value(_MALFORMED_NO_PROXY)
    assert sanitized == "localhost,127.0.0.1,::1,::1,fd8b:4f84::1"
    assert changed == ["[::1]", "[fd8b:4f84::1]"]


def test_sanitize_leaves_other_entries_untouched() -> None:
    value = "localhost,127.0.0.1,::1,[::1]:8080,*.example.com"
    sanitized, changed = _sanitize_no_proxy_value(value)
    assert sanitized == value
    assert changed == []


def test_bracketed_ipv6_no_proxy_constructs_and_bypasses(
    monkeypatch: pytest.MonkeyPatch,
    recording_proxy: tuple[str, list[str]],
) -> None:
    proxy_url, seen = recording_proxy
    _set_env(monkeypatch, "all_proxy", proxy_url)
    _set_env(monkeypatch, "no_proxy", _MALFORMED_NO_PROXY)
    env_before = {var: os.environ.get(var) for var in _PROXY_VARS}

    client = ProviderHTTPClient()
    try:
        inner = client._get_client()
        # The corrected configuration keeps [::1] as a bypass entry and
        # still routes other hosts through the proxy.
        assert (
            inner._transport_for_url(httpx.URL("http://[::1]:9/")) is inner._transport
        )
        assert (
            inner._transport_for_url(httpx.URL("http://example.com/"))
            is not inner._transport
        )
        with pytest.raises(urllib_error.URLError):
            client.urlopen(_request("http://[::1]:9/v1/models"), timeout=5)
        assert seen == []
    finally:
        client.close()
    # Construction and the request left the process environment untouched.
    assert {var: os.environ.get(var) for var in _PROXY_VARS} == env_before


def test_normal_no_proxy_behavior_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    recording_proxy: tuple[str, list[str]],
) -> None:
    proxy_url, seen = recording_proxy
    _set_env(monkeypatch, "all_proxy", proxy_url)
    _set_env(monkeypatch, "no_proxy", "localhost,127.0.0.1,::1")

    client = ProviderHTTPClient()
    try:
        # A bypassed host is contacted directly (connection refused on the
        # dead port) and never reaches the proxy.
        with pytest.raises(urllib_error.URLError):
            client.urlopen(_request("http://127.0.0.1:9/v1/models"), timeout=5)
        assert seen == []
        # A non-bypassed host still goes through the proxy, which answers
        # 502 here and surfaces as HTTPError.
        with pytest.raises(urllib_error.HTTPError):
            client.urlopen(_request("http://example.com:9/v1/models"), timeout=5)
        assert len(seen) == 1
        assert "example.com" in seen[0]
    finally:
        client.close()


def test_unfixable_no_proxy_entry_surfaces_as_urlerror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A scheme-form no_proxy entry with an unterminated bracket is not a
    # bracketed literal the transport rewrites; construction keeps failing,
    # and urlopen reports it through URLError instead of raw InvalidURL.
    _set_env(monkeypatch, "no_proxy", "http://[::1")

    client = ProviderHTTPClient()
    try:
        with pytest.raises(httpx.InvalidURL):
            client._get_client()
        with pytest.raises(urllib_error.URLError):
            client.urlopen(_request("http://example.com/v1/models"), timeout=5)
    finally:
        client.close()


def test_unrelated_invalid_proxy_url_is_not_corrected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A malformed proxy URL is unrelated to no_proxy: the transport does not
    # rewrite it, construction raises InvalidURL, and urlopen normalizes it
    # to URLError rather than letting it escape raw.
    _set_env(monkeypatch, "http_proxy", "http://proxy:abc")
    _set_env(monkeypatch, "no_proxy", "localhost,127.0.0.1")

    client = ProviderHTTPClient()
    try:
        with pytest.raises(httpx.InvalidURL):
            client._get_client()
        with pytest.raises(urllib_error.URLError):
            client.urlopen(_request("http://example.com/v1/models"), timeout=5)
    finally:
        client.close()
