import io
from argparse import Namespace
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest import mock

from openminion.cli.commands.api import run_api


def _config(host: str = "127.0.0.1", port: int = 8080) -> SimpleNamespace:
    return SimpleNamespace(gateway=SimpleNamespace(host=host, port=port))


def test_run_api_startup_failure_returns_nonzero_and_message() -> None:
    args = Namespace(config="test-configs.json", host=None, port=None)
    mocked_load_config = mock.Mock(return_value=_config())
    mocked_build_server = mock.Mock(side_effect=OSError("Address already in use"))

    with mock.patch.dict(
        run_api.__globals__,
        {
            "load_config": mocked_load_config,
            "build_api_server": mocked_build_server,
        },
    ):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = run_api(args)

    assert code == 1
    output = buf.getvalue()
    assert "failed to start" in output
    assert "127.0.0.1:8080" in output


def test_run_api_keyboard_interrupt_stops_gracefully() -> None:
    args = Namespace(config=None, host=None, port=None)
    server = mock.Mock()
    server.server_address = ("127.0.0.1", 9090)
    server.serve_forever.side_effect = KeyboardInterrupt()
    mocked_load_config = mock.Mock(return_value=_config(port=9090))
    mocked_build_server = mock.Mock(return_value=server)

    with mock.patch.dict(
        run_api.__globals__,
        {
            "load_config": mocked_load_config,
            "build_api_server": mocked_build_server,
        },
    ):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = run_api(args)

    assert code == 0
    server.serve_forever.assert_called_once()
    server.server_close.assert_called_once()
    output = buf.getvalue()
    assert "listening on http://127.0.0.1:9090" in output
    assert "API server stopped" in output


def test_run_api_unexpected_server_failure_returns_nonzero() -> None:
    args = Namespace(config=None, host=None, port=None)
    server = mock.Mock()
    server.server_address = ("127.0.0.1", 9091)
    server.serve_forever.side_effect = RuntimeError("serve failed")
    mocked_load_config = mock.Mock(return_value=_config(port=9091))
    mocked_build_server = mock.Mock(return_value=server)

    with mock.patch.dict(
        run_api.__globals__,
        {
            "load_config": mocked_load_config,
            "build_api_server": mocked_build_server,
        },
    ):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = run_api(args)

    assert code == 1
    server.server_close.assert_called_once()
    assert "stopped unexpectedly" in buf.getvalue()
