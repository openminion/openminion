from __future__ import annotations

import importlib
from http import HTTPStatus
from unittest import mock


IMPORT_SURFACE = [
    ("openminion.api.runtime", "APIRuntime"),
    ("openminion.api.turns", "run_turn"),
    ("openminion.api.turns", "TurnTimeoutError"),
    ("openminion.api.server", "build_api_server"),
    ("openminion.api.server", "dispatch_request"),
    ("openminion.api.server.app", "_OpenMinionAPIHandler"),
    ("openminion.api.responses.serialization", "error_response"),
    ("openminion.api.routes.turns", "handle_request"),
    ("openminion.api.queries.sessions", "list_session_messages"),
]


def test_api_layout_characterization_import_surface() -> None:
    for module_name, attr_name in IMPORT_SURFACE:
        module = importlib.import_module(module_name)
        assert hasattr(module, attr_name), f"{module_name} missing {attr_name}"


def test_server_dispatch_uses_same_app_module_object_for_runtime_patching() -> None:
    import openminion.api.server as server
    import openminion.api.server.app as server_app

    sentinel_runtime = object()
    sentinel_counter = object()
    sentinel_result = (HTTPStatus.OK, {"ok": True})
    original_run_turn = getattr(server_app, "run_turn", None)
    had_run_turn = hasattr(server_app, "run_turn")

    def fake_dispatch_request(*args, **kwargs):
        assert server_app.run_turn is sentinel_runtime
        assert server_app.perf_counter is sentinel_counter
        assert server_app.APIRuntime is sentinel_result
        return (HTTPStatus.OK, {"ok": True})

    with (
        mock.patch.object(server, "run_turn", sentinel_runtime),
        mock.patch.object(server, "perf_counter", sentinel_counter),
        mock.patch.object(server, "APIRuntime", sentinel_result),
        mock.patch.object(
            server_app, "dispatch_request", side_effect=fake_dispatch_request
        ),
    ):
        status, payload = server.dispatch_request(
            "GET",
            "/health",
            None,
            runtime=None,
            runtime_bootstrap_error=None,
        )

    assert status == HTTPStatus.OK
    assert payload == {"ok": True}
    if had_run_turn:
        assert getattr(server_app, "run_turn", None) is original_run_turn
    else:
        assert not hasattr(server_app, "run_turn")
