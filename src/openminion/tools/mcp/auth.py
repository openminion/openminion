"""MCP OAuth authorization helpers."""

import base64
import hashlib
import json
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib import parse as urllib_parse
from urllib import error as urllib_error
from urllib import request as urllib_request

from openminion.base.config.mcp import MCPAuthorizationConfig
from openminion.base.config.runtime import RuntimeConfig
from openminion.modules.runtime.sync import run_async_compat
from openminion.modules.secret.schemas import SecretNotFoundError


class MCPTokenStore(Protocol):
    """Synchronous token-state boundary used by MCP transports."""

    def get(self, ref: str) -> str: ...

    def set(self, ref: str, value: str) -> None: ...

    def consume(self, ref: str) -> str: ...

    def delete(self, ref: str) -> None: ...

    def close(self) -> None: ...


def read_token_ref(token_store: MCPTokenStore | None, ref: str) -> str:
    if token_store is None or not ref:
        return ""
    return str(token_store.get(ref) or "").strip()


@dataclass
class InMemoryMCPTokenStore:
    """Test/default token store that keeps secrets out of config exports."""

    values: dict[str, str]
    _lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False
    )

    def get(self, ref: str) -> str:
        return self.values.get(ref, "")

    def set(self, ref: str, value: str) -> None:
        self.values[ref] = value

    def consume(self, ref: str) -> str:
        with self._lock:
            return self.values.pop(ref, "")

    def delete(self, ref: str) -> None:
        self.values.pop(ref, None)

    def close(self) -> None:
        return None


class SecretServiceMCPTokenStore:
    """MCP token adapter over OpenMinion's encrypted secret service."""

    def __init__(self, service: Any) -> None:
        self._service = service

    def get(self, ref: str) -> str:
        try:
            return str(self._service.get_secret_sync(ref, namespace="mcp") or "")
        except SecretNotFoundError:
            return ""

    def set(self, ref: str, value: str) -> None:
        run_async_compat(self._service.set_secret(ref, value, namespace="mcp"))

    def consume(self, ref: str) -> str:
        try:
            return str(self._service.consume_secret_sync(ref, namespace="mcp") or "")
        except SecretNotFoundError:
            return ""

    def delete(self, ref: str) -> None:
        run_async_compat(self._service.delete_secret(ref, namespace="mcp"))

    def close(self) -> None:
        self._service.close_sync()


def build_runtime_mcp_token_store(
    runtime_config: RuntimeConfig,
) -> MCPTokenStore | None:
    """Build encrypted MCP token storage when the runtime secret key is set."""

    servers = runtime_config.mcp_servers
    needs_store = any(
        server.enabled
        and (
            server.env_secret_refs
            or server.authorization.bearer_token_ref
            or server.authorization.access_token_ref
            or server.authorization.refresh_token_ref
        )
        for server in servers
    )
    if not needs_store:
        return None

    from openminion.base.config.env import resolve_environment_config
    from openminion.base.config.paths import resolve_data_root, resolve_home_root
    from openminion.modules.secret.factory import build_secret_service

    env = resolve_environment_config(runtime_env=runtime_config.env)
    home_root = resolve_home_root(env=env.values)
    service = build_secret_service(
        data_root=resolve_data_root(home_root, env=env.values),
        env=env,
    )
    return SecretServiceMCPTokenStore(service) if service is not None else None


@dataclass(frozen=True)
class MCPOAuthMetadata:
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str = ""
    revocation_endpoint: str = ""
    issuer: str = ""
    code_challenge_methods_supported: tuple[str, ...] = ()
    client_id_metadata_document_supported: bool = False
    authorization_response_iss_parameter_supported: bool = False


@dataclass(frozen=True)
class MCPOAuthPKCEChallenge:
    code_verifier: str
    code_challenge: str
    method: str = "S256"


