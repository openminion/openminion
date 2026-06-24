"""Search tool error mapping."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from .constants import (
    SEARCH_FAULT_AUTH_FAILED,
    SEARCH_FAULT_HTTP_5XX,
    SEARCH_FAULT_MALFORMED_RESPONSE,
    SEARCH_FAULT_NETWORK_TIMEOUT,
    SEARCH_FAULT_RATE_LIMITED,
    SEARCH_FAULT_UNAVAILABLE,
)
from .providers import SearchProvider, SearchProviderError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from openminion.modules.tool.runtime import RuntimeContext


SearchFaultMode = Literal[
    "network_timeout",
    "http_5xx",
    "rate_limited",
    "auth_failed",
    "malformed_response",
    "unavailable",
]

_FAULT_PROFILE: dict[SearchFaultMode, tuple[str, str, str]] = {
    "network_timeout": (
        "UPSTREAM_ERROR",
        SEARCH_FAULT_NETWORK_TIMEOUT,
        "search provider network timeout (deterministic fault injection)",
    ),
    "http_5xx": (
        "UPSTREAM_ERROR",
        SEARCH_FAULT_HTTP_5XX,
        "search provider returned HTTP 5xx (deterministic fault injection)",
    ),
    "rate_limited": (
        "RATE_LIMITED",
        SEARCH_FAULT_RATE_LIMITED,
        "search provider rate-limited the request (deterministic fault injection)",
    ),
    "auth_failed": (
        "AUTH_INVALID",
        SEARCH_FAULT_AUTH_FAILED,
        "search provider rejected credentials (deterministic fault injection)",
    ),
    "malformed_response": (
        "REMOTE_PROTOCOL_ERROR",
        SEARCH_FAULT_MALFORMED_RESPONSE,
        "search provider returned malformed response (deterministic fault injection)",
    ),
    "unavailable": (
        "UPSTREAM_ERROR",
        SEARCH_FAULT_UNAVAILABLE,
        "search provider unavailable (deterministic fault injection)",
    ),
}


@dataclass(frozen=True)
class SearchFaultProfile:
    mode: SearchFaultMode
    http_status: int | None = None


class SearchProviderFaultHarness:
    def __init__(
        self,
        wrapped: SearchProvider,
        *,
        fault: SearchFaultProfile | None = None,
    ) -> None:
        self._wrapped = wrapped
        self._fault = fault

    @property
    def provider_id(self) -> str:
        return str(getattr(self._wrapped, "provider_id", "") or "")

    @property
    def display_name(self) -> str:
        return str(getattr(self._wrapped, "display_name", "") or self.provider_id)

    @property
    def fault(self) -> SearchFaultProfile | None:
        return self._fault

    def search(
        self,
        query: str,
        *,
        max_results: int,
        args: Mapping[str, Any],
        ctx: "RuntimeContext",
    ) -> Mapping[str, Any]:
        if self._fault is None:
            return self._wrapped.search(
                query,
                max_results=max_results,
                args=args,
                ctx=ctx,
            )
        raise _build_fault_error(
            provider_id=self.provider_id,
            fault=self._fault,
        )

    def healthcheck(self, ctx: "RuntimeContext | None" = None) -> bool:
        if self._fault is not None:
            return False
        return bool(self._wrapped.healthcheck(ctx))


def _build_fault_error(
    *,
    provider_id: str,
    fault: SearchFaultProfile,
) -> SearchProviderError:
    code, reason_code, message = _FAULT_PROFILE[fault.mode]
    details: dict[str, Any] = {
        "reason_code": reason_code,
        "provider_id": provider_id,
        "fault_mode": fault.mode,
        "deterministic_fault": True,
    }
    if fault.http_status is not None:
        details["http_status"] = int(fault.http_status)
    return SearchProviderError(
        message=message,
        code=code,
        details=details,
    )


__all__ = [
    "SearchFaultMode",
    "SearchFaultProfile",
    "SearchProviderFaultHarness",
]
