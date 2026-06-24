from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from openminion.tools.search.providers import SearchProviderError
from openminion.tools.search.providers.brave.plugin import BraveSearchFacadeProvider
from openminion.tools.search.providers.brave.provider import (
    BraveSearchError,
    clamp_count,
    clamp_offset,
)


@dataclass
class _ProviderStub:
    def search(self, *, args):
        del args
        return (
            {
                "query": {"original": "cats", "more_results_available": True},
                "web": {
                    "results": [
                        {
                            "title": "Cats",
                            "url": "https://example.com/cats",
                            "description": "About cats",
                            "extra_snippets": ["s1", "s2"],
                        }
                    ]
                },
            },
            {
                "X-RateLimit-Limit": "100",
                "X-RateLimit-Remaining": "99",
                "X-RateLimit-Reset": "60",
            },
        )

    def _api_key(self, args, ctx=None):
        del args, ctx
        return "brv-test"


@dataclass
class _ProviderFailStub:
    def search(self, *, args):
        del args
        raise BraveSearchError("Missing Brave API key", code="DEPENDENCY_MISSING")

    def _api_key(self, args, ctx=None):
        del args, ctx
        return ""


@dataclass
class _ProviderContextStub:
    def search(self, *, args):
        del args
        return (
            {
                "query": {"original": "cats", "more_results_available": False},
                "web": {"results": []},
            },
            {},
        )

    def _api_key(self, args, ctx=None):
        del args
        env = getattr(ctx, "env", {}) if ctx is not None else {}
        return str(
            getattr(env, "get", lambda *_args, **_kwargs: "")("BRAVE_API_KEY", "")
        )


def test_clamp_constraints() -> None:
    assert clamp_count(100) == 20
    assert clamp_count(-1) == 1
    assert clamp_offset(100) == 9
    assert clamp_offset(-5) == 0


def test_brave_facade_normalizes_payload() -> None:
    provider = BraveSearchFacadeProvider(provider=_ProviderStub())
    result = provider.search(
        "cats",
        max_results=5,
        args={"count": 5, "offset": 0, "extra_snippets": True},
        ctx=None,
    )

    assert result["provider"] == "brave"
    assert result["query"]["original"] == "cats"
    assert result["query"]["more_results_available"] is True
    assert result["results"][0]["rank"] == 1
    assert result["results"][0]["extra_snippets"] == ["s1", "s2"]
    assert result["rate_limit"]["remaining"] == "99"


def test_brave_facade_maps_provider_error() -> None:
    provider = BraveSearchFacadeProvider(provider=_ProviderFailStub())

    with pytest.raises(SearchProviderError) as exc_info:
        provider.search("cats", max_results=5, args={}, ctx=None)

    err = exc_info.value
    assert err.code == "DEPENDENCY_MISSING"
    assert "Brave search failed" in str(err)


def test_brave_facade_healthcheck_uses_provider_api_key() -> None:
    ok_provider = BraveSearchFacadeProvider(provider=_ProviderStub())
    fail_provider = BraveSearchFacadeProvider(provider=_ProviderFailStub())

    assert ok_provider.healthcheck() is True
    assert fail_provider.healthcheck() is False


def test_brave_facade_healthcheck_accepts_runtime_context() -> None:
    provider = BraveSearchFacadeProvider(provider=_ProviderContextStub())

    assert (
        provider.healthcheck(SimpleNamespace(env={"BRAVE_API_KEY": "ctx-brave-key"}))
        is True
    )
    assert provider.healthcheck(SimpleNamespace(env={})) is False