@dataclass(frozen=True)
class MCPOAuthTokenState:
    access_token: str
    refresh_token: str = ""
    expires_at: float = 0.0
    scope: str = ""
    issuer: str = ""

    @property
    def expired(self) -> bool:
        return bool(self.expires_at) and time.time() >= self.expires_at


@dataclass(frozen=True)
class MCPOAuthTransaction:
    server_name: str
    resource: str
    redirect_uri: str
    state: str
    code_verifier: str
    issuer: str
    issuer_required: bool
    authorization_endpoint: str
    token_endpoint: str
    expires_at: float
    resource_metadata_url: str = ""
    scope: str = ""


def discover_oauth_metadata(
    config: MCPAuthorizationConfig,
    *,
    resource: str = "",
    resource_metadata_url: str = "",
    timeout_seconds: float = 10.0,
) -> MCPOAuthMetadata:
    """Resolve validated OAuth metadata for an MCP resource."""

    issuer = _discover_authorization_server(
        resource=resource,
        resource_metadata_url=resource_metadata_url,
        timeout_seconds=timeout_seconds,
    )
    metadata_urls = (
        [config.authorization_server_metadata_url]
        if config.authorization_server_metadata_url
        else _authorization_metadata_urls(issuer)
    )
    for metadata_url in metadata_urls:
        try:
            payload = _load_json_object(metadata_url, timeout_seconds)
        except urllib_error.HTTPError as exc:
            if exc.code == 404:
                continue
            raise
        metadata = _oauth_metadata_from_payload(payload, expected_issuer=issuer)
        _validate_configured_oauth_metadata(config, metadata)
        return metadata
    raise ValueError("authorization server metadata is unavailable")


def _validate_configured_oauth_metadata(
    config: MCPAuthorizationConfig, metadata: MCPOAuthMetadata
) -> None:
    for field_name in (
        "authorization_endpoint",
        "token_endpoint",
        "registration_endpoint",
        "revocation_endpoint",
    ):
        configured = str(getattr(config, field_name, "") or "").strip()
        if configured and configured != getattr(metadata, field_name):
            raise ValueError(
                f"discovered OAuth {field_name} does not match configuration"
            )


def _oauth_metadata_from_payload(
    payload: dict[str, Any], *, expected_issuer: str = ""
) -> MCPOAuthMetadata:
    issuer = str(payload.get("issuer", "") or "").strip()
    if expected_issuer and issuer != expected_issuer:
        raise ValueError("OAuth metadata issuer does not match selected issuer")
    authorization_endpoint = str(
        payload.get("authorization_endpoint", "") or ""
    ).strip()
    token_endpoint = str(payload.get("token_endpoint", "") or "").strip()
    if not authorization_endpoint or not token_endpoint:
        raise ValueError(
            "OAuth metadata must include authorization_endpoint and token_endpoint"
        )
    code_challenge_methods = tuple(
        str(item).strip()
        for item in payload.get("code_challenge_methods_supported", [])
        if str(item).strip()
    )
    if "S256" not in code_challenge_methods:
        raise ValueError("OAuth metadata must advertise PKCE S256 support")
    return MCPOAuthMetadata(
        authorization_endpoint=authorization_endpoint,
        token_endpoint=token_endpoint,
        registration_endpoint=str(
            payload.get("registration_endpoint", "") or ""
        ).strip(),
        revocation_endpoint=str(payload.get("revocation_endpoint", "") or "").strip(),
        issuer=issuer,
        code_challenge_methods_supported=code_challenge_methods,
        client_id_metadata_document_supported=(
            payload.get("client_id_metadata_document_supported") is True
        ),
        authorization_response_iss_parameter_supported=(
            payload.get("authorization_response_iss_parameter_supported") is True
        ),
    )


