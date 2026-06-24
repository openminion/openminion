"""MCP OAuth authorization helpers."""

import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any, Protocol
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from openminion.base.config.mcp import MCPAuthorizationConfig


class MCPTokenStore(Protocol):
    """Synchronous token-state boundary used by MCP transports."""

    def get(self, ref: str) -> str: ...

    def set(self, ref: str, value: str) -> None: ...


@dataclass
class InMemoryMCPTokenStore:
    """Test/default token store that keeps secrets out of config exports."""

    values: dict[str, str]

    def get(self, ref: str) -> str:
        return str(self.values.get(ref, "") or "")

    def set(self, ref: str, value: str) -> None:
        self.values[str(ref or "").strip()] = str(value or "")


@dataclass(frozen=True)
class MCPOAuthMetadata:
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str = ""
    revocation_endpoint: str = ""
    issuer: str = ""


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

    @property
    def expired(self) -> bool:
        return bool(self.expires_at) and time.time() >= self.expires_at


def discover_oauth_metadata(
    config: MCPAuthorizationConfig,
    *,
    timeout_seconds: float = 10.0,
) -> MCPOAuthMetadata:
    """Resolve OAuth server metadata from config or metadata URL."""

    if config.authorization_endpoint and config.token_endpoint:
        return MCPOAuthMetadata(
            authorization_endpoint=config.authorization_endpoint,
            token_endpoint=config.token_endpoint,
            registration_endpoint=config.registration_endpoint,
            revocation_endpoint=config.revocation_endpoint,
        )
    if not config.authorization_server_metadata_url:
        raise ValueError("oauth_pkce requires OAuth metadata or explicit endpoints")
    request = urllib_request.Request(
        config.authorization_server_metadata_url,
        headers={"Accept": "application/json"},
        method="GET",
    )
    with urllib_request.urlopen(request, timeout=float(timeout_seconds)) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("OAuth metadata response must be a JSON object")
    authorization_endpoint = str(
        payload.get("authorization_endpoint", "") or ""
    ).strip()
    token_endpoint = str(payload.get("token_endpoint", "") or "").strip()
    if not authorization_endpoint or not token_endpoint:
        raise ValueError(
            "OAuth metadata must include authorization_endpoint and token_endpoint"
        )
    return MCPOAuthMetadata(
        authorization_endpoint=authorization_endpoint,
        token_endpoint=token_endpoint,
        registration_endpoint=str(
            payload.get("registration_endpoint", "") or ""
        ).strip(),
        revocation_endpoint=str(payload.get("revocation_endpoint", "") or "").strip(),
        issuer=str(payload.get("issuer", "") or "").strip(),
    )


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
) -> str:
    query = {
        "response_type": "code",
        "client_id": config.client_id,
        "redirect_uri": config.redirect_uri,
        "code_challenge": challenge.code_challenge,
        "code_challenge_method": challenge.method,
        "state": state,
    }
    if config.scope:
        query["scope"] = config.scope
    return f"{metadata.authorization_endpoint}?{urllib_parse.urlencode(query)}"


def exchange_authorization_code(
    *,
    config: MCPAuthorizationConfig,
    metadata: MCPOAuthMetadata,
    code: str,
    challenge: MCPOAuthPKCEChallenge,
    timeout_seconds: float = 10.0,
) -> MCPOAuthTokenState:
    return _request_token(
        metadata.token_endpoint,
        {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": config.client_id,
            "redirect_uri": config.redirect_uri,
            "code_verifier": challenge.code_verifier,
        },
        timeout_seconds=timeout_seconds,
    )


def refresh_oauth_access_token(
    *,
    config: MCPAuthorizationConfig,
    metadata: MCPOAuthMetadata,
    refresh_token: str,
    timeout_seconds: float = 10.0,
) -> MCPOAuthTokenState:
    return _request_token(
        metadata.token_endpoint,
        {
            "grant_type": "refresh_token",
            "client_id": config.client_id,
            "refresh_token": refresh_token,
        },
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
            "client_name": str(client_name or "openminion").strip() or "openminion",
            "redirect_uris": [
                str(item).strip() for item in redirect_uris if str(item).strip()
            ],
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
    )


__all__ = [
    "InMemoryMCPTokenStore",
    "MCPOAuthMetadata",
    "MCPOAuthPKCEChallenge",
    "MCPOAuthTokenState",
    "MCPTokenStore",
    "build_authorization_url",
    "build_pkce_challenge",
    "discover_oauth_metadata",
    "exchange_authorization_code",
    "refresh_oauth_access_token",
    "register_oauth_client",
    "revoke_oauth_token",
]
