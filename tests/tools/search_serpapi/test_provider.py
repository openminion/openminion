from __future__ import annotations

import io
import json
from types import SimpleNamespace
from urllib import error as urllib_error
from urllib import parse as urllib_parse

import pytest

from openminion.tools.search.providers import SearchProviderError
from openminion.tools.search.providers.serpapi.provider import (
    SerpApiSearchProvider,
    _error_code_for_status,
    _normalize_safe_search,
)


class _ResponseStub:
    def __init__(self, payload: dict[str, object], *, status: int = 200) -> None:
        self._body = json.dumps(payload).encode("utf-8")
        self.status = status
        self.headers: dict[str, str] = {}

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_ResponseStub":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        del exc_type, exc, tb
        return False


def test_normalize_safe_search_variants() -> None:
    assert _normalize_safe_search("off") == "off"
    assert _normalize_safe_search("0") == "off"
    assert _normalize_safe_search("strict") == "active"
    assert _normalize_safe_search("yes") == "active"
    assert _normalize_safe_search("unexpected") == ""


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
    provider = SerpApiSearchProvider()

    with pytest.raises(SearchProviderError) as exc_info:
        provider.search(
            "cats",
            max_results=3,
            args={},
            ctx=SimpleNamespace(env={"SERPAPI_API_KEY": ""}),
        )

    assert exc_info.value.code == "DEPENDENCY_MISSING"
    assert "API key" in str(exc_info.value)


def test_search_maps_params_and_slices_results_without_num(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = SerpApiSearchProvider()
    captured: dict[str, str] = {}

    def _fake_urlopen(request, timeout):
        del timeout
        parsed = urllib_parse.urlparse(request.full_url)
        query = urllib_parse.parse_qs(parsed.query)
        captured.update({key: values[0] for key, values in query.items()})
        return _ResponseStub(
            {
                "search_metadata": {"status": "Success"},
                "answer_box": {"answer": "Cats are animals."},
                "serpapi_pagination": {"next": "https://example.com/next"},
                "organic_results": [
                    {
                        "position": 1,
                        "title": "Cats 1",
                        "link": "https://example.com/cats-1",
                        "snippet": "cats 1",
                    },
                    {
                        "position": 2,
                        "title": "Cats 2",
                        "link": "https://example.com/cats-2",
                        "snippet": "cats 2",
                    },
                    {
                        "position": 3,
                        "title": "Cats 3",
                        "link": "https://example.com/cats-3",
                        "snippet": "cats 3",
                    },
                ],
            }
        )

    monkeypatch.setattr(
        "openminion.tools.search.providers.serpapi.provider.urllib_request.urlopen",
        _fake_urlopen,
    )

    result = provider.search(
        "cats",
        max_results=2,
        args={
            "api_key": "arg-serp-key",
            "country": "us",
            "search_lang": "en",
            "ui_lang": "fr",
            "safesearch": "strict",
        },
        ctx=SimpleNamespace(env={}),
    )

    assert captured["engine"] == "google"
    assert captured["output"] == "json"
    assert captured["api_key"] == "arg-serp-key"
    assert captured["q"] == "cats"
    assert captured["gl"] == "us"
    assert captured["hl"] == "en"
    assert captured["safe"] == "active"
    assert "num" not in captured
    assert result["provider"] == "serpapi"
    assert result["query"]["original"] == "cats"
    assert result["query"]["more_results_available"] is True
    assert len(result["results"]) == 2
    assert result["answer"] == "Cats are animals."


def test_success_with_top_level_error_becomes_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = SerpApiSearchProvider()

    monkeypatch.setattr(
        "openminion.tools.search.providers.serpapi.provider.urllib_request.urlopen",
        lambda request, timeout: _ResponseStub(
            {
                "search_metadata": {"status": "Success"},
                "error": "partial upstream warning",
                "search_information": {"organic_results_state": "Fully empty"},
                "organic_results": [],
            }
        ),
    )

    result = provider.search(
        "cats",
        max_results=5,
        args={"api_key": "arg-serp-key"},
        ctx=SimpleNamespace(env={}),
    )

    assert "partial upstream warning" in result["warnings"]
    assert "organic_results_state=Fully empty" in result["warnings"]


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
    provider = SerpApiSearchProvider()

    def _raise_http_error(request, timeout):
        del request, timeout
        raise urllib_error.HTTPError(
            url="https://serpapi.example.test/search",
            code=status,
            msg="failure",
            hdrs=None,
            fp=io.BytesIO(b'{"error":"bad news"}'),
        )

    monkeypatch.setattr(
        "openminion.tools.search.providers.serpapi.provider.urllib_request.urlopen",
        _raise_http_error,
    )

    with pytest.raises(SearchProviderError) as exc_info:
        provider.search(
            "cats",
            max_results=3,
            args={"api_key": "arg-serp-key"},
            ctx=SimpleNamespace(env={}),
        )

    assert exc_info.value.code == expected
