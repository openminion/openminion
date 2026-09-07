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


def test_provider_returns_bounded_xml_as_text(monkeypatch) -> None:
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
            headers={"content-type": "application/atom+xml", "etag": '"v1"'},
            body=b"<feed><entry><id>1</id></entry></feed>",
        ),
    )

    payload = provider.fetch({"url": "https://example.com/feed", "method": "GET"})
    assert payload["ok"] is True
    assert payload["extracted_text"].startswith("<feed>")
    assert "UNSUPPORTED_CONTENT_TYPE" not in payload["warnings"]


def test_provider_exposes_only_safe_rate_limit_facts(monkeypatch) -> None:
    provider = CoreHttpFetchProvider()
    monkeypatch.setattr(
        "openminion.tools.fetch.providers.core_http._enforce_url_policy",
        lambda url, allow_private_hosts=False: object(),
    )
    monkeypatch.setattr(
        "openminion.tools.fetch.providers.core_http._open_once",
        lambda **kwargs: _FetchStep(
            status_code=429,
            final_url=str(kwargs["url"]),
            headers={
                "content-type": "text/plain",
                "retry-after": "120",
                "authorization": "secret",
            },
            body=b"",
        ),
    )

    payload = provider.fetch({"url": "https://example.com/feed", "method": "GET"})
    assert payload["ok"] is False
    assert payload["error"]["code"] == "RATE_LIMITED"
    details = payload["error"]["details"]
    assert details["retry_after"] == "120"
    assert "next_eligible_at" in details
    assert "authorization" not in details


def test_provider_normalizes_impractically_large_rate_limit_values(monkeypatch) -> None:
    provider = CoreHttpFetchProvider()
    monkeypatch.setattr(
        "openminion.tools.fetch.providers.core_http._enforce_url_policy",
        lambda url, allow_private_hosts=False: object(),
    )
    monkeypatch.setattr(
        "openminion.tools.fetch.providers.core_http._open_once",
        lambda **kwargs: _FetchStep(
            status_code=429,
            final_url=str(kwargs["url"]),
            headers={"retry-after": "9" * 5_000},
            body=b"",
        ),
    )

    payload = provider.fetch({"url": "https://example.com/feed", "method": "GET"})

    assert payload["error"]["code"] == "RATE_LIMITED"
    assert len(payload["error"]["details"]["retry_after"]) == 128
    assert "next_eligible_at" not in payload["error"]["details"]


def test_provider_normalizes_overflowing_rate_limit_date(monkeypatch) -> None:
    provider = CoreHttpFetchProvider()
    monkeypatch.setattr(
        "openminion.tools.fetch.providers.core_http._enforce_url_policy",
        lambda url, allow_private_hosts=False: object(),
    )
    monkeypatch.setattr(
        "openminion.tools.fetch.providers.core_http._open_once",
        lambda **kwargs: _FetchStep(
            status_code=429,
            final_url=str(kwargs["url"]),
            headers={"retry-after": "Mon, 01 Jan 999999999999999999999 00:00:00 GMT"},
            body=b"",
        ),
    )

    payload = provider.fetch({"url": "https://example.com/feed", "method": "GET"})

    assert payload["error"]["code"] == "RATE_LIMITED"
    assert "next_eligible_at" not in payload["error"]["details"]


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
