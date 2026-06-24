from __future__ import annotations

import urllib.error

import openminion.tools.fetch.providers.core_http as core_http_module
from openminion.tools.fetch.providers.core_http import CoreHttpFetchProvider, _FetchStep


def test_provider_blocks_non_http_scheme() -> None:
    provider = CoreHttpFetchProvider()
    payload = provider.fetch({"url": "file:///etc/passwd"})
    assert payload["ok"] is False
    assert payload["error"]["code"] == "SCHEME_NOT_ALLOWED"


def test_provider_blocks_loopback_url() -> None:
    provider = CoreHttpFetchProvider()
    payload = provider.fetch({"url": "http://127.0.0.1:8080"})
    assert payload["ok"] is False
    assert payload["error"]["code"] == "SSRF_BLOCKED"


def test_provider_enforces_redirect_limit(monkeypatch) -> None:
    provider = CoreHttpFetchProvider()

    monkeypatch.setattr(
        "openminion.tools.fetch.providers.core_http._enforce_url_policy",
        lambda url, allow_private_hosts=False: object(),
    )
    monkeypatch.setattr(
        "openminion.tools.fetch.providers.core_http._open_once",
        lambda **kwargs: _FetchStep(
            status_code=302,
            final_url=str(kwargs["url"]),
            headers={"location": "https://example.com/next"},
            body=b"",
        ),
    )

    payload = provider.fetch(
        {
            "url": "https://example.com/start",
            "follow_redirects": True,
            "max_redirects": 0,
            "method": "GET",
        }
    )
    assert payload["ok"] is False
    assert payload["error"]["code"] == "REDIRECT_LIMIT_EXCEEDED"


def test_provider_extracts_html_text_and_title(monkeypatch) -> None:
    provider = CoreHttpFetchProvider()

    monkeypatch.setattr(
        "openminion.tools.fetch.providers.core_http._enforce_url_policy",
        lambda url, allow_private_hosts=False: object(),
    )
    monkeypatch.setattr(
        "openminion.tools.fetch.providers.core_http._open_once",
        lambda **kwargs: _FetchStep(
            status_code=200,
            final_url=str(kwargs["url"]),
            headers={"content-type": "text/html; charset=utf-8"},
            body=b"<html lang='en'><head><title>Example Domain</title></head><body><h1>Hello</h1></body></html>",
        ),
    )

    payload = provider.fetch({"url": "https://example.com", "method": "GET"})
    assert payload["ok"] is True
    assert payload["status_code"] == 200
    assert payload["title"] == "Example Domain"
    assert "Hello" in payload["extracted_text"]


def test_provider_head_skips_body(monkeypatch) -> None:
    provider = CoreHttpFetchProvider()

    monkeypatch.setattr(
        "openminion.tools.fetch.providers.core_http._enforce_url_policy",
        lambda url, allow_private_hosts=False: object(),
    )
    monkeypatch.setattr(
        "openminion.tools.fetch.providers.core_http._open_once",
        lambda **kwargs: _FetchStep(
            status_code=200,
            final_url=str(kwargs["url"]),
            headers={"content-type": "text/plain", "content-length": "42"},
            body=b"",
        ),
    )

    payload = provider.fetch({"url": "https://example.com", "method": "HEAD"})
    assert payload["ok"] is True
    assert payload["status_code"] == 200
    assert payload["content_bytes"] == 0


def test_open_once_maps_timeout_error() -> None:
    class _TimeoutOpener:
        def open(self, _request, timeout=None):  # type: ignore[no-untyped-def]
            del timeout
            raise urllib.error.URLError(TimeoutError("timed out"))

    original_build_opener = core_http_module.urllib.request.build_opener
    core_http_module.urllib.request.build_opener = lambda *_args, **_kwargs: (
        _TimeoutOpener()
    )  # type: ignore[assignment]
    try:
        try:
            core_http_module._open_once(
                url="https://example.com",
                method="GET",
                headers={},
                timeout_ms=1000,
                max_bytes=1024,
                read_body=True,
            )
            assert False, "expected timeout error"
        except Exception as exc:
            assert isinstance(exc, core_http_module._FetchProviderError)
            assert exc.code == "TIMEOUT"
    finally:
        core_http_module.urllib.request.build_opener = original_build_opener


def test_open_once_enforces_max_bytes() -> None:
    class _Response:
        status = 200
        headers = {"content-type": "text/plain"}

        def geturl(self) -> str:
            return "https://example.com"

        def read(self, _size: int) -> bytes:
            return b"x" * 7

        def close(self) -> None:
            return None

    class _Opener:
        def open(self, _request, timeout=None):  # type: ignore[no-untyped-def]
            del timeout
            return _Response()

    original_build_opener = core_http_module.urllib.request.build_opener
    core_http_module.urllib.request.build_opener = lambda *_args, **_kwargs: _Opener()  # type: ignore[assignment]
    try:
        try:
            core_http_module._open_once(
                url="https://example.com",
                method="GET",
                headers={},
                timeout_ms=1000,
                max_bytes=5,
                read_body=True,
            )
            assert False, "expected max-bytes error"
        except Exception as exc:
            assert isinstance(exc, core_http_module._FetchProviderError)
            assert exc.code == "MAX_BYTES_EXCEEDED"
    finally:
        core_http_module.urllib.request.build_opener = original_build_opener
