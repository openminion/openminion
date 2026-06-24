import io
from http import HTTPStatus
from unittest import mock

from openminion.api.server import _OpenMinionAPIHandler


def _handler() -> _OpenMinionAPIHandler:
    handler = object.__new__(_OpenMinionAPIHandler)
    handler.send_response = mock.Mock()
    handler.send_header = mock.Mock()
    handler.end_headers = mock.Mock()
    handler.wfile = io.BytesIO()
    return handler


def test_write_json_sets_x_request_id_header_from_meta() -> None:
    handler = _handler()
    payload = {"ok": True, "meta": {"request_id": "req-123"}}
    _OpenMinionAPIHandler._write_json(handler, HTTPStatus.OK, payload)
    handler.send_header.assert_any_call("X-Request-ID", "req-123")


def test_write_json_skips_x_request_id_when_missing() -> None:
    handler = _handler()
    payload = {"ok": True}
    _OpenMinionAPIHandler._write_json(handler, HTTPStatus.OK, payload)
    sent_headers = [call.args[0] for call in handler.send_header.call_args_list]
    assert "X-Request-ID" not in sent_headers


def test_write_json_sets_no_store_for_metrics_path() -> None:
    handler = _handler()
    payload = {"ok": True, "meta": {"request_id": "req-123", "path": "/metrics"}}
    _OpenMinionAPIHandler._write_json(handler, HTTPStatus.OK, payload)
    handler.send_header.assert_any_call("Cache-Control", "no-store")
