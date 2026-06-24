from __future__ import annotations

import io
import json
from types import SimpleNamespace
from urllib import error as urllib_error

import pytest

from openminion.tools.search.providers import SearchProviderError
from openminion.tools.search.providers.tinyfish.provider import (
    TinyFishSearchProvider,
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
        (402, "AUTH_FAILED"),
        (403, "AUTH_FAILED"),
        (429, "RATE_LIMITED"),
        (500, "UPSTREAM_ERROR"),
    ],
)
def test_error_code_for_status(status: int, expected: str) -> None:
    assert _error_code_for_status(status) == expected


def test_search_requires_api_key() -> None:
    provider = TinyFishSearchProvider()

    with pytest.raises(SearchProviderError) as exc_info:
        provider.search(
            "cats",
            max_results=3,
            args={},
            ctx=SimpleNamespace(env={"TINYFISH_API_KEY": ""}),
        )

    assert exc_info.value.code == "DEPENDENCY_MISSING"
    assert "API key" in str(exc_info.value)


def test_search_maps_params_and_locally_truncates_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = TinyFishSearchProvider()
    captured: dict[str, object] = {}

    def _fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["headers"] = dict(request.headers)
        return _ResponseStub(
            {
                "query": "latest OpenAI news",
                "total_results": 20,
                "page": 0,
                "results": [
                    {
                        "position": 1,
                        "site_name": "Example",
                        "title": "OpenAI 1",
                        "snippet": "first",
                        "url": "https://example.com/1",
                    },
                    {
                        "position": 2,
                        "site_name": "Example",
                        "title": "OpenAI 2",
                        "snippet": "second",
                        "url": "https://example.com/2",
                    },
                ],
            }
        )

    monkeypatch.setattr(
        "openminion.tools.search.providers.tinyfish.provider.urllib_request.urlopen",
        _fake_urlopen,
    )

    result = provider.search(
        "latest OpenAI news",
        max_results=1,
        args={
            "api_key": "arg-tinyfish-key",
            "country": "us",
            "search_lang": "en",
            "ui_lang": "fr",
            "offset": 4,
            "page": 2,
        },
        ctx=SimpleNamespace(env={}),
    )

    assert captured["timeout"] == 20.0
    assert (
        captured["url"]
        == "https://api.search.tinyfish.ai?query=latest+OpenAI+news&location=US&language=fr"
    )
    headers = captured["headers"]
    assert headers.get("X-api-key") == "arg-tinyfish-key"
    assert result["provider"] == "tinyfish"
    assert result["query"]["original"] == "latest OpenAI news"
    assert result["query"]["more_results_available"] is True
    assert len(result["results"]) == 1
    assert result["results"][0]["title"] == "OpenAI 1"
    assert result["results"][0]["site_name"] == "Example"


def test_search_normalizes_empty_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = TinyFishSearchProvider()

    monkeypatch.setattr(
        "openminion.tools.search.providers.tinyfish.provider.urllib_request.urlopen",
        lambda request, timeout: _ResponseStub(
            {"query": "OpenAI", "total_results": 0, "page": 0, "results": []}
        ),
    )

    result = provider.search(
        "OpenAI",
        max_results=3,
        args={"api_key": "arg-tinyfish-key"},
        ctx=SimpleNamespace(env={}),
    )

    assert result["results"] == []
    assert result["warnings"] == []
    assert result["query"]["more_results_available"] is False


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (400, "INVALID_REQUEST"),
        (401, "AUTH_FAILED"),
        (402, "AUTH_FAILED"),
        (403, "AUTH_FAILED"),
        (429, "RATE_LIMITED"),
        (500, "UPSTREAM_ERROR"),
    ],
)
def test_http_errors_map_to_search_provider_codes(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    expected: str,
) -> None:
    provider = TinyFishSearchProvider()

    def _raise_http_error(request, timeout):
        del request, timeout
        raise urllib_error.HTTPError(
            url="https://api.search.tinyfish.ai",
            code=status,
            msg="failure",
            hdrs=None,
            fp=io.BytesIO(b'{"error":{"code":"INVALID_API_KEY","message":"bad news"}}'),
        )

    monkeypatch.setattr(
        "openminion.tools.search.providers.tinyfish.provider.urllib_request.urlopen",
        _raise_http_error,
    )

    with pytest.raises(SearchProviderError) as exc_info:
        provider.search(
            "cats",
            max_results=3,
            args={"api_key": "arg-tinyfish-key"},
            ctx=SimpleNamespace(env={}),
        )

    assert exc_info.value.code == expected
