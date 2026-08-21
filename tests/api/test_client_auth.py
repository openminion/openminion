from __future__ import annotations

from datetime import UTC, datetime, timedelta
import io
from pathlib import Path

import pytest

from openminion.api.server.client_auth import (
    ClientAuthError,
    ClientAuthHTTPMixin,
    ClientAuthService,
    build_config_id,
)
from openminion.base.version import OPENMINION_VERSION
from http import HTTPStatus


def _service(tmp_path: Path, *, token: str = "master-token") -> ClientAuthService:
    return ClientAuthService(
        master_token=token,
        config_path=tmp_path / "config.json",
        home_root=tmp_path / "home",
        data_root=tmp_path / "data",
        bind_host="127.0.0.1",
        daemon_version=OPENMINION_VERSION,
    )


def _mint(service: ClientAuthService) -> dict[str, object]:
    return service.mint(protocol_min=1, protocol_max=1, ttl_seconds=60)


def test_config_id_binds_config_home_and_data_roots(tmp_path: Path) -> None:
    base = build_config_id(tmp_path / "config", tmp_path / "home", tmp_path / "data")
    assert base != build_config_id(
        tmp_path / "other", tmp_path / "home", tmp_path / "data"
    )
    assert base != build_config_id(
        tmp_path / "config", tmp_path / "other", tmp_path / "data"
    )
    assert base != build_config_id(
        tmp_path / "config", tmp_path / "home", tmp_path / "other"
    )


def test_master_mints_scoped_client_and_revoke_fails_closed(tmp_path: Path) -> None:
    service = _service(tmp_path)
    assert (
        service.authorize(
            method="POST",
            path="/v1/client/leases",
            master_tokens=("master-token",),
            client_tokens=(),
            peer_host="127.0.0.1",
        )
        is None
    )
    lease = _mint(service)
    identity = service.authorize(
        method="GET",
        path="/v1/client/capabilities",
        master_tokens=(),
        client_tokens=(str(lease["client_token"]),),
        peer_host="::1",
    )
    assert identity is not None
    assert identity.config_id == service.config_id
    assert service.is_active(identity) is True
    service.revoke(identity)
    assert service.is_active(identity) is False
    with pytest.raises(ClientAuthError, match="not authorized"):
        service.authorize(
            method="GET",
            path="/v1/health",
            master_tokens=(),
            client_tokens=(str(lease["client_token"]),),
            peer_host="127.0.0.1",
        )


@pytest.mark.parametrize(
    ("masters", "clients", "peer"),
    [
        ((), (), "127.0.0.1"),
        (("wrong",), (), "127.0.0.1"),
        (("master-token", "master-token"), (), "127.0.0.1"),
        (("master-token",), ("client",), "127.0.0.1"),
        (("master-token",), (), "192.0.2.10"),
    ],
)
def test_master_admission_negatives_fail_with_generic_forbidden(
    tmp_path: Path,
    masters: tuple[str, ...],
    clients: tuple[str, ...],
    peer: str,
) -> None:
    with pytest.raises(ClientAuthError) as caught:
        _service(tmp_path).authorize(
            method="GET",
            path="/v1/health",
            master_tokens=masters,
            client_tokens=clients,
            peer_host=peer,
        )
    assert caught.value.code == "forbidden"


