from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

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
        subject_id: Optional[str] = None,
        effect: Optional[str] = None,
        tool: Optional[str] = None,
        method: Optional[str] = None,
        active_only: bool = False,
    ) -> list[PolicyGrant]: ...

    @abstractmethod
    def get_grant(self, grant_id: str) -> Optional[PolicyGrant]: ...

    @abstractmethod
    def consume_grant_use(self, grant_id: str) -> Optional[PolicyGrant]: ...

    @abstractmethod
    def cleanup_expired(self) -> int: ...

    @abstractmethod
    def log_decision(
        self,
        *,
        trace_id: Optional[str],
        session_id: Optional[str],
        agent_id: Optional[str],
        invocation_id: str,
        tool: str,
        method: str,
        decision: str,
        matched_grant_id: Optional[str],
        reason_code: str,
        risk_spec: Dict[str, Any],
    ) -> str: ...

    @abstractmethod
    def list_decisions(self, *, limit: int = 100) -> list[Dict[str, Any]]: ...

    @abstractmethod
    def set_setting(self, key: str, value: str) -> None: ...

    @abstractmethod
    def get_setting(self, key: str) -> Optional[str]: ...

    @abstractmethod
    def close(self) -> None: ...
