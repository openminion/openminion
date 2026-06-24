import pytest
from openminion.base.protocol import (
    ErrorPayload,
    ProtocolError,
    build_error_response,
    build_success_response,
    negotiate_protocol,
    parse_connect_params,
    parse_event_frame,
    parse_frame,
    parse_request_frame,
    parse_response_frame,
)


def test_parse_request_frame() -> None:
    frame = parse_request_frame(
        {
            "type": "req",
            "id": "r1",
            "method": "connect",
            "params": {"min_protocol": 1, "max_protocol": 1},
        }
    )
    assert frame.type == "req"
    assert frame.id == "r1"
    assert frame.method == "connect"


def test_parse_frame_dispatches_event() -> None:
    frame = parse_frame(
        {"type": "event", "event": "ready", "payload": {"ok": True}, "seq": 1}
    )
    assert frame.type == "event"
    assert frame.event == "ready"
    assert frame.seq == 1


def test_parse_response_frame_with_error() -> None:
    frame = parse_response_frame(
        {
            "type": "res",
            "id": "r1",
            "ok": False,
            "payload": {},
            "error": {
                "code": "protocol_mismatch",
                "message": "No overlap",
                "details": {"server": 1},
                "retryable": False,
            },
        }
    )
    assert frame.ok is False
    assert frame.error is not None
    assert frame.error.code == "protocol_mismatch"


def test_parse_connect_params() -> None:
    params = parse_connect_params(
        {"min_protocol": 1, "max_protocol": 2, "client": {"name": "tester"}}
    )
    assert params.min_protocol == 1
    assert params.max_protocol == 2
    assert params.client["name"] == "tester"


def test_negotiate_protocol_picks_highest_compatible() -> None:
    selected = negotiate_protocol(
        client_min=1, client_max=3, server_min=1, server_max=2
    )
    assert selected == 2


def test_negotiate_protocol_mismatch_raises() -> None:
    with pytest.raises(ProtocolError) as exc_info:
        negotiate_protocol(client_min=3, client_max=4, server_min=1, server_max=2)
    assert exc_info.value.code == "protocol_mismatch"


def test_build_error_response_shape() -> None:
    response = build_error_response(
        "r1",
        ProtocolError(
            "invalid_frame", "bad frame", retryable=False, retry_after_ms=1000
        ),
    )
    payload = response.to_dict()
    assert payload["type"] == "res"
    assert payload["ok"] is False
    assert payload["error"]["code"] == "invalid_frame"
    assert payload["error"]["retry_after_ms"] == 1000


def test_build_error_response_normalizes_singular_detail_mapping() -> None:
    response = build_error_response(
        "r2",
        {
            "code": "invalid_frame",
            "message": "bad frame",
            "detail": {"field": "id"},
        },
    )
    payload = response.to_dict()
    assert payload["error"]["details"] == {"field": "id"}
    assert "detail" not in payload["error"]


def test_build_success_response_shape() -> None:
    response = build_success_response("r1", payload={"protocol": 1})
    payload = response.to_dict()
    assert payload["type"] == "res"
    assert payload["ok"] is True
    assert payload["payload"]["protocol"] == 1


def test_error_payload_to_dict_omits_none() -> None:
    payload = ErrorPayload(code="x", message="y").to_dict()
    assert payload == {"code": "x", "message": "y"}


def test_parse_event_frame_invalid_seq() -> None:
    with pytest.raises(ProtocolError) as exc_info:
        parse_event_frame({"type": "event", "event": "ready", "payload": {}, "seq": -1})
    assert exc_info.value.code == "invalid_event_seq"
