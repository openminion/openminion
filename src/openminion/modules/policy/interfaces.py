from typing import Any, ClassVar, Protocol


POLICY_INTERFACE_VERSION = "v1"
_REQUIRED_METHODS = (
    "close",
    "mode",
    "set_mode",
    "register_risk",
    "check",
    "create_grant",
    "create_grant_from_confirmation",
    "revoke_grant",
    "list_grants",
    "cleanup_expired",
    "list_decisions",
)


class PolicyCompatibilityError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class PolicyCtlInterface(Protocol):
    """Policy Control interface contract."""

    contract_version: ClassVar[str] = POLICY_INTERFACE_VERSION

    def __init__(
        self,
        *,
        store: Any,  # SQLitePolicyStore,
        config: Any | None = None,  # PolicyConfig
        risk_registry: dict[str, Any] | None = None,  # RiskSpec
    ) -> None: ...

    @staticmethod
    def with_sqlite(
        database_path: str,
        *,
        config: Any | None = None,
        risk_registry: dict[str, Any] | None = None,
    ) -> Any: ...  # PolicyCtl

    def close(self) -> None: ...

    def mode(self) -> str: ...

    def set_mode(self, mode: str) -> None: ...

    def register_risk(self, key: str, spec: Any) -> None: ...  # RiskSpec

    def check(
        self,
        invocation: Any,
        ctx: Any,
        *,
        risk_override: Any | None = None,
    ) -> Any: ...  # PolicyDecision

    def create_grant(self, grant: Any) -> str: ...  # PolicyGrantInput

    def create_grant_from_confirmation(
        self,
        *,
        invocation: Any,
        ctx: Any,
        action: str,
        until_seconds: int | None = None,
        scope_overrides: dict[str, Any] | None = None,
        max_uses: int | None = None,
    ) -> str: ...

    def revoke_grant(self, grant_id: str) -> bool: ...

    def list_grants(
        self,
        *,
        subject_id: str | None = None,
        effect: str | None = None,
        tool: str | None = None,
        method: str | None = None,
        active_only: bool = False,
    ) -> list[Any]: ...  # List[PolicyGrant]

    def cleanup_expired(self) -> int: ...

    def list_decisions(self, *, limit: int = 100) -> list[dict[str, Any]]: ...


def ensure_policy_compatibility(
    ctl: Any, strict: bool = True
) -> tuple[bool, list[str]]:
    """Validate policy controller implements the required interface."""
    errors: list[str] = []
    if not hasattr(ctl, "contract_version"):
        errors.append("Missing contract_version attribute")
    elif ctl.contract_version != POLICY_INTERFACE_VERSION:
        errors.append(
            f"Version mismatch: expected {POLICY_INTERFACE_VERSION}, "
            f"got {ctl.contract_version}"
        )
    for method in _REQUIRED_METHODS:
        if not hasattr(ctl, method) or not callable(getattr(ctl, method)):
            errors.append(f"Missing required method: {method}")

    if errors:
        if strict:
            raise PolicyCompatibilityError(
                "POLICY_CTL_INTERFACE_VIOLATION",
                f"Policy controller incompatible: {errors}",
            )
        return False, errors

    return True, []
