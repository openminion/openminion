from openminion.services.gateway.protocol import GatewayProtocolSession


def test_first_frame_must_be_connect() -> None:
    session = GatewayProtocolSession()
    response = session.handle_frame(
        {"type": "req", "id": "r1", "method": "turn.send", "params": {}}
    )
    assert response["type"] == "res"
    assert response["ok"] is False
    assert response["error"]["code"] == "handshake_required"
    assert session.state.connected is False


def test_connect_success() -> None:
    session = GatewayProtocolSession(
        min_protocol=1,
        max_protocol=2,
        features={"methods": ["connect", "turn.send"], "events": ["agent.update"]},
        policy_limits={"max_payload_bytes": 131072},
    )
    response = session.handle_frame(
        {
            "type": "req",
            "id": "r1",
            "method": "connect",
            "params": {
                "min_protocol": 1,
                "max_protocol": 3,
                "client": {"name": "tester"},
            },
        }
    )
    assert response["ok"] is True
    assert response["payload"]["protocol"] == 2
    assert response["payload"]["server"]["name"] == "openminion"
    assert response["payload"]["features"]["methods"][0] == "connect"
    assert response["payload"]["auth"]["role"] == "operator"
    assert response["payload"]["auth"]["scopes"] == []
    assert session.state.connected is True
    assert session.state.protocol == 2


def test_connect_protocol_mismatch() -> None:
    session = GatewayProtocolSession(min_protocol=1, max_protocol=1)
    response = session.handle_frame(
        {
            "type": "req",
            "id": "r1",
            "method": "connect",
            "params": {
                "min_protocol": 2,
                "max_protocol": 3,
                "client": {"name": "tester"},
            },
        }
    )
    assert response["ok"] is False
    assert response["error"]["code"] == "protocol_mismatch"
    assert session.state.connected is False


def test_connect_twice_returns_already_connected() -> None:
    session = GatewayProtocolSession()
    first = session.handle_frame(
        {
            "type": "req",
            "id": "r1",
            "method": "connect",
            "params": {"min_protocol": 1, "max_protocol": 1},
        }
    )
    assert first["ok"] is True

    second = session.handle_frame(
        {
            "type": "req",
            "id": "r2",
            "method": "connect",
            "params": {"min_protocol": 1, "max_protocol": 1},
        }
    )
    assert second["ok"] is False
    assert second["error"]["code"] == "already_connected"


def test_method_after_connect_not_implemented() -> None:
    session = GatewayProtocolSession()
    connected = session.handle_frame(
        {
            "type": "req",
            "id": "r1",
            "method": "connect",
            "params": {
                "min_protocol": 1,
                "max_protocol": 1,
                "client": {"role": "operator", "scopes": ["operator.write"]},
            },
        }
    )
    assert connected["ok"] is True

    response = session.handle_frame(
        {
            "type": "req",
            "id": "r2",
            "method": "turn.send",
            "params": {},
        }
    )
    assert response["ok"] is False
    assert response["error"]["code"] == "method_not_implemented"


def test_connect_rejects_invalid_client_role() -> None:
    session = GatewayProtocolSession()
    response = session.handle_frame(
        {
            "type": "req",
            "id": "r1",
            "method": "connect",
            "params": {
                "min_protocol": 1,
                "max_protocol": 1,
                "client": {"role": "viewer"},
            },
        }
    )
    assert response["ok"] is False
    assert response["error"]["code"] == "invalid_connect_client_role"
    assert session.state.connected is False


def test_turn_send_denied_when_scope_missing() -> None:
    session = GatewayProtocolSession()
    connected = session.handle_frame(
        {
            "type": "req",
            "id": "r1",
            "method": "connect",
            "params": {
                "min_protocol": 1,
                "max_protocol": 1,
                "client": {"role": "operator", "scopes": ["operator.read"]},
            },
        }
    )
    assert connected["ok"] is True

    response = session.handle_frame(
        {
            "type": "req",
            "id": "r2",
            "method": "turn.send",
            "params": {},
        }
    )
    assert response["ok"] is False
    assert response["error"]["code"] == "auth_denied"
    assert "missing_scopes" in response["error"]["details"]


def test_admin_reload_denied_for_node_role() -> None:
    session = GatewayProtocolSession()
    connected = session.handle_frame(
        {
            "type": "req",
            "id": "r1",
            "method": "connect",
            "params": {
                "min_protocol": 1,
                "max_protocol": 1,
                "client": {"role": "node", "scopes": ["operator.admin"]},
            },
        }
    )
    assert connected["ok"] is True

    response = session.handle_frame(
        {
            "type": "req",
            "id": "r2",
            "method": "admin.reload",
            "params": {},
        }
    )
    assert response["ok"] is False
    assert response["error"]["code"] == "auth_denied"


def test_invalid_request_shape_returns_structured_error() -> None:
    session = GatewayProtocolSession()
    response = session.handle_frame(
        {"type": "req", "id": "", "method": "connect", "params": {}}
    )
    assert response["ok"] is False
    assert response["error"]["code"] == "invalid_frame_field"
