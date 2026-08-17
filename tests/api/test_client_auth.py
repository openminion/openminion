from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from openminion.api.server.client_auth import (
    ClientAuthError,
    ClientAuthService,
    build_config_id,
)


def _service(tmp_path: Path, *, token: str = "master-token") -> ClientAuthService:
    return ClientAuthService(
        master_token=token,
        config_path=tmp_path / "config.json",
        home_root=tmp_path / "home",
        data_root=tmp_path / "data",
        bind_host="127.0.0.1",
        daemon_version="0.0.9",
    )


def _mint(service: ClientAuthService) -> dict[str, object]:
    return service.mint(protocol_min=1, protocol_max=1, ttl_seconds=60)


def test_config_id_binds_config_home_and_data_roots(tmp_path: Path) -> None:
    base = build_config_id(tmp_path / "config", tmp_path / "home", tmp_path / "data")
    assert base != build_config_id(tmp_path / "other", tmp_path / "home", tmp_path / "data")
    assert base != build_config_id(tmp_path / "config", tmp_path / "other", tmp_path / "data")
    assert base != build_config_id(tmp_path / "config", tmp_path / "home", tmp_path / "other")


def test_master_mints_scoped_client_and_revoke_fails_closed(tmp_path: Path) -> None:
    service = _service(tmp_path)
    assert service.authorize(
        method="POST",
        path="/v1/client/leases",
        master_tokens=("master-token",),
        client_tokens=(),
        peer_host="127.0.0.1",
    ) is None
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
    service.revoke(identity)
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
            path="/v1/turn/stream",
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


def test_blank_master_preserves_legacy_but_blocks_desktop_mint(tmp_path: Path) -> None:
    service = _service(tmp_path, token="")
    assert service.authorize(
        method="GET",
        path="/v1/health",
        master_tokens=(),
        client_tokens=(),
        peer_host="127.0.0.1",
    ) is None
    with pytest.raises(ClientAuthError) as caught:
        service.authorize(
            method="POST",
            path="/v1/client/leases",
            master_tokens=(),
            client_tokens=(),
            peer_host="127.0.0.1",
        )
    assert caught.value.code == "desktop_ipc_token_required"