def test_client_wrong_route_and_expired_lease_fail_closed(tmp_path: Path) -> None:
    service = _service(tmp_path)
    lease = _mint(service)
    token = str(lease["client_token"])
    with pytest.raises(ClientAuthError) as wrong_route:
        service.authorize(
            method="POST",
            path="/v1/admin/kill",
            master_tokens=(),
            client_tokens=(token,),
            peer_host="127.0.0.1",
        )
    assert wrong_route.value.code == "forbidden"
    record = next(iter(service._leases.values()))
    record.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    with pytest.raises(ClientAuthError) as expired:
        service.authorize(
            method="GET",
            path="/v1/health",
            master_tokens=(),
            client_tokens=(token,),
            peer_host="127.0.0.1",
        )
    assert expired.value.code == "forbidden"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/v1/client/sessions"),
        ("POST", "/v1/client/sessions"),
        ("GET", "/v1/client/sessions/session-1"),
        ("DELETE", "/v1/client/sessions/session-1"),
        ("GET", "/v1/client/sessions/session-1/events"),
        ("POST", "/v1/turn/stream"),
        ("POST", "/v1/turn/trace-1/cancel"),
        (
            "POST",
            "/v1/client/sessions/session-1/turns/trace-1/approvals/approval-1",
        ),
        ("POST", "/v1/client/sessions/session-1/media"),
        ("GET", "/v1/client/sessions/session-1/media/" + "a" * 48),
        ("DELETE", "/v1/client/sessions/session-1/media/" + "a" * 48),
        ("GET", "/v1/client/sessions/session-1/artifacts"),
        ("GET", "/v1/client/sessions/session-1/artifacts/" + "a" * 48),
        (
            "POST",
            "/v1/client/sessions/session-1/artifacts/" + "a" * 48 + "/detach",
        ),
        (
            "POST",
            "/v1/client/sessions/session-1/artifacts/" + "a" * 48 + "/restore",
        ),
    ],
)
def test_client_session_and_turn_routes_are_capability_admitted(
    tmp_path: Path,
    method: str,
    path: str,
) -> None:
    service = _service(tmp_path)
    lease = _mint(service)
    identity = service.authorize(
        method=method,
        path=path,
        master_tokens=(),
        client_tokens=(str(lease["client_token"]),),
        peer_host="127.0.0.1",
    )
    assert identity is not None
    assert {
        "sessions.list",
        "sessions.create",
        "sessions.load",
        "sessions.close",
        "sessions.events",
        "turns.submit",
        "turns.cancel",
        "turns.tool_progress",
        "approvals.decide",
        "media.upload",
        "media.read",
        "media.release",
        "artifacts.catalog.v1",
        "artifacts.content.v1",
        "artifacts.detach_restore.v1",
    }.issubset(identity.capabilities)


def test_blank_master_preserves_legacy_but_blocks_desktop_mint(tmp_path: Path) -> None:
    service = _service(tmp_path, token="")
    assert (
        service.authorize(
            method="GET",
            path="/v1/health",
            master_tokens=(),
            client_tokens=(),
            peer_host="127.0.0.1",
        )
        is None
    )
    with pytest.raises(ClientAuthError) as caught:
        service.authorize(
            method="POST",
            path="/v1/client/leases",
            master_tokens=(),
            client_tokens=(),
            peer_host="127.0.0.1",
        )
    assert caught.value.code == "desktop_ipc_token_required"


def test_duplicate_client_wrong_method_and_nonloopback_bind_fail(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    lease = _mint(service)
    token = str(lease["client_token"])
    for clients, method, path in (
        ((token, token), "GET", "/v1/health"),
        ((token,), "POST", "/v1/health"),
    ):
        with pytest.raises(ClientAuthError) as caught:
            service.authorize(
                method=method,
                path=path,
                master_tokens=(),
                client_tokens=clients,
                peer_host="127.0.0.1",
            )
        assert caught.value.code == "forbidden"
    nonloopback = ClientAuthService(
        master_token="master-token",
        config_path=tmp_path / "config",
        home_root=tmp_path,
        data_root=tmp_path / "data",
        bind_host="0.0.0.0",
        daemon_version=OPENMINION_VERSION,
    )
    with pytest.raises(ClientAuthError) as caught:
        nonloopback.authorize(
            method="GET",
            path="/v1/health",
            master_tokens=("master-token",),
            client_tokens=(),
            peer_host="127.0.0.1",
        )
    assert caught.value.code == "forbidden"


def test_authenticated_response_is_replaced_when_it_exceeds_bound() -> None:
    adapter = ClientAuthHTTPMixin()
    adapter.client_response_limited = True
    status, encoded = adapter._bounded_json_response(
        HTTPStatus.OK,
        {"ok": True, "value": "x" * (64 * 1024)},
    )
    assert status == HTTPStatus.BAD_GATEWAY
    assert len(encoded) < 64 * 1024
    assert b'"code":"response_too_large"' in encoded


@pytest.mark.parametrize(
    ("path", "limit"),
    [
        ("/v1/client/sessions", 32 * 1024),
        ("/v1/client/sessions/session-1", 8 * 1024),
        ("/v1/turn/stream", 256 * 1024),
        ("/v1/turn/trace-1/cancel", 16 * 1024),
        ("/v1/client/sessions/session-1/artifacts/opaque/detach", 8 * 1024),
    ],
)
def test_authenticated_route_body_limits_fail_before_read(
    path: str,
    limit: int,
) -> None:
    adapter = ClientAuthHTTPMixin()
    adapter.client_body_limited = True
    adapter.headers = {
        "Content-Length": str(limit + 1),
        "Content-Type": "application/json",
    }
    adapter.rfile = io.BytesIO(b"")
    with pytest.raises(ValueError, match="exceeds"):
        adapter._read_optional_json_body(path=path)
