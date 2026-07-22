from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..models import PolicyGrant, PolicyGrantInput


class PolicyStore(ABC):
    """Abstract base for policy storage implementations."""

    @abstractmethod
    def create_grant(self, grant: PolicyGrantInput) -> str: ...

    @abstractmethod
    def revoke_grant(self, grant_id: str) -> bool: ...

    @abstractmethod
    def list_grants(
        self,
        *,
        subject_id: str | None = None,
        effect: str | None = None,
        tool: str | None = None,
        method: str | None = None,
        active_only: bool = False,
    ) -> list[PolicyGrant]: ...

    @abstractmethod
    def get_grant(self, grant_id: str) -> PolicyGrant | None: ...

    @abstractmethod
    def consume_grant_use(self, grant_id: str) -> PolicyGrant | None: ...

    @abstractmethod
    def cleanup_expired(self) -> int: ...

    @abstractmethod
    def log_decision(
        self,
        *,
        trace_id: str | None,
        session_id: str | None,
        agent_id: str | None,
        invocation_id: str,
        tool: str,
        method: str,
        decision: str,
        matched_grant_id: str | None,
        reason_code: str,
        risk_spec: dict[str, Any],
    ) -> str: ...

    @abstractmethod
    def list_decisions(self, *, limit: int = 100) -> list[dict[str, Any]]: ...

    @abstractmethod
    def set_setting(self, key: str, value: str) -> None: ...

    @abstractmethod
    def get_setting(self, key: str) -> str | None: ...

    @abstractmethod
    def close(self) -> None: ...
