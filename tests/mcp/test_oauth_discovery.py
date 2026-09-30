from __future__ import annotations

import io
import json
import threading
from urllib import error as urllib_error

import pytest

from openminion.base.config.mcp import MCPAuthorizationConfig
from openminion.tools.mcp.auth import (
    _validate_authorization_issuer,
    consume_oauth_transaction,
    discover_oauth_metadata,
    InMemoryMCPTokenStore,
    MCPOAuthMetadata,
    MCPOAuthTransaction,
    store_oauth_transaction,
)
from openminion.base.config.mcp import MCPServerConfig
from openminion.tools.mcp.transport import (
    probe_oauth_challenge,
    StreamableHTTPMCPTransport,
)


class _JSONResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode()


def _metadata(issuer: str) -> dict:
    return {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/authorize",
        "token_endpoint": f"{issuer}/token",
        "code_challenge_methods_supported": ["S256"],
        "authorization_response_iss_parameter_supported": True,
    }


def test_oauth_discovery_prefers_challenge_resource_metadata(monkeypatch) -> None:
    requested: list[str] = []
    challenge_url = "https://mcp.example/auth-resource"
    issuer = "https://auth.example/tenant"

    def open_url(request, *, timeout):
        del timeout
        requested.append(request.full_url)
        if request.full_url == challenge_url:
            return _JSONResponse(
                {
                    "resource": "https://mcp.example/mcp",
                    "authorization_servers": [issuer],
                }
            )
        return _JSONResponse(_metadata(issuer))

    monkeypatch.setattr("openminion.tools.mcp.auth.urllib_request.urlopen", open_url)

    metadata = discover_oauth_metadata(
        MCPAuthorizationConfig(mode="oauth_pkce", client_id="client"),
        resource="https://mcp.example/mcp",
        resource_metadata_url=challenge_url,
    )

    assert requested == [
        challenge_url,
        "https://auth.example/.well-known/oauth-authorization-server/tenant",
    ]
    assert metadata.issuer == issuer
    assert metadata.authorization_response_iss_parameter_supported is True


def test_oauth_challenge_probe_requires_protected_resource_metadata(
    monkeypatch,
) -> None:
    def open_url(request, *, timeout):
        del timeout
        assert request.get_method() == "POST"
        assert request.headers["Mcp-method"] == "server/discover"
        payload = json.loads(request.data)
        meta = payload["params"]["_meta"]
        assert "io.modelcontextprotocol/clientCapabilities" in meta
        raise urllib_error.HTTPError(
            request.full_url,
            401,
            "Unauthorized",
            {
                "WWW-Authenticate": (
                    'Bearer resource_metadata="https://mcp.example/auth-resource", '
                    'scope="mcp.read"'
                )
            },
            io.BytesIO(),
        )

    monkeypatch.setattr(
        "openminion.tools.mcp.http_transport.urllib_request.urlopen", open_url
    )

    challenge = probe_oauth_challenge(
        MCPServerConfig(
            name="remote",
            transport="streamable_http",
            url="https://mcp.example/mcp",
        )
    )

    assert challenge == {
        "scheme": "Bearer",
        "resource_metadata": "https://mcp.example/auth-resource",
        "scope": "mcp.read",
    }


def test_oauth_discovery_uses_required_well_known_order(monkeypatch) -> None:
    requested: list[str] = []
    resource = "https://mcp.example/public/mcp"
    issuer = "https://auth.example/tenant"

    def open_url(request, *, timeout):
        del timeout
        url = request.full_url
        requested.append(url)
        if url in {
            "https://mcp.example/.well-known/oauth-protected-resource/public/mcp",
            "https://auth.example/.well-known/oauth-authorization-server/tenant",
        }:
            raise urllib_error.HTTPError(url, 404, "missing", {}, io.BytesIO())
        if url == "https://mcp.example/.well-known/oauth-protected-resource":
            return _JSONResponse(
                {"resource": resource, "authorization_servers": [issuer]}
            )
        return _JSONResponse(_metadata(issuer))

    monkeypatch.setattr("openminion.tools.mcp.auth.urllib_request.urlopen", open_url)

    metadata = discover_oauth_metadata(
        MCPAuthorizationConfig(mode="oauth_pkce", client_id="client"),
        resource=resource,
    )

    assert requested == [
        "https://mcp.example/.well-known/oauth-protected-resource/public/mcp",
        "https://mcp.example/.well-known/oauth-protected-resource",
        "https://auth.example/.well-known/oauth-authorization-server/tenant",
        "https://auth.example/.well-known/openid-configuration/tenant",
    ]
    assert metadata.issuer == issuer


