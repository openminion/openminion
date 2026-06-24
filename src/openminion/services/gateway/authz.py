from dataclasses import dataclass
from typing import Iterable, Mapping

from openminion.base.protocol import ProtocolError


@dataclass(frozen=True)
class MethodAuthorizationRule:
    allowed_roles: tuple[str, ...]
    required_scopes: tuple[str, ...] = ()


def default_method_authorization_rules() -> dict[str, MethodAuthorizationRule]:
    return {
        "turn.send": MethodAuthorizationRule(
            allowed_roles=("operator", "node"),
            required_scopes=("operator.write",),
        ),
        "session.get": MethodAuthorizationRule(
            allowed_roles=("operator", "node"),
            required_scopes=("operator.read",),
        ),
        "status.get": MethodAuthorizationRule(
            allowed_roles=("operator", "node"),
            required_scopes=("operator.read",),
        ),
        "admin.reload": MethodAuthorizationRule(
            allowed_roles=("operator",),
            required_scopes=("operator.admin",),
        ),
    }


def authorize_method(
    *,
    method: str,
    role: str,
    scopes: Iterable[str],
    rules: Mapping[str, MethodAuthorizationRule],
) -> ProtocolError | None:
    normalized_method = str(method).strip()
    rule = rules.get(normalized_method)
    if rule is None:
        return None

    normalized_role = str(role).strip().lower()
    if normalized_role not in rule.allowed_roles:
        return ProtocolError(
            "auth_denied",
            f"Role '{normalized_role}' is not allowed for method '{normalized_method}'.",
            details={
                "method": normalized_method,
                "role": normalized_role,
                "allowed_roles": list(rule.allowed_roles),
                "missing_scopes": [],
            },
            retryable=False,
        )

    normalized_scopes = {
        scope.strip().lower() for scope in scopes if str(scope).strip()
    }
    missing_scopes = [
        scope for scope in rule.required_scopes if scope not in normalized_scopes
    ]
    if missing_scopes:
        return ProtocolError(
            "auth_denied",
            f"Missing required scopes for method '{normalized_method}'.",
            details={
                "method": normalized_method,
                "role": normalized_role,
                "required_scopes": list(rule.required_scopes),
                "missing_scopes": missing_scopes,
            },
            retryable=False,
        )
    return None
