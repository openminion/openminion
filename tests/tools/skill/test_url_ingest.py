from __future__ import annotations

import io
from typing import Any
from urllib import error as urllib_error

import pytest

from openminion.tools.skill import url_ingest


_MARKDOWN_BODY = b"# Sample Skill\n\n## Procedure\nRun the example command.\n"


class _FakeResponse:
    def __init__(
        self,
        *,
        body: bytes = _MARKDOWN_BODY,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._body = body
        self.status = status
        self.headers = _Headers(headers or {"Content-Type": "text/markdown"})

    def read(self, n: int = -1) -> bytes:
        return self._body[:n] if n >= 0 else self._body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None


class _Headers:
    def __init__(self, data: dict[str, str]) -> None:
        self._data = {key.lower(): value for key, value in data.items()}

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key.lower(), default)


# redirect-aware re-validation tests.


def _install_opener_stub(monkeypatch: pytest.MonkeyPatch, handler) -> None:

    class _StubOpener:
        def open(self, req, timeout: float):  # noqa: ARG002
            return handler(req)

    monkeypatch.setattr(
        url_ingest.urllib_request, "build_opener", lambda *_h: _StubOpener()
    )


def _stub_dns_stable(monkeypatch: pytest.MonkeyPatch, ips: set[str]) -> None:
    monkeypatch.setattr(url_ingest, "_resolve_host_ips", lambda _host: set(ips))


def _stub_no_blocked_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(url_ingest, "is_blocked_skill_host", lambda _host: False)


def test_fetch_succeeds_when_no_redirects(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_no_blocked_hosts(monkeypatch)
    _stub_dns_stable(monkeypatch, {"203.0.113.10"})

    def handler(_req: Any) -> _FakeResponse:
        return _FakeResponse()

    _install_opener_stub(monkeypatch, handler)
    result = url_ingest.fetch_skill_markdown_from_url("https://example.com/skill.md")
    assert result["ok"] is True
    assert "Sample Skill" in result["content"]
    assert result["final_url"] == "https://example.com/skill.md"


def test_fetch_follows_one_redirect_with_revalidation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_no_blocked_hosts(monkeypatch)
    _stub_dns_stable(monkeypatch, {"203.0.113.10"})

    call_log: list[str] = []

    def handler(req: Any) -> _FakeResponse:
        url = req.full_url if hasattr(req, "full_url") else str(req)
        call_log.append(url)
        if url.endswith("/redirect.md"):
            raise urllib_error.HTTPError(
                url,
                302,
                "Found",
                _Headers({"Location": "https://example.com/final.md"}),
                io.BytesIO(b""),
            )
        return _FakeResponse()

    _install_opener_stub(monkeypatch, handler)
    result = url_ingest.fetch_skill_markdown_from_url("https://example.com/redirect.md")
    assert result["ok"] is True
    assert result["final_url"] == "https://example.com/final.md"
    assert call_log == [
        "https://example.com/redirect.md",
        "https://example.com/final.md",
    ]


def test_fetch_blocks_redirect_to_private_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # First host is public, redirect target is blocked.
    def is_blocked(host: str) -> bool:
        return host in {"internal.corp"}

    monkeypatch.setattr(url_ingest, "is_blocked_skill_host", is_blocked)
    _stub_dns_stable(monkeypatch, {"203.0.113.10"})

    def handler(req: Any) -> _FakeResponse:
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if url.endswith("/redirect.md"):
            raise urllib_error.HTTPError(
                url,
                302,
                "Found",
                _Headers({"Location": "https://internal.corp/secret.md"}),
                io.BytesIO(b""),
            )
        raise AssertionError(f"unexpected fetch to {url}")

    _install_opener_stub(monkeypatch, handler)
    result = url_ingest.fetch_skill_markdown_from_url("https://example.com/redirect.md")
    assert result["ok"] is False
    # The redirect target was blocked at the next hop's host check.
    assert result["error_code"] == "BLOCKED_HOST", result


def test_fetch_caps_redirect_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_no_blocked_hosts(monkeypatch)
    _stub_dns_stable(monkeypatch, {"203.0.113.10"})

    def handler(req: Any) -> _FakeResponse:
        # Every URL bounces to /next.md — infinite loop of distinct hops.
        url = req.full_url if hasattr(req, "full_url") else str(req)
        next_url = url.rsplit("/", 1)[0] + "/next.md"
        if url.endswith("/next.md") and url == next_url:
            # Make every hop a redirect by varying the path
            next_url = url.replace("/next.md", "/next2.md")
        raise urllib_error.HTTPError(
            url,
            302,
            "Found",
            _Headers({"Location": next_url}),
            io.BytesIO(b""),
        )

    _install_opener_stub(monkeypatch, handler)
    result = url_ingest.fetch_skill_markdown_from_url("https://example.com/skill.md")
    assert result["ok"] is False
    assert result["error_code"] == "URL_INGEST_REDIRECT_LIMIT", result


def test_fetch_uses_max_redirects_constant() -> None:
    # constant is the canonical cap. Pin it so changes are visible
    # in tests.
    from openminion.tools.skill.constants import SKILL_URL_MAX_REDIRECTS

    assert SKILL_URL_MAX_REDIRECTS == 3


# DNS rebinding guard.


def test_fetch_succeeds_when_dns_resolution_is_stable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_no_blocked_hosts(monkeypatch)
    _stub_dns_stable(monkeypatch, {"203.0.113.10"})

    def handler(_req: Any) -> _FakeResponse:
        return _FakeResponse()

    _install_opener_stub(monkeypatch, handler)
    result = url_ingest.fetch_skill_markdown_from_url("https://example.com/skill.md")
    assert result["ok"] is True


def test_fetch_fails_when_dns_resolution_drifts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_no_blocked_hosts(monkeypatch)

    call_count = {"n": 0}

    def shifting_resolver(_host: str) -> set[str]:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return {"203.0.113.10"}  # initial check
        return {"10.0.0.5"}  # different IP at fetch time

    monkeypatch.setattr(url_ingest, "_resolve_host_ips", shifting_resolver)

    def handler(_req: Any) -> _FakeResponse:
        raise AssertionError(
            "fetch should not be issued when DNS rebinding guard fires"
        )

    _install_opener_stub(monkeypatch, handler)
    result = url_ingest.fetch_skill_markdown_from_url("https://example.com/skill.md")
    assert result["ok"] is False
    assert result["error_code"] == "URL_INGEST_DNS_REBINDING_GUARD", result


def test_fetch_rejects_non_markdown_extension() -> None:
    # paths must end with .md.
    result = url_ingest.fetch_skill_markdown_from_url(
        "https://example.com/not-a-skill.txt"
    )
    assert result["ok"] is False
    assert result["error_code"] == "INVALID_FILE_TYPE"


def test_fetch_rejects_invalid_scheme() -> None:
    result = url_ingest.fetch_skill_markdown_from_url("file:///etc/passwd.md")
    assert result["ok"] is False
    assert result["error_code"] == "INVALID_SCHEME"
