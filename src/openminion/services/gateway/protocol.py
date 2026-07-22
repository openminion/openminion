from dataclasses import dataclass, field
from typing import Any, Optional, cast
from collections.abc import Mapping

from openminion import __version__
from openminion.base.protocol import (
    ProtocolError,
    build_error_response,
    build_success_response,
    negotiate_protocol,
    parse_connect_params,
    parse_request_frame,
)
from openminion.services.gateway.authz import (
    MethodAuthorizationRule,
    authorize_method,
    default_method_authorization_rules,
)


@dataclass(frozen=True)
class HandshakeState:
    connected: bool = False
    protocol: Optional[int] = None
    client: dict[str, Any] = field(default_factory=dict)
    role: str = "operator"
    scopes: tuple[str, ...] = ()


class GatewayProtocolSession:
    def __init__(
        self,
        *,
        server_name: str = "openminion",
        server_version: str = __version__,
        min_protocol: int = 1,
        max_protocol: int = 1,
        features: Optional[dict[str, Any]] = None,
        policy_limits: Optional[dict[str, Any]] = None,
        method_authz: Optional[Mapping[str, MethodAuthorizationRule]] = None,
    ) -> None:
        self._server_name = server_name
        self._server_version = server_version
        self._min_protocol = min_protocol
        self._max_protocol = max_protocol
        self._features = dict(features or {"methods": ["connect"], "events": []})
        self._policy_limits = dict(policy_limits or {"max_payload_bytes": 65536})
        self._method_authz = dict(method_authz or default_method_authorization_rules())
        self._state = HandshakeState()

    @property
    def state(self) -> HandshakeState:
        return self._state

    def handle_frame(self, frame_raw: Mapping[str, Any]) -> dict[str, Any]:
        request_id = _extract_request_id(frame_raw)
        try:
            request = parse_request_frame(frame_raw)
        except ProtocolError as exc:
            return _response_dict(build_error_response(request_id, exc))

        if not self._state.connected:
            if request.method != "connect":
                return _response_dict(
                    build_error_response(
                        request.id,
                        ProtocolError(
                            "handshake_required",
                            "First request must use method 'connect'",
                            retryable=False,
                        ),
                    )
                )
            return self._handle_connect(request.id, request.params)

        if request.method == "connect":
            return _response_dict(
                build_error_response(
                    request.id,
                    ProtocolError(
                        "already_connected",
                        "Protocol session is already connected",
                        retryable=False,
                    ),
                )
            )

        auth_error = authorize_method(
            method=request.method,
            role=self._state.role,
            scopes=self._state.scopes,
            rules=self._method_authz,
        )
        if auth_error is not None:
            return _response_dict(build_error_response(request.id, auth_error))

        return _response_dict(
            build_error_response(
                request.id,
                ProtocolError(
                    "method_not_implemented",
                    f"Method '{request.method}' is not implemented",
                    retryable=False,
                ),
            )
        )

    def _handle_connect(
        self, request_id: str, params_raw: Mapping[str, Any]
    ) -> dict[str, Any]:
        try:
            params = parse_connect_params(params_raw)
            selected_protocol = negotiate_protocol(
                client_min=params.min_protocol,
                client_max=params.max_protocol,
                server_min=self._min_protocol,
                server_max=self._max_protocol,
            )
            role, scopes = _parse_client_role_and_scopes(params.client)
        except ProtocolError as exc:
            return _response_dict(build_error_response(request_id, exc))

        self._state = HandshakeState(
            connected=True,
            protocol=selected_protocol,
            client=params.client,
            role=role,
            scopes=scopes,
        )
        return _response_dict(
            build_success_response(
                request_id,
                payload={
                    "protocol": selected_protocol,
                    "server": {
                        "name": self._server_name,
                        "version": self._server_version,
                        "min_protocol": self._min_protocol,
                        "max_protocol": self._max_protocol,
                    },
                    "features": self._features,
                    "policy_limits": self._policy_limits,
                    "auth": {"role": role, "scopes": list(scopes)},
                },
            )
        )


def _response_dict(response: Any) -> dict[str, Any]:
    return cast(dict[str, Any], response.to_dict())


def _extract_request_id(frame_raw: Mapping[str, Any]) -> str:
    request_id = frame_raw.get("id")
    if isinstance(request_id, str) and request_id.strip():
        return request_id.strip()
    return "unknown"


def _parse_client_role_and_scopes(
    client: Mapping[str, Any],
) -> tuple[str, tuple[str, ...]]:
    role_raw = client.get("role", "operator")
    role = str(role_raw).strip().lower() or "operator"
    if role not in {"operator", "node"}:
        raise ProtocolError(
            "invalid_connect_client_role",
            "connect client role must be one of operator|node",
            details={"role": role_raw},
            retryable=False,
        )

    scopes_raw = client.get("scopes", [])
    if scopes_raw is None:
        scopes_raw = []
    if not isinstance(scopes_raw, (list, tuple)):
        raise ProtocolError(
            "invalid_connect_client_scopes",
            "connect client scopes must be an array of strings",
            details={"scopes": scopes_raw},
            retryable=False,
        )

    normalized_scopes: list[str] = []
    for scope in scopes_raw:
        if not isinstance(scope, str):
            raise ProtocolError(
                "invalid_connect_client_scopes",
                "connect client scopes must be an array of strings",
                details={"scopes": scopes_raw},
                retryable=False,
            )
        normalized = scope.strip().lower()
        if normalized:
            normalized_scopes.append(normalized)

    return role, tuple(sorted(set(normalized_scopes)))