def _discover_authorization_server(
    *, resource: str, resource_metadata_url: str, timeout_seconds: float
) -> str:
    urls = (
        [resource_metadata_url]
        if resource_metadata_url
        else _protected_resource_metadata_urls(resource)
    )
    if not urls:
        raise ValueError("oauth_pkce discovery requires the MCP resource URL")
    for metadata_url in urls:
        try:
            payload = _load_json_object(metadata_url, timeout_seconds)
        except urllib_error.HTTPError as exc:
            if exc.code == 404:
                continue
            raise
        advertised_resource = str(payload.get("resource", "") or "").strip()
        if not advertised_resource:
            raise ValueError("protected resource metadata must include resource")
        if advertised_resource != resource:
            raise ValueError("protected resource metadata resource does not match")
        issuers = payload.get("authorization_servers")
        if not isinstance(issuers, list) or not issuers:
            raise ValueError(
                "protected resource metadata must include authorization_servers"
            )
        issuer = str(issuers[0] or "").strip()
        if not issuer:
            raise ValueError("protected resource metadata contains an empty issuer")
        return issuer
    raise ValueError("protected resource metadata is unavailable")


def _protected_resource_metadata_urls(resource: str) -> list[str]:
    parsed = urllib_parse.urlsplit(str(resource or "").strip())
    if not parsed.scheme or not parsed.netloc:
        return []
    origin = f"{parsed.scheme}://{parsed.netloc}"
    path = parsed.path.rstrip("/")
    urls = [f"{origin}/.well-known/oauth-protected-resource{path}"] if path else []
    root = f"{origin}/.well-known/oauth-protected-resource"
    if root not in urls:
        urls.append(root)
    return urls


def _authorization_metadata_urls(issuer: str) -> list[str]:
    parsed = urllib_parse.urlsplit(issuer)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("authorization server issuer must be an absolute URL")
    origin = f"{parsed.scheme}://{parsed.netloc}"
    path = parsed.path.rstrip("/")
    if path:
        return [
            f"{origin}/.well-known/oauth-authorization-server{path}",
            f"{origin}/.well-known/openid-configuration{path}",
            f"{origin}{path}/.well-known/openid-configuration",
        ]
    return [
        f"{origin}/.well-known/oauth-authorization-server",
        f"{origin}/.well-known/openid-configuration",
    ]


def _load_json_object(url: str, timeout_seconds: float) -> dict[str, Any]:
    request = urllib_request.Request(
        url,
        headers={"Accept": "application/json"},
        method="GET",
    )
    with urllib_request.urlopen(request, timeout=float(timeout_seconds)) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"OAuth metadata at {url!r} must be a JSON object")
    return payload


def build_pkce_challenge() -> MCPOAuthPKCEChallenge:
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return MCPOAuthPKCEChallenge(code_verifier=verifier, code_challenge=challenge)


def build_authorization_url(
    *,
    config: MCPAuthorizationConfig,
    metadata: MCPOAuthMetadata,
    challenge: MCPOAuthPKCEChallenge,
    state: str,
    resource: str = "",
    scope: str = "",
) -> str:
    query = {
        "response_type": "code",
        "client_id": config.client_id,
        "redirect_uri": config.redirect_uri,
        "code_challenge": challenge.code_challenge,
        "code_challenge_method": challenge.method,
        "state": state,
    }
    requested_scope = scope.strip() or config.scope
    if requested_scope:
        query["scope"] = requested_scope
    if resource:
        query["resource"] = resource
    return f"{metadata.authorization_endpoint}?{urllib_parse.urlencode(query)}"


def exchange_authorization_code(
    *,
    config: MCPAuthorizationConfig,
    metadata: MCPOAuthMetadata,
    code: str,
    challenge: MCPOAuthPKCEChallenge,
    authorization_issuer: str = "",
    resource: str = "",
    scope: str = "",
    timeout_seconds: float = 10.0,
) -> MCPOAuthTokenState:
    _validate_authorization_issuer(
        expected=metadata.issuer,
        received=authorization_issuer,
        required=metadata.authorization_response_iss_parameter_supported,
    )
    fields = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": config.client_id,
        "redirect_uri": config.redirect_uri,
        "code_verifier": challenge.code_verifier,
    }
    if resource:
        fields["resource"] = resource
    if scope:
        fields["scope"] = scope.strip()
    return _request_token(
        metadata.token_endpoint,
        fields,
        issuer=metadata.issuer,
        timeout_seconds=timeout_seconds,
    )


