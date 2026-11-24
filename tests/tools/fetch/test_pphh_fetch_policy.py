from __future__ import annotations

import socket

import pytest

from openminion.tools.fetch.policy import FetchPolicyError, enforce_url_policy


def test_enforce_url_policy_fails_closed_on_dns_error(monkeypatch) -> None:
    def _raise_gaierror(*args, **kwargs):
        del args, kwargs
        raise socket.gaierror("synthetic resolution failure")

    monkeypatch.setattr(socket, "getaddrinfo", _raise_gaierror)

    with pytest.raises(FetchPolicyError) as excinfo:
        enforce_url_policy(
            "https://example.invalid/resource", allow_private_hosts=False
        )

    exc = excinfo.value
    assert exc.code == "SSRF_BLOCKED"
    assert exc.details["reason_code"] == "ssrf_resolution_failed"


def test_enforce_url_policy_allow_private_hosts_skips_resolution(monkeypatch) -> None:
    called = False

    def _record_call(*args, **kwargs):
        nonlocal called
        del args, kwargs
        called = True
        raise AssertionError("resolution should not run")

    monkeypatch.setattr(socket, "getaddrinfo", _record_call)

    parsed = enforce_url_policy(
        "http://localhost:8080/status",
        allow_private_hosts=True,
    )

    assert parsed.hostname == "localhost"
    assert called is False


def test_enforce_url_policy_blocks_private_resolution(monkeypatch) -> None:
    def _private_resolution(*args, **kwargs):
        del args, kwargs
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", _private_resolution)

    with pytest.raises(FetchPolicyError) as excinfo:
        enforce_url_policy("https://example.com", allow_private_hosts=False)

    assert excinfo.value.code == "SSRF_BLOCKED"
    assert excinfo.value.details["resolved_ip"] == "127.0.0.1"


@pytest.mark.parametrize("allow_private_hosts", [False, True])
@pytest.mark.parametrize("url", ["http://[::1/resource", "https://[2001:db8::1/path"])
def test_enforce_url_policy_rejects_malformed_ipv6_url_as_invalid_url(
    allow_private_hosts: bool, url: str
) -> None:
    with pytest.raises(FetchPolicyError) as excinfo:
        enforce_url_policy(url, allow_private_hosts=allow_private_hosts)

    assert excinfo.value.code == "INVALID_URL"


@pytest.mark.parametrize("allow_private_hosts", [False, True])
@pytest.mark.parametrize(
    "url",
    [
        "https://example.com:abc/path",
        "https://example.com:99999/path",
        "https://example.com:0/path",
        "http://127.0.0.1:abc/path",
        "http://127.0.0.1:0/path",
    ],
)
def test_enforce_url_policy_rejects_invalid_and_zero_ports_before_resolution(
    monkeypatch: pytest.MonkeyPatch, allow_private_hosts: bool, url: str
) -> None:
    def _unexpected_resolution(*args, **kwargs):
        del args, kwargs
        raise AssertionError("DNS resolution must not run for invalid URL ports")

    monkeypatch.setattr(socket, "getaddrinfo", _unexpected_resolution)

    with pytest.raises(FetchPolicyError) as excinfo:
        enforce_url_policy(url, allow_private_hosts=allow_private_hosts)

    assert excinfo.value.code == "INVALID_URL"


@pytest.mark.parametrize("allow_private_hosts", [False, True])
@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/has space",
        "https://example.com/line\nbreak",
        "https://example.com/del\x7fchar",
    ],
)
def test_enforce_url_policy_rejects_embedded_whitespace_and_control_chars(
    monkeypatch: pytest.MonkeyPatch, allow_private_hosts: bool, url: str
) -> None:
    def _unexpected_resolution(*args, **kwargs):
        del args, kwargs
        raise AssertionError("DNS resolution must not run for malformed URLs")

    monkeypatch.setattr(socket, "getaddrinfo", _unexpected_resolution)

    with pytest.raises(FetchPolicyError) as excinfo:
        enforce_url_policy(url, allow_private_hosts=allow_private_hosts)

    assert excinfo.value.code == "INVALID_URL"


def test_enforce_url_policy_preserves_outer_whitespace_percent_encoding_and_ports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_ports: list[int] = []

    def _public_resolution(_host, port, **_kwargs):
        observed_ports.append(int(port))
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", int(port)))
        ]

    monkeypatch.setattr(socket, "getaddrinfo", _public_resolution)

    parsed_explicit = enforce_url_policy(
        "  https://example.com:8443/a%20b?q=1  ",
        allow_private_hosts=False,
    )
    parsed_empty_colon = enforce_url_policy(
        "http://example.com:/status",
        allow_private_hosts=False,
    )
    parsed_ipv6_private = enforce_url_policy(
        "http://[::1]:8080/health",
        allow_private_hosts=True,
    )

    assert parsed_explicit.port == 8443
    assert parsed_empty_colon.port is None
    assert parsed_ipv6_private.hostname == "::1"
    assert parsed_ipv6_private.port == 8080
    assert observed_ports == [8443, 80]


def test_core_http_and_fetch_tool_return_invalid_url_for_initial_and_redirect_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openminion.tools.fetch.plugin import _invoke_fetch
    from openminion.tools.fetch.providers.core_http import (
        CoreHttpFetchProvider,
        _FetchStep,
    )

    provider = CoreHttpFetchProvider()

    for bad_url in (
        "http://127.0.0.1:abc/x",
        "http://[::1/x",
        "http://127.0.0.1:8080/a b",
        "https://example.com:99999/x",
        "https://example.com:0/x",
    ):
        direct = provider.fetch({"url": bad_url, "allow_private_hosts": True})
        assert direct["ok"] is False
        assert direct["error"]["code"] == "INVALID_URL"

        tool_res = _invoke_fetch(
            {"url": bad_url, "allow_private_hosts": True},
            None,
            method="GET",
        )
        assert tool_res["ok"] is False
        assert tool_res["error"]["code"] == "INVALID_URL"

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda _host, port, **_kw: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", int(port)))
        ],
    )

    for bad_location in (
        "/next path",
        "/next\npath",
        "https://example.com:abc/next",
        "http://[::1/next",
    ):
        monkeypatch.setattr(
            "openminion.tools.fetch.providers.core_http._open_once",
            lambda **kwargs: _FetchStep(
                status_code=302,
                final_url=str(kwargs["url"]),
                headers={"location": bad_location},
                body=b"",
            ),
        )
        redirected = provider.fetch(
            {"url": "https://example.com/start", "method": "GET"}
        )
        assert redirected["ok"] is False
        assert redirected["error"]["code"] == "INVALID_URL"

    def _valid_redirect_open(**kwargs):
        if str(kwargs["url"]).endswith("/start"):
            return _FetchStep(
                status_code=302,
                final_url=str(kwargs["url"]),
                headers={"location": "/next%20path"},
                body=b"",
            )
        return _FetchStep(
            status_code=200,
            final_url=str(kwargs["url"]),
            headers={"content-type": "text/plain"},
            body=b"ok",
        )

    monkeypatch.setattr(
        "openminion.tools.fetch.providers.core_http._open_once",
        _valid_redirect_open,
    )
    ok_redirect = provider.fetch({"url": "https://example.com/start", "method": "GET"})
    assert ok_redirect["ok"] is True
    assert ok_redirect["final_url"] == "https://example.com/next%20path"
