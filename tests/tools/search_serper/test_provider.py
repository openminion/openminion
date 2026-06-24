from __future__ import annotations

import io
import json
from types import SimpleNamespace
from urllib import error as urllib_error

import pytest

from openminion.tools.search.providers import SearchProviderError
from openminion.tools.search.providers.serper.provider import (
    SerperSearchProvider,
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
    provider = SerperSearchProvider()

    with pytest.raises(SearchProviderError) as exc_info:
        provider.search(
            "cats",
            max_results=3,
            args={},
            ctx=SimpleNamespace(env={"SERPER_API_KEY": ""}),
        )

    assert exc_info.value.code == "DEPENDENCY_MISSING"
    assert "API key" in str(exc_info.value)


def test_search_maps_params_and_normalizes_empty_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = SerperSearchProvider()
    captured: dict[str, object] = {}

    def _fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _ResponseStub({"organic": []})

    monkeypatch.setattr(
        "openminion.tools.search.providers.serper.provider.urllib_request.urlopen",
        _fake_urlopen,
    )

    result = provider.search(
        "latest OpenAI news",
        max_results=3,
        args={
            "api_key": "arg-serper-key",
            "country": "US",
            "search_lang": "en",
            "ui_lang": "fr",
            "location": "San Francisco",
            "page": 2,
        },
        ctx=SimpleNamespace(env={}),
    )

    headers = captured["headers"]
    assert captured["url"] == "https://google.serper.dev/search"
    assert captured["timeout"] == 20.0
    assert captured["body"] == {
        "q": "latest OpenAI news",
        "num": 3,
        "gl": "us",
        "hl": "fr",
    }
    assert headers.get("X-api-key") == "arg-serper-key"
    assert result["provider"] == "serper"
    assert result["query"]["original"] == "latest OpenAI news"
    assert result["query"]["more_results_available"] is False
    assert result["results"] == []
    assert result["warnings"] == []


def test_search_normalizes_organic_results_and_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = SerperSearchProvider()

    monkeypatch.setattr(
        "openminion.tools.search.providers.serper.provider.urllib_request.urlopen",
        lambda request, timeout: _ResponseStub(
            {
                "warning": "partial upstream warning",
                "organic": [
                    {
                        "position": 1,
                        "title": "OpenAI 1",
                        "link": "https://example.com/1",
                        "snippet": "first",
                    },
                    {
                        "title": "OpenAI 2",
                        "link": "https://example.com/2",
                        "snippet": "second",
                    },
                ],
            }
        ),
    )

    result = provider.search(
        "OpenAI",
        max_results=1,
        args={"api_key": "arg-serper-key"},
        ctx=SimpleNamespace(env={}),
    )

    assert len(result["results"]) == 1
    assert result["results"][0]["rank"] == 1
    assert result["results"][0]["title"] == "OpenAI 1"
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
    provider = SerperSearchProvider()

    def _raise_http_error(request, timeout):
        del request, timeout
        raise urllib_error.HTTPError(
            url="https://google.serper.dev/search",
            code=status,
            msg="failure",
            hdrs=None,
            fp=io.BytesIO(b'{"message":"bad news"}'),
        )

    monkeypatch.setattr(
        "openminion.tools.search.providers.serper.provider.urllib_request.urlopen",
        _raise_http_error,
    )

    with pytest.raises(SearchProviderError) as exc_info:
        provider.search(
            "cats",
            max_results=3,
            args={"api_key": "arg-serper-key"},
            ctx=SimpleNamespace(env={}),
        )

    assert exc_info.value.code == expected
