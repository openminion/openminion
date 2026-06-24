from __future__ import annotations

import io
import json
from types import SimpleNamespace
from urllib import error as urllib_error

import pytest

from openminion.tools.fetch.providers.tinyfish.provider import (
    TinyFishFetchProvider,
    _error_code_for_row,
    _error_code_for_status,
)


class _ResponseStub:
    def __init__(self, payload: dict[str, object], *, status: int = 200) -> None:
        self._body = json.dumps(payload).encode("utf-8")
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_ResponseStub":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        del exc_type, exc, tb
        return False


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (400, "INVALID_REQUEST"),
        (401, "AUTH_FAILED"),
        (429, "RATE_LIMITED"),
        (500, "UPSTREAM_ERROR"),
    ],
)
def test_error_code_for_status(status: int, expected: str) -> None:
    assert _error_code_for_status(status) == expected


@pytest.mark.parametrize(
    ("row_error", "expected"),
    [
        ("invalid_url", "INVALID_REQUEST"),
        ("timeout", "UPSTREAM_ERROR"),
        ("bot_blocked", "UPSTREAM_ERROR"),
    ],
)
def test_error_code_for_row(row_error: str, expected: str) -> None:
    assert _error_code_for_row(row_error) == expected


def test_fetch_requires_api_key() -> None:
    provider = TinyFishFetchProvider()

    result = provider.fetch(
        {"url": "https://example.com", "method": "GET"},
        ctx=SimpleNamespace(env={"TINYFISH_API_KEY": ""}),
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "DEPENDENCY_MISSING"


def test_fetch_rejects_head_requests() -> None:
    provider = TinyFishFetchProvider()

    result = provider.fetch(
        {"url": "https://example.com", "method": "HEAD"},
        ctx=SimpleNamespace(env={"TINYFISH_API_KEY": "tinyfish-key"}),
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_ARGUMENT"


def test_fetch_maps_request_and_normalizes_markdown_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = TinyFishFetchProvider()
    captured: dict[str, object] = {}

    def _fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _ResponseStub(
            {
                "results": [
                    {
                        "url": "https://example.com",
                        "final_url": "https://example.com/final",
                        "title": "Example Title",
                        "description": "desc",
                        "language": "en",
                        "author": "Jane",
                        "published_date": "2026-05-01",
                        "text": "# Example Title\n\nBody",
                        "format": "markdown",
                        "latency_ms": 100,
                    }
                ],
                "errors": [],
            }
        )

    monkeypatch.setattr(
        "openminion.tools.fetch.providers.tinyfish.provider.urllib_request.urlopen",
        _fake_urlopen,
    )

    result = provider.fetch(
        {
            "url": "https://example.com",
            "method": "GET",
            "timeout_ms": 9000,
            "headers": {"Authorization": "ignored"},
            "provider_options": {"tinyfish": {"format": "markdown", "links": True}},
        },
        ctx=SimpleNamespace(env={"TINYFISH_API_KEY": "tinyfish-key"}),
    )

    assert captured["url"] == "https://api.fetch.tinyfish.ai"
    assert captured["timeout"] == 9.0
    assert captured["body"] == {
        "urls": ["https://example.com"],
        "format": "markdown",
        "links": True,
        "image_links": False,
    }
    headers = captured["headers"]
    assert headers.get("X-api-key") == "tinyfish-key"
    assert result["ok"] is True
    assert result["backend"] == "tinyfish"
    assert result["final_url"] == "https://example.com/final"
    assert result["content_type"] == "text/markdown"
    assert any(
        item.startswith("UNSUPPORTED_REQUEST_FIELD:headers")
        for item in result["warnings"]
    )


def test_fetch_normalizes_json_success(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = TinyFishFetchProvider()

    monkeypatch.setattr(
        "openminion.tools.fetch.providers.tinyfish.provider.urllib_request.urlopen",
        lambda request, timeout: _ResponseStub(
            {
                "results": [
                    {
                        "url": "https://example.com",
                        "final_url": "https://example.com",
                        "title": "Example Title",
                        "language": "en",
                        "text": {"type": "document", "children": []},
                        "format": "json",
                    }
                ],
                "errors": [],
            }
        ),
    )

    result = provider.fetch(
        {
            "url": "https://example.com",
            "method": "GET",
            "provider_options": {"tinyfish": {"format": "json"}},
        },
        ctx=SimpleNamespace(env={"TINYFISH_API_KEY": "tinyfish-key"}),
    )

    assert result["ok"] is True
    assert result["content_type"] == "application/json"
    assert '"type": "document"' in result["raw_body"]


def test_fetch_maps_per_url_error(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = TinyFishFetchProvider()

    monkeypatch.setattr(
        "openminion.tools.fetch.providers.tinyfish.provider.urllib_request.urlopen",
        lambda request, timeout: _ResponseStub(
            {
                "results": [],
                "errors": [
                    {
                        "url": "https://example.com",
                        "error": "invalid_url",
                    }
                ],
            }
        ),
    )

    result = provider.fetch(
        {"url": "https://example.com", "method": "GET"},
        ctx=SimpleNamespace(env={"TINYFISH_API_KEY": "tinyfish-key"}),
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_REQUEST"


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (400, "INVALID_REQUEST"),
        (401, "AUTH_FAILED"),
        (429, "RATE_LIMITED"),
        (500, "UPSTREAM_ERROR"),
    ],
)
def test_http_errors_map_to_fetch_codes(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    expected: str,
) -> None:
    provider = TinyFishFetchProvider()

    def _raise_http_error(request, timeout):
        del request, timeout
        raise urllib_error.HTTPError(
            url="https://api.fetch.tinyfish.ai",
            code=status,
            msg="failure",
            hdrs=None,
            fp=io.BytesIO(b'{"error":{"code":"INVALID_API_KEY","message":"bad news"}}'),
        )

    monkeypatch.setattr(
        "openminion.tools.fetch.providers.tinyfish.provider.urllib_request.urlopen",
        _raise_http_error,
    )

    result = provider.fetch(
        {"url": "https://example.com", "method": "GET"},
        ctx=SimpleNamespace(env={"TINYFISH_API_KEY": "tinyfish-key"}),
    )

    assert result["ok"] is False
    assert result["error"]["code"] == expected
