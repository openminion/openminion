from __future__ import annotations

import json
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from tests._csc_fixtures import _csc_install_default_agent

from openminion.api.server import build_api_server
from openminion.api.server.client_auth import ClientAuthService
from openminion.api.server.dispatch import dispatch_request
from openminion.base.config import OpenMinionConfig, save_config
from openminion.base.version import OPENMINION_VERSION


def _service(tmp_path: Path) -> ClientAuthService:
    return ClientAuthService(
        master_token="master-token",
        config_path=tmp_path / "config.json",
        home_root=tmp_path / "home",
        data_root=tmp_path / "data",
        bind_host="127.0.0.1",
        daemon_version=OPENMINION_VERSION,
    )


def _mint_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "schema_version": 1,
        "client": {
            "kind": "desktop",
            "version": "0.0.0",
            "protocol_min": 1,
            "protocol_max": 1,
        },
        "requested_ttl_seconds": 60,
    }
    body.update(overrides)
    return body


def test_mint_uses_canonical_response_envelope_and_scoped_routes(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    status, payload = dispatch_request(
        "POST",
        "/v1/client/leases",
        None,
        body=_mint_body(),
        client_auth=service,
        request_id="request-1",
    )
    assert status == 200
    assert set(payload) == {"ok", "lease", "meta"}
    assert set(payload["meta"]) == {"request_id", "method", "path"}
    assert payload["meta"] == {
        "request_id": "request-1",
        "method": "POST",
        "path": "/v1/client/leases",
    }
    lease = payload["lease"]
    assert lease["config_id"] == service.config_id
    assert "client_token" in lease
    identity = service.authorize(
        method="GET",
        path="/v1/client/capabilities",
        master_tokens=(),
        client_tokens=(lease["client_token"],),
        peer_host="127.0.0.1",
    )
    cap_status, capabilities = dispatch_request(
        "GET",
        "/v1/client/capabilities",
        None,
        client_auth=service,
        client_identity=identity,
    )
    assert cap_status == 200
    assert capabilities["protocol"] == 1
    assert capabilities["config_id"] == service.config_id


def test_mint_rejects_unknown_fields_protocol_and_ttl(tmp_path: Path) -> None:
    service = _service(tmp_path)
    cases = [
        _mint_body(extra=True),
        _mint_body(
            client={
                "kind": "desktop",
                "version": "0.0.0",
                "protocol_min": 2,
                "protocol_max": 2,
            }
        ),
        _mint_body(requested_ttl_seconds=59),
    ]
    for body in cases:
        status, payload = dispatch_request(
            "POST",
            "/v1/client/leases",
            None,
            body=body,
            client_auth=service,
        )
        assert status == 400
        assert payload["error"]["code"] in {"invalid_request", "unsupported_protocol"}


def test_renew_and_revoke_apply_only_to_authenticated_identity(tmp_path: Path) -> None:
    service = _service(tmp_path)
    lease = service.mint(protocol_min=1, protocol_max=1, ttl_seconds=60)
    identity = service.authorize(
        method="POST",
        path="/v1/client/leases/renew",
        master_tokens=(),
        client_tokens=(str(lease["client_token"]),),
        peer_host="127.0.0.1",
    )
    renew_status, renewed = dispatch_request(
        "POST",
        "/v1/client/leases/renew",
        None,
        body={},
        client_auth=service,
        client_identity=identity,
    )
    assert renew_status == 200
    assert "client_token" not in renewed["lease"]
    revoke_status, revoked = dispatch_request(
        "DELETE",
        "/v1/client/leases/current",
        None,
        body={},
        client_auth=service,
        client_identity=identity,
    )
    assert revoke_status == 200
    assert revoked["revoked"] is True


def test_http_server_enforces_master_and_client_tokens_before_dispatch(
    tmp_path: Path,
) -> None:
    home_root = tmp_path / "home"
    data_root = home_root / "data"
    config_path = home_root / "config.json"
    config = OpenMinionConfig()
    _csc_install_default_agent(config, provider="echo")  # type: ignore[attr-defined]
    config.runtime.ipc_token = "master-token"
    config.storage.path = str(data_root / "state" / "api.db")
    home_root.mkdir(parents=True)
    save_config(config, str(config_path))
    server = build_api_server(
        str(config_path),
        "127.0.0.1",
        0,
        home_root=home_root,
        data_root=data_root,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        status, denied = _http_json(Request(f"{base_url}/v1/health"))
        assert status == 403
        assert denied["error"]["code"] == "forbidden"
        status, health = _http_json(
            Request(
                f"{base_url}/v1/health",
                headers={"X-IPC-Token": "master-token"},
            )
        )
        assert status == 200
        assert health["ok"] is True
        status, minted = _http_json(
            Request(
                f"{base_url}/v1/client/leases",
                method="POST",
                headers={
                    "X-IPC-Token": "master-token",
                    "Content-Type": "application/json",
                },
                data=json.dumps(_mint_body()).encode("utf-8"),
            )
        )
        assert status == 200
        status, capabilities = _http_json(
            Request(
                f"{base_url}/v1/client/capabilities",
                headers={"X-OpenMinion-Client-Token": minted["lease"]["client_token"]},
            )
        )
        assert status == 200
        assert capabilities["config_id"] == minted["lease"]["config_id"]
        status, denied_header = _http_json(
            Request(
                f"{base_url}/v1/client/capabilities",
                headers={
                    "X-OpenMinion-Client-Token": minted["lease"]["client_token"],
                    "X-Not-Admitted": "value",
                },
            )
        )
        assert status == 403
        assert denied_header["error"]["code"] == "forbidden"
        status, query_denied = _http_json(
            Request(
                f"{base_url}/v1/client/leases?unknown=1",
                method="POST",
                headers={
                    "X-IPC-Token": "master-token",
                    "Content-Type": "application/json",
                },
                data=json.dumps(_mint_body()).encode("utf-8"),
            )
        )
        assert status == 400
        assert query_denied["error"]["code"] == "invalid_request"
        for request in (
            Request(
                f"{base_url}/v1/client/capabilities",
                headers={
                    "X-OpenMinion-Client-Token": minted["lease"]["client_token"],
                    "Content-Length": "2",
                },
                data=b"{}",
                method="GET",
            ),
            Request(
                f"{base_url}/v1/client/leases",
                method="POST",
                headers={"X-IPC-Token": "master-token", "Content-Type": "text/plain"},
                data=b"{}",
            ),
            Request(
                f"{base_url}/v1/client/leases",
                method="POST",
                headers={
                    "X-IPC-Token": "master-token",
                    "Content-Type": "application/json",
                },
                data=b"{bad",
            ),
        ):
            status, rejected = _http_json(request)
            assert status == 400
            assert rejected["error"]["code"] in {"invalid_request", "invalid_json"}
        oversized = Request(
            f"{base_url}/v1/client/leases",
            method="POST",
            headers={
                "X-IPC-Token": "master-token",
                "Content-Type": "application/json",
            },
            data=b"x" * (16 * 1024 + 1),
        )
        assert _http_status(oversized) == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _http_json(request: Request) -> tuple[int, dict[str, object]]:
    try:
        with urlopen(request, timeout=5) as response:  # noqa: S310
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _http_status(request: Request) -> int:
    try:
        with urlopen(request, timeout=5) as response:  # noqa: S310
            return response.status
    except HTTPError as exc:
        return exc.code
