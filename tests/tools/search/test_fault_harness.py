from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from openminion.tools.search.constants import (
    SEARCH_FAULT_AUTH_FAILED,
    SEARCH_FAULT_HTTP_5XX,
    SEARCH_FAULT_MALFORMED_RESPONSE,
    SEARCH_FAULT_NETWORK_TIMEOUT,
    SEARCH_FAULT_RATE_LIMITED,
    SEARCH_FAULT_UNAVAILABLE,
)
from openminion.tools.search.fault import (
    SearchFaultProfile,
    SearchProviderFaultHarness,
)
from openminion.tools.search.providers import SearchProviderError


class _StubProvider:
    provider_id = "stub"
    display_name = "Stub Provider"

    def __init__(self) -> None:
        self.search_calls: list[tuple[str, int]] = []
        self.healthcheck_return = True

    def search(
        self,
        query: str,
        *,
        max_results: int,
        args: Mapping[str, Any],
        ctx: Any,
    ) -> Mapping[str, Any]:
        del args, ctx
        self.search_calls.append((query, max_results))
        return {
            "results": [{"title": f"result for {query}", "url": "https://example.com"}]
        }

    def healthcheck(self, ctx: Any = None) -> bool:
        del ctx
        return self.healthcheck_return


def _make_harness(
    *,
    fault: SearchFaultProfile | None = None,
) -> tuple[SearchProviderFaultHarness, _StubProvider]:
    stub = _StubProvider()
    return SearchProviderFaultHarness(stub, fault=fault), stub


# --- pass-through behavior (fault=None) ------------------------------------


def test_harness_with_no_fault_is_transparent_passthrough() -> None:
    harness, stub = _make_harness(fault=None)
    result = harness.search("openminion", max_results=5, args={}, ctx=None)
    assert stub.search_calls == [("openminion", 5)]
    assert result["results"][0]["title"] == "result for openminion"


def test_harness_with_no_fault_proxies_healthcheck() -> None:
    harness, stub = _make_harness(fault=None)
    stub.healthcheck_return = True
    assert harness.healthcheck() is True
    stub.healthcheck_return = False
    assert harness.healthcheck() is False


def test_harness_exposes_wrapped_provider_id_and_display_name() -> None:
    harness, _ = _make_harness(fault=None)
    assert harness.provider_id == "stub"
    assert harness.display_name == "Stub Provider"


# --- fault injection: each typed fault emits the right reason code --------


@pytest.mark.parametrize(
    "fault_mode,expected_code,expected_reason_code",
    [
        ("network_timeout", "UPSTREAM_ERROR", SEARCH_FAULT_NETWORK_TIMEOUT),
        ("http_5xx", "UPSTREAM_ERROR", SEARCH_FAULT_HTTP_5XX),
        ("rate_limited", "RATE_LIMITED", SEARCH_FAULT_RATE_LIMITED),
        ("auth_failed", "AUTH_INVALID", SEARCH_FAULT_AUTH_FAILED),
        (
            "malformed_response",
            "REMOTE_PROTOCOL_ERROR",
            SEARCH_FAULT_MALFORMED_RESPONSE,
        ),
        ("unavailable", "UPSTREAM_ERROR", SEARCH_FAULT_UNAVAILABLE),
    ],
)
def test_harness_raises_typed_error_for_each_fault_mode(
    fault_mode: str,
    expected_code: str,
    expected_reason_code: str,
) -> None:
    harness, stub = _make_harness(
        fault=SearchFaultProfile(mode=fault_mode)  # type: ignore[arg-type]
    )
    with pytest.raises(SearchProviderError) as exc:
        harness.search("x", max_results=1, args={}, ctx=None)
    assert exc.value.code == expected_code
    assert exc.value.details is not None
    assert exc.value.details.get("reason_code") == expected_reason_code
    assert exc.value.details.get("fault_mode") == fault_mode
    assert exc.value.details.get("deterministic_fault") is True
    assert exc.value.details.get("provider_id") == "stub"
    # The wrapped provider's search must NOT have been called when a fault
    # is configured — the harness short-circuits.
    assert stub.search_calls == []


def test_harness_surfaces_http_status_in_details_when_provided() -> None:
    harness, _ = _make_harness(
        fault=SearchFaultProfile(mode="http_5xx", http_status=503),
    )
    with pytest.raises(SearchProviderError) as exc:
        harness.search("x", max_results=1, args={}, ctx=None)
    assert exc.value.details is not None
    assert exc.value.details.get("http_status") == 503


def test_harness_omits_http_status_when_not_provided() -> None:
    harness, _ = _make_harness(
        fault=SearchFaultProfile(mode="network_timeout"),
    )
    with pytest.raises(SearchProviderError) as exc:
        harness.search("x", max_results=1, args={}, ctx=None)
    assert exc.value.details is not None
    assert "http_status" not in exc.value.details


# --- recovery observability via healthcheck --------------------------------


def test_harness_healthcheck_is_false_under_any_fault() -> None:
    for mode in (
        "network_timeout",
        "http_5xx",
        "rate_limited",
        "auth_failed",
        "malformed_response",
        "unavailable",
    ):
        harness, stub = _make_harness(
            fault=SearchFaultProfile(mode=mode)  # type: ignore[arg-type]
        )
        # Even when the wrapped provider claims healthy, the fault overrides.
        stub.healthcheck_return = True
        assert harness.healthcheck() is False, (
            f"fault {mode!r} must short-circuit healthcheck"
        )


# --- discipline contract: fault profile is structural, not prose ----------


def test_fault_error_message_is_fixed_per_mode_not_runtime_authored() -> None:
    harness, _ = _make_harness(
        fault=SearchFaultProfile(mode="network_timeout"),
    )
    with pytest.raises(SearchProviderError) as exc:
        harness.search("x", max_results=1, args={}, ctx=None)
    assert "deterministic fault injection" in str(exc.value)


def test_fault_profile_mode_set_is_closed_to_typed_literal() -> None:
    harness, _ = _make_harness(
        # Bypass the type-checker to assert runtime defense.
        fault=SearchFaultProfile(mode="not_a_real_mode"),  # type: ignore[arg-type]
    )
    with pytest.raises(KeyError):
        harness.search("x", max_results=1, args={}, ctx=None)