def test_oauth_discovery_rejects_metadata_issuer_mismatch(monkeypatch) -> None:
    def open_url(request, *, timeout):
        del timeout
        if "oauth-protected-resource" in request.full_url:
            return _JSONResponse(
                {
                    "resource": "https://mcp.example/mcp",
                    "authorization_servers": ["https://auth.example"],
                }
            )
        return _JSONResponse(_metadata("https://attacker.example"))

    monkeypatch.setattr("openminion.tools.mcp.auth.urllib_request.urlopen", open_url)

    with pytest.raises(ValueError, match="issuer does not match"):
        discover_oauth_metadata(
            MCPAuthorizationConfig(mode="oauth_pkce", client_id="client"),
            resource="https://mcp.example/mcp",
        )


def test_oauth_discovery_requires_resource_in_protected_metadata(monkeypatch) -> None:
    monkeypatch.setattr(
        "openminion.tools.mcp.auth.urllib_request.urlopen",
        lambda request, *, timeout: _JSONResponse(
            {"authorization_servers": ["https://auth.example"]}
        ),
    )

    with pytest.raises(ValueError, match="must include resource"):
        discover_oauth_metadata(
            MCPAuthorizationConfig(mode="oauth_pkce", client_id="client"),
            resource="https://mcp.example/mcp",
        )


def test_oauth_discovery_binds_configured_metadata_to_resource_issuer(
    monkeypatch,
) -> None:
    requested: list[str] = []
    resource = "https://mcp.example/mcp"
    metadata_url = "https://configured.example/oauth-metadata"

    def open_url(request, *, timeout):
        del timeout
        requested.append(request.full_url)
        if "oauth-protected-resource" in request.full_url:
            return _JSONResponse(
                {
                    "resource": resource,
                    "authorization_servers": ["https://auth.example"],
                }
            )
        return _JSONResponse(_metadata("https://auth.example"))

    monkeypatch.setattr("openminion.tools.mcp.auth.urllib_request.urlopen", open_url)

    result = discover_oauth_metadata(
        MCPAuthorizationConfig(
            mode="oauth_pkce",
            client_id="client",
            authorization_server_metadata_url=metadata_url,
        ),
        resource=resource,
    )

    assert requested == [
        "https://mcp.example/.well-known/oauth-protected-resource/mcp",
        metadata_url,
    ]
    assert result.issuer == "https://auth.example"


def test_oauth_discovery_enforces_configured_endpoint_pins(monkeypatch) -> None:
    resource = "https://mcp.example/mcp"

    def open_url(request, *, timeout):
        del timeout
        if "oauth-protected-resource" in request.full_url:
            return _JSONResponse(
                {
                    "resource": resource,
                    "authorization_servers": ["https://auth.example"],
                }
            )
        return _JSONResponse(_metadata("https://auth.example"))

    monkeypatch.setattr("openminion.tools.mcp.auth.urllib_request.urlopen", open_url)
    config = MCPAuthorizationConfig(
        mode="oauth_pkce",
        client_id="client",
        authorization_endpoint="https://wrong.example/authorize",
    )

    with pytest.raises(ValueError, match="authorization_endpoint"):
        discover_oauth_metadata(config, resource=resource)


