from __future__ import annotations

import io
import json
from types import SimpleNamespace
from urllib import error as urllib_error

import pytest

from openminion.tools.search.providers import SearchProviderError
from openminion.tools.search.providers.firecrawl.provider import (
    FirecrawlSearchProvider,
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
        (403, "AUTH_FAILED"),
        (429, "RATE_LIMITED"),
        (500, "UPSTREAM_ERROR"),
    ],
)
def test_error_code_for_status(status: int, expected: str) -> None:
    assert _error_code_for_status(status) == expected


def test_search_requires_api_key() -> None:
    provider = FirecrawlSearchProvider()

    with pytest.raises(SearchProviderError) as exc_info:
        provider.search(
            "cats",
            max_results=3,
            args={},
            ctx=SimpleNamespace(env={"FIRECRAWL_API_KEY": ""}),
        )

    assert exc_info.value.code == "DEPENDENCY_MISSING"
    assert "API key" in str(exc_info.value)


def test_search_maps_params_and_normalizes_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = FirecrawlSearchProvider()
    captured: dict[str, object] = {}

    def _fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _ResponseStub(
            {
                "warning": "partial upstream warning",
                "data": {
                    "web": [
                        {
                            "title": "OpenAI 1",
                            "url": "https://example.com/1",
                            "description": "first",
                        },
                        {
                            "title": "OpenAI 2",
                            "url": "https://example.com/2",
                            "snippet": "second",
                        },
                        {
                            "title": "OpenAI 3",
                            "url": "https://example.com/3",
                            "markdown": "third",
                        },
                    ]
                },
            }
        )

    monkeypatch.setattr(
        "openminion.tools.search.providers.firecrawl.provider.urllib_request.urlopen",
        _fake_urlopen,
    )

    result = provider.search(
        "latest OpenAI news",
        max_results=2,
        args={
            "api_key": "arg-firecrawl-key",
            "country": "us",
            "location": "San Francisco",
            "categories": ["news"],
            "scrapeOptions": {"formats": ["markdown"]},
        },
        ctx=SimpleNamespace(env={}),
    )

    assert captured["url"] == "https://api.firecrawl.dev/v2/search"
    assert captured["timeout"] == 20.0
    assert captured["headers"]["Authorization"] == "Bearer arg-firecrawl-key"
    assert captured["body"] == {
        "query": "latest OpenAI news",
        "limit": 2,
        "sources": ["web"],
        "country": "us",
    }
    assert result["provider"] == "firecrawl"
    assert result["query"]["original"] == "latest OpenAI news"
    assert result["query"]["more_results_available"] is False
    assert len(result["results"]) == 2
    assert result["results"][0]["description"] == "first"
    assert result["results"][1]["description"] == "second"
    assert "partial upstream warning" in result["warnings"]


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (400, "INVALID_REQUEST"),
        (401, "AUTH_FAILED"),
        (429, "RATE_LIMITED"),
        (500, "UPSTREAM_ERROR"),
    ],
)
def test_http_errors_map_to_search_provider_codes(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    expected: str,
) -> None:
    provider = FirecrawlSearchProvider()

    def _raise_http_error(request, timeout):
        del request, timeout
        raise urllib_error.HTTPError(
            url="https://api.firecrawl.dev/v2/search",
            code=status,
            msg="failure",
            hdrs=None,
            fp=io.BytesIO(b'{"error":"bad news"}'),
        )

    monkeypatch.setattr(
        "openminion.tools.search.providers.firecrawl.provider.urllib_request.urlopen",
        _raise_http_error,
    )

    with pytest.raises(SearchProviderError) as exc_info:
        provider.search(
            "cats",
            max_results=3,
            args={"api_key": "arg-firecrawl-key"},
            ctx=SimpleNamespace(env={}),
        )

    assert exc_info.value.code == expected