def refresh_oauth_access_token(
    *,
    config: MCPAuthorizationConfig,
    metadata: MCPOAuthMetadata,
    refresh_token: str,
    resource: str = "",
    scope: str = "",
    timeout_seconds: float = 10.0,
) -> MCPOAuthTokenState:
    fields = {
        "grant_type": "refresh_token",
        "client_id": config.client_id,
        "refresh_token": refresh_token,
    }
    if resource:
        fields["resource"] = resource
    if scope:
        fields["scope"] = scope.strip()
    return _request_token(
        metadata.token_endpoint,
        fields,
        issuer=metadata.issuer,
        timeout_seconds=timeout_seconds,
    )


def register_oauth_client(
    *,
    metadata: MCPOAuthMetadata,
    client_name: str,
    redirect_uris: list[str],
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    if not metadata.registration_endpoint:
        raise ValueError(
            "OAuth metadata does not advertise dynamic client registration"
        )
    payload = json.dumps(
        {
            "client_name": client_name.strip() or "openminion",
            "application_type": "native",
            "redirect_uris": [item.strip() for item in redirect_uris if item.strip()],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        },
        separators=(",", ":"),
    ).encode("utf-8")
    request = urllib_request.Request(
        metadata.registration_endpoint,
        data=payload,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib_request.urlopen(request, timeout=float(timeout_seconds)) as response:
        decoded = json.loads(response.read().decode("utf-8"))
    return dict(decoded) if isinstance(decoded, dict) else {}


def revoke_oauth_token(
    *,
    metadata: MCPOAuthMetadata,
    token: str,
    timeout_seconds: float = 10.0,
) -> bool:
    if not metadata.revocation_endpoint:
        return False
    body = urllib_parse.urlencode({"token": token}).encode("utf-8")
    request = urllib_request.Request(
        metadata.revocation_endpoint,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib_request.urlopen(request, timeout=float(timeout_seconds)) as response:
        return int(getattr(response, "status", response.getcode()) or 0) in {200, 202}


def _request_token(
    endpoint: str,
    fields: dict[str, str],
    *,
    issuer: str,
    timeout_seconds: float,
) -> MCPOAuthTokenState:
    body = urllib_parse.urlencode(fields).encode("utf-8")
    request = urllib_request.Request(
        endpoint,
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    with urllib_request.urlopen(request, timeout=float(timeout_seconds)) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("OAuth token response must be a JSON object")
    access_token = str(payload.get("access_token", "") or "").strip()
    if not access_token:
        raise ValueError("OAuth token response omitted access_token")
    expires_in = float(payload.get("expires_in", 0) or 0)
    return MCPOAuthTokenState(
        access_token=access_token,
        refresh_token=str(
            payload.get("refresh_token", "") or fields.get("refresh_token", "")
        ).strip(),
        expires_at=(time.time() + expires_in if expires_in > 0 else 0.0),
        scope=str(payload.get("scope", "") or "").strip(),
        issuer=issuer.strip(),
    )


def mcp_token_issuer_ref(token_ref: str) -> str:
    """Return the sibling issuer marker for an MCP token reference."""

    return f"{str(token_ref or '').strip()}.issuer"


def read_issuer_bound_token(
    token_store: MCPTokenStore | None,
    token_ref: str,
    issuer: str,
) -> str:
    """Read a token only when its stored issuer matches the selected issuer."""

    if token_store is None or not token_ref:
        return ""
    token = str(token_store.get(token_ref) or "").strip()
    if not token:
        return ""
    expected = str(issuer or "").strip()
    if expected and token_store.get(mcp_token_issuer_ref(token_ref)) != expected:
        raise ValueError("stored MCP token issuer does not match selected issuer")
    return token


def store_issuer_bound_token(
    token_store: MCPTokenStore,
    token_ref: str,
    value: str,
    issuer: str,
) -> None:
    """Store an MCP token and its selected issuer marker together."""

    token_store.set(token_ref, value)
    token_store.set(mcp_token_issuer_ref(token_ref), issuer)


def store_oauth_transaction(
    token_store: MCPTokenStore,
    transaction: MCPOAuthTransaction,
) -> None:
    token_store.set(
        _oauth_transaction_ref(transaction.server_name, transaction.state),
        json.dumps(
            {
                "server_name": transaction.server_name,
                "resource": transaction.resource,
                "redirect_uri": transaction.redirect_uri,
                "state": transaction.state,
                "code_verifier": transaction.code_verifier,
                "issuer": transaction.issuer,
                "issuer_required": transaction.issuer_required,
                "authorization_endpoint": transaction.authorization_endpoint,
                "token_endpoint": transaction.token_endpoint,
                "expires_at": transaction.expires_at,
                "resource_metadata_url": transaction.resource_metadata_url,
                "scope": transaction.scope,
            },
            separators=(",", ":"),
            sort_keys=True,
        ),
    )


def consume_oauth_transaction(
    token_store: MCPTokenStore,
    *,
    server_name: str,
    state: str,
    now: float | None = None,
) -> MCPOAuthTransaction:
    ref = _oauth_transaction_ref(server_name, state)
    raw = token_store.consume(ref)
    if not raw:
        raise ValueError("OAuth transaction is unknown or already used")
    try:
        payload = json.loads(raw)
        transaction = MCPOAuthTransaction(
            server_name=str(payload["server_name"]),
            resource=str(payload["resource"]),
            redirect_uri=str(payload["redirect_uri"]),
            state=str(payload["state"]),
            code_verifier=str(payload["code_verifier"]),
            issuer=str(payload["issuer"]),
            issuer_required=bool(payload["issuer_required"]),
            authorization_endpoint=str(payload["authorization_endpoint"]),
            token_endpoint=str(payload["token_endpoint"]),
            expires_at=float(payload["expires_at"]),
            resource_metadata_url=str(payload.get("resource_metadata_url", "")),
            scope=str(payload.get("scope", "")),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("OAuth transaction is invalid") from exc
    if transaction.server_name != server_name or transaction.state != state:
        raise ValueError("OAuth transaction does not match the authorization response")
    if transaction.expires_at <= (time.time() if now is None else now):
        raise ValueError("OAuth transaction has expired")
    return transaction


def _oauth_transaction_ref(server_name: str, state: str) -> str:
    return f"oauth_transaction.{server_name.strip()}.{state.strip()}"


def _validate_authorization_issuer(
    *, expected: str, received: str, required: bool
) -> None:
    received = received.strip()
    if not received:
        if required:
            raise ValueError("OAuth authorization response omitted required issuer")
        return
    expected = expected.strip()
    if not expected or received != expected:
        raise ValueError("OAuth authorization response issuer does not match metadata")


__all__ = [
    "InMemoryMCPTokenStore",
    "MCPOAuthMetadata",
    "MCPOAuthPKCEChallenge",
    "MCPOAuthTokenState",
    "MCPOAuthTransaction",
    "MCPTokenStore",
    "SecretServiceMCPTokenStore",
    "build_runtime_mcp_token_store",
    "build_authorization_url",
    "build_pkce_challenge",
    "consume_oauth_transaction",
    "discover_oauth_metadata",
    "exchange_authorization_code",
    "refresh_oauth_access_token",
    "register_oauth_client",
    "revoke_oauth_token",
    "store_oauth_transaction",
]
