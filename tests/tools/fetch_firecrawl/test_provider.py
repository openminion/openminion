from __future__ import annotations

import io
import json
from types import SimpleNamespace
from urllib import error as urllib_error

import pytest

import importlib

provider_module = importlib.import_module(
    "openminion.tools.fetch.providers.firecrawl.provider"
)
FirecrawlFetchProvider = provider_module.FirecrawlFetchProvider
_error_code_for_status = provider_module._error_code_for_status


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
        (402, "UPSTREAM_ERROR"),
        (422, "INVALID_REQUEST"),
        (429, "RATE_LIMITED"),
        (500, "UPSTREAM_ERROR"),
    ],
)
def test_error_code_for_status_maps_documented_responses(
    status: int, expected: str
) -> None:
    assert _error_code_for_status(status) == expected


def test_fetch_requires_api_key() -> None:
    provider = FirecrawlFetchProvider()

    result = provider.fetch(
        {"url": "https://example.com", "method": "GET"},
        ctx=SimpleNamespace(env={"FIRECRAWL_API_KEY": ""}),
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "DEPENDENCY_MISSING"
    assert result["backend"] == "firecrawl"


def test_fetch_rejects_head_requests_with_invalid_argument() -> None:
    provider = FirecrawlFetchProvider()

    result = provider.fetch(
        {"url": "https://example.com", "method": "HEAD"},
        ctx=SimpleNamespace(env={"FIRECRAWL_API_KEY": "fc-key"}),
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_ARGUMENT"
    assert result["backend"] == "firecrawl"


def test_fetch_rejects_empty_url() -> None:
    provider = FirecrawlFetchProvider()

    result = provider.fetch(
        {"url": "   ", "method": "GET"},
        ctx=SimpleNamespace(env={"FIRECRAWL_API_KEY": "fc-key"}),
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_REQUEST"


def test_fetch_rejects_unknown_provider_option_key() -> None:
    provider = FirecrawlFetchProvider()

    result = provider.fetch(
        {
            "url": "https://example.com",
            "method": "GET",
            "provider_options": {"firecrawl": {"unsupported_key": True}},
        },
        ctx=SimpleNamespace(env={"FIRECRAWL_API_KEY": "fc-key"}),
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_ARGUMENT"


def test_fetch_maps_request_body_and_normalizes_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = FirecrawlFetchProvider()
    captured: dict[str, object] = {}

    def _fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _ResponseStub(
            {
                "success": True,
                "data": {
                    "markdown": "# Hello\n\nWorld",
                    "html": "<html><body>hi</body></html>",
                    "rawHtml": "<html><body>raw</body></html>",
                    "links": ["https://example.com/a"],
                    "warning": "minor",
                    "metadata": {
                        "statusCode": 200,
                        "contentType": "text/html; charset=utf-8",
                        "sourceURL": "https://example.com/final",
                        "title": "Example",
                        "language": "en",
                    },
                },
            }
        )

    monkeypatch.setattr(
        provider_module.urllib_request,
        "urlopen",
        _fake_urlopen,
    )

    result = provider.fetch(
        {
            "url": "https://example.com",
            "method": "GET",
            "timeout_ms": 9000,
            "headers": {"X-Custom": "value"},
            "provider_options": {
                "firecrawl": {
                    "formats": ["markdown", "html", "links"],
                    "only_main_content": True,
                    "include_tags": ["article"],
                    "exclude_tags": ["nav"],
                    "wait_for_ms": 500,
                    "mobile": True,
                    "max_age_ms": 60000,
                    "block_ads": True,
                }
            },
        },
        ctx=SimpleNamespace(env={"FIRECRAWL_API_KEY": "fc-key"}),
    )

    # URL targets v2 scrape endpoint
    assert captured["url"].endswith("/v2/scrape")
    assert captured["timeout"] == 9.0
    # Authorization header carries bearer token
    headers = captured["headers"]
    assert headers.get("Authorization") == "Bearer fc-key"
    # Body uses Firecrawl camelCase keys + supported formats subset
    body = captured["body"]
    assert body["url"] == "https://example.com"
    assert body["formats"] == ["markdown", "html", "links"]
    assert body["onlyMainContent"] is True
    assert body["includeTags"] == ["article"]
    assert body["excludeTags"] == ["nav"]
    assert body["waitFor"] == 500
    assert body["mobile"] is True
    assert body["maxAge"] == 60000
    assert body["blockAds"] is True
    assert body["timeout"] == 9000
    assert body["headers"] == {"X-Custom": "value"}

    # Normalized response uses spec §4.8 rules
    assert result["ok"] is True
    assert result["backend"] == "firecrawl"
    assert result["final_url"] == "https://example.com/final"
    assert result["status_code"] == 200
    assert result["title"] == "Example"
    assert result["language"] == "en"
    assert result["raw_body"] == "<html><body>raw</body></html>"  # rawHtml preferred
    assert result["extracted_text"].startswith("# Hello")
    assert "minor" in result["warnings"]
    assert result["meta"]["links"] == ["https://example.com/a"]


def test_fetch_falls_back_to_html_when_raw_html_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = FirecrawlFetchProvider()

    monkeypatch.setattr(
        provider_module.urllib_request,
        "urlopen",
        lambda request, timeout: _ResponseStub(
            {
                "success": True,
                "data": {
                    "html": "<html>body</html>",
                    "metadata": {"statusCode": 200, "contentType": "text/html"},
                },
            }
        ),
    )

    result = provider.fetch(
        {
            "url": "https://example.com",
            "method": "GET",
            "provider_options": {"firecrawl": {"formats": ["html"]}},
        },
        ctx=SimpleNamespace(env={"FIRECRAWL_API_KEY": "fc-key"}),
    )

    assert result["ok"] is True
    assert result["raw_body"] == "<html>body</html>"


def test_fetch_uses_markdown_as_raw_body_when_html_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = FirecrawlFetchProvider()

    monkeypatch.setattr(
        provider_module.urllib_request,
        "urlopen",
        lambda request, timeout: _ResponseStub(
            {
                "success": True,
                "data": {
                    "markdown": "# Only markdown",
                    "metadata": {"statusCode": 200},
                },
            }
        ),
    )

    result = provider.fetch(
        {
            "url": "https://example.com",
            "method": "GET",
            "provider_options": {"firecrawl": {"formats": ["markdown"]}},
        },
        ctx=SimpleNamespace(env={"FIRECRAWL_API_KEY": "fc-key"}),
    )

    assert result["ok"] is True
    assert result["raw_body"] == "# Only markdown"
    assert result["content_type"] == "text/markdown"


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (400, "INVALID_REQUEST"),
        (401, "AUTH_FAILED"),
        (402, "UPSTREAM_ERROR"),
        (429, "RATE_LIMITED"),
        (500, "UPSTREAM_ERROR"),
    ],
)
def test_http_errors_map_to_fetch_codes(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    expected: str,
) -> None:
    provider = FirecrawlFetchProvider()

    def _raise_http_error(request, timeout):
        del request, timeout
        raise urllib_error.HTTPError(
            url="https://api.firecrawl.dev/v2/scrape",
            code=status,
            msg="failure",
            hdrs=None,
            fp=io.BytesIO(b'{"success":false,"error":"bad"}'),
        )

    monkeypatch.setattr(
        provider_module.urllib_request,
        "urlopen",
        _raise_http_error,
    )

    result = provider.fetch(
        {"url": "https://example.com", "method": "GET"},
        ctx=SimpleNamespace(env={"FIRECRAWL_API_KEY": "fc-key"}),
    )

    assert result["ok"] is False
    assert result["error"]["code"] == expected
    assert result["backend"] == "firecrawl"


def test_url_error_maps_to_upstream_error(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = FirecrawlFetchProvider()

    def _raise_url_error(request, timeout):
        del request, timeout
        raise urllib_error.URLError("connection refused")

    monkeypatch.setattr(
        provider_module.urllib_request,
        "urlopen",
        _raise_url_error,
    )

    result = provider.fetch(
        {"url": "https://example.com", "method": "GET"},
        ctx=SimpleNamespace(env={"FIRECRAWL_API_KEY": "fc-key"}),
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "UPSTREAM_ERROR"


def test_malformed_json_maps_to_upstream_error(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = FirecrawlFetchProvider()

    class _BadJSONResponse:
        def read(self) -> bytes:
            return b"not valid json"

        def __enter__(self) -> "_BadJSONResponse":
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            del exc_type, exc, tb
            return False

    monkeypatch.setattr(
        provider_module.urllib_request,
        "urlopen",
        lambda request, timeout: _BadJSONResponse(),
    )

    result = provider.fetch(
        {"url": "https://example.com", "method": "GET"},
        ctx=SimpleNamespace(env={"FIRECRAWL_API_KEY": "fc-key"}),
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "UPSTREAM_ERROR"


def test_missing_data_block_maps_to_upstream_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = FirecrawlFetchProvider()

    monkeypatch.setattr(
        provider_module.urllib_request,
        "urlopen",
        lambda request, timeout: _ResponseStub({"success": True}),
    )

    result = provider.fetch(
        {"url": "https://example.com", "method": "GET"},
        ctx=SimpleNamespace(env={"FIRECRAWL_API_KEY": "fc-key"}),
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "UPSTREAM_ERROR"


def test_default_formats_when_options_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = FirecrawlFetchProvider()
    captured: dict[str, object] = {}

    def _fake_urlopen(request, timeout):
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _ResponseStub(
            {
                "success": True,
                "data": {
                    "markdown": "hi",
                    "metadata": {"statusCode": 200},
                },
            }
        )

    monkeypatch.setattr(
        provider_module.urllib_request,
        "urlopen",
        _fake_urlopen,
    )

    result = provider.fetch(
        {"url": "https://example.com", "method": "GET"},
        ctx=SimpleNamespace(env={"FIRECRAWL_API_KEY": "fc-key"}),
    )

    assert result["ok"] is True
    assert captured["body"]["formats"] == ["markdown"]
