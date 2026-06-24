from openminion.services.gateway.authz import (
    authorize_method,
    default_method_authorization_rules,
)


def test_authorize_method_allows_unknown_methods() -> None:
    error = authorize_method(
        method="custom.method",
        role="operator",
        scopes=["operator.read"],
        rules=default_method_authorization_rules(),
    )
    assert error is None


def test_authorize_method_denies_missing_scope() -> None:
    error = authorize_method(
        method="turn.send",
        role="operator",
        scopes=["operator.read"],
        rules=default_method_authorization_rules(),
    )
    assert error is not None
    assert error.code == "auth_denied"
    assert "missing_scopes" in (error.details or {})


def test_authorize_method_denies_wrong_role() -> None:
    error = authorize_method(
        method="admin.reload",
        role="node",
        scopes=["operator.admin"],
        rules=default_method_authorization_rules(),
    )
    assert error is not None
    assert error.code == "auth_denied"
    assert "allowed_roles" in (error.details or {})
