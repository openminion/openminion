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
