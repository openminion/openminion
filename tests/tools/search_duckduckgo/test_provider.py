from __future__ import annotations

import io
from urllib import error as urllib_error
from urllib import parse as urllib_parse

import pytest

from openminion.tools.search.providers import SearchProviderError
from openminion.tools.search.providers.duckduckgo.provider import (
    DuckDuckGoSearchProvider,
    _error_code_for_status,
    _result_url,
)


class _ResponseStub:
    def __init__(self, body: str) -> None:
        self._body = body.encode("utf-8")

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
        (422, "INVALID_REQUEST"),
        (429, "RATE_LIMITED"),
        (500, "UPSTREAM_ERROR"),
    ],
)
def test_error_code_for_status(status: int, expected: str) -> None:
    assert _error_code_for_status(status) == expected


def test_result_url_decodes_duckduckgo_redirect() -> None:
    href = "/l/?uddg=https%3A%2F%2Fexample.com%2Farticle"

    assert _result_url(href) == "https://example.com/article"


def test_search_posts_query_and_normalizes_html_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = DuckDuckGoSearchProvider()
    captured: dict[str, object] = {}

    def _fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["headers"] = dict(request.headers)
        captured["body"] = urllib_parse.parse_qs(request.data.decode("utf-8"))
        return _ResponseStub(
            """
            <div class="result">
              <a class="result__a" href="https://example.com/one">
                Example <b>One</b>
              </a>
              <a class="result__snippet">First &amp; best result.</a>
            </div>
            <div class="result">
              <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Ftwo">
                Example Two
              </a>
              <div class="result__snippet">Second result.</div>
            </div>
            """
        )

    monkeypatch.setattr(
        "openminion.tools.search.providers.duckduckgo.provider.urllib_request.urlopen",
        _fake_urlopen,
    )

    result = provider.search("OpenMinion", max_results=1, args={}, ctx=None)

    assert captured["url"] == "https://html.duckduckgo.com/html/"
    assert captured["timeout"] == 20.0
    assert captured["body"] == {"q": ["OpenMinion"]}
    assert captured["headers"]["User-agent"] == "OpenMinionSearch/1.0"
    assert result == {
        "provider": "duckduckgo",
        "query": {"original": "OpenMinion", "more_results_available": True},
        "results": [
            {
                "rank": 1,
                "title": "Example One",
                "url": "https://example.com/one",
                "description": "First & best result.",
            }
        ],
        "warnings": [],
    }


def test_search_rejects_empty_query() -> None:
    provider = DuckDuckGoSearchProvider()

    with pytest.raises(SearchProviderError) as exc_info:
        provider.search(" ", max_results=3, args={}, ctx=None)

    assert exc_info.value.code == "INVALID_REQUEST"


def test_http_and_network_errors_map_to_provider_codes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = DuckDuckGoSearchProvider()

    def _raise_http_error(request, timeout):
        del request, timeout
        raise urllib_error.HTTPError(
            url="https://html.duckduckgo.com/html/",
            code=429,
            msg="limited",
            hdrs=None,
            fp=io.BytesIO(b"limited"),
        )

    monkeypatch.setattr(
        "openminion.tools.search.providers.duckduckgo.provider.urllib_request.urlopen",
        _raise_http_error,
    )
    with pytest.raises(SearchProviderError) as http_exc:
        provider.search("cats", max_results=3, args={}, ctx=None)
    assert http_exc.value.code == "RATE_LIMITED"

    def _raise_url_error(request, timeout):
        del request, timeout
        raise urllib_error.URLError("offline")

    monkeypatch.setattr(
        "openminion.tools.search.providers.duckduckgo.provider.urllib_request.urlopen",
        _raise_url_error,
    )
    with pytest.raises(SearchProviderError) as network_exc:
        provider.search("cats", max_results=3, args={}, ctx=None)
    assert network_exc.value.code == "UPSTREAM_ERROR"