def test_oauth_challenge_metadata_replaces_cached_discovery(monkeypatch) -> None:
    discovered: list[str] = []

    def discover(_config, *, resource, resource_metadata_url, timeout_seconds):
        del resource, timeout_seconds
        discovered.append(resource_metadata_url)
        issuer = (
            "https://challenge.example"
            if resource_metadata_url
            else "https://cached.example"
        )
        return MCPOAuthMetadata(
            authorization_endpoint=f"{issuer}/authorize",
            token_endpoint=f"{issuer}/token",
            issuer=issuer,
        )

    monkeypatch.setattr(
        "openminion.tools.mcp.http_transport.discover_oauth_metadata", discover
    )
    transport = StreamableHTTPMCPTransport(
        MCPServerConfig(
            name="oauth",
            transport="streamable_http",
            url="https://mcp.example/mcp",
            authorization=MCPAuthorizationConfig(mode="oauth_pkce", client_id="client"),
        )
    )

    assert transport._oauth_metadata_for_server().issuer == "https://cached.example"  # noqa: SLF001
    assert (
        transport._oauth_metadata_for_server(  # noqa: SLF001
            resource_metadata_url="https://mcp.example/challenge"
        ).issuer
        == "https://challenge.example"
    )
    assert discovered == ["", "https://mcp.example/challenge"]


@pytest.mark.parametrize(
    ("required", "received", "raises"),
    [
        (True, "https://auth.example", False),
        (True, "", True),
        (False, "https://auth.example", False),
        (False, "", False),
    ],
)
def test_authorization_response_issuer_matrix(
    required: bool, received: str, raises: bool
) -> None:
    if raises:
        with pytest.raises(ValueError, match="issuer"):
            _validate_authorization_issuer(
                expected="https://auth.example",
                received=received,
                required=required,
            )
        return
    _validate_authorization_issuer(
        expected="https://auth.example",
        received=received,
        required=required,
    )


def test_authorization_response_issuer_rejects_mismatch() -> None:
    with pytest.raises(ValueError, match="issuer does not match"):
        _validate_authorization_issuer(
            expected="https://auth.example",
            received="https://other.example",
            required=False,
        )


def _transaction(
    *, state: str = "state", expires_at: float = 200.0
) -> MCPOAuthTransaction:
    return MCPOAuthTransaction(
        server_name="remote",
        resource="https://mcp.example/mcp",
        redirect_uri="http://127.0.0.1/callback",
        state=state,
        code_verifier="private-verifier",
        issuer="https://auth.example",
        issuer_required=True,
        authorization_endpoint="https://auth.example/authorize",
        token_endpoint="https://auth.example/token",
        expires_at=expires_at,
    )


def test_oauth_transaction_is_consumed_once() -> None:
    store = InMemoryMCPTokenStore({})
    store_oauth_transaction(store, _transaction())

    result = consume_oauth_transaction(
        store, server_name="remote", state="state", now=100.0
    )

    assert result.code_verifier == "private-verifier"
    with pytest.raises(ValueError, match="unknown or already used"):
        consume_oauth_transaction(store, server_name="remote", state="state", now=100.0)


def test_oauth_transaction_has_one_concurrent_consumer() -> None:
    store = InMemoryMCPTokenStore({})
    store_oauth_transaction(store, _transaction())
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def consume() -> None:
        barrier.wait()
        try:
            consume_oauth_transaction(
                store, server_name="remote", state="state", now=100.0
            )
        except ValueError:
            outcomes.append("rejected")
        else:
            outcomes.append("consumed")

    threads = [threading.Thread(target=consume) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert sorted(outcomes) == ["consumed", "rejected"]


def test_oauth_transaction_rejects_unknown_expired_and_mismatched_state() -> None:
    store = InMemoryMCPTokenStore({})
    with pytest.raises(ValueError, match="unknown"):
        consume_oauth_transaction(
            store, server_name="remote", state="missing", now=100.0
        )

    store_oauth_transaction(store, _transaction(expires_at=50.0))
    with pytest.raises(ValueError, match="expired"):
        consume_oauth_transaction(store, server_name="remote", state="state", now=100.0)

    store_oauth_transaction(store, _transaction(state="other"))
    with pytest.raises(ValueError, match="unknown"):
        consume_oauth_transaction(store, server_name="remote", state="state", now=100.0)
