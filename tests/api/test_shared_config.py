from __future__ import annotations

from pathlib import Path
from unittest import mock

from openminion.api.config import (
    APIRuntimeBootstrap,
    bootstrap_api_runtime,
    build_api_handler_class,
    close_api_runtime_if_owned,
    resolve_api_runtime,
)
from openminion.api.constants import (
    API_METRICS_TOKEN_HEADER,
)
from openminion.api.core.deps import (
    resolve_api_config_display_path,
    resolve_api_config_hint,
)
from openminion.api.server.app import _OpenMinionAPIHandler


def test_api_shared_constants_contract() -> None:
    assert API_METRICS_TOKEN_HEADER == "X-Metrics-Token"
    assert resolve_api_config_hint(None) == "~/.openminion/agents.json"
    from openminion.api.operations.tools import (
        _API_TOOLS_DEFAULT_CHANNEL,
        _API_TOOLS_DEFAULT_TARGET,
        _API_TOOLS_DEFAULT_SESSION_ID,
    )  # noqa: PLC0415

    assert _API_TOOLS_DEFAULT_CHANNEL == "console"
    assert _API_TOOLS_DEFAULT_TARGET == "api-user"
    assert _API_TOOLS_DEFAULT_SESSION_ID == "tools"


def test_bootstrap_api_runtime_returns_runtime_and_error() -> None:
    runtime = mock.Mock()
    with mock.patch(
        "openminion.api.config.APIRuntime.from_config_path",
        side_effect=[runtime, RuntimeError("boom")],
    ):
        first = bootstrap_api_runtime("config.json")
        second = bootstrap_api_runtime("config.json")

    assert first == APIRuntimeBootstrap(runtime=runtime, runtime_bootstrap_error=None)
    assert second.runtime is None
    assert second.runtime_bootstrap_error == "boom"


def test_bootstrap_preserves_ipc_token_when_runtime_startup_fails() -> None:
    config = mock.Mock()
    config.runtime.ipc_token = "degraded-secret"
    manager = mock.Mock(base_config=config)
    with (
        mock.patch(
            "openminion.api.config.APIRuntime.from_config_path",
            side_effect=RuntimeError("boom"),
        ),
        mock.patch(
            "openminion.api.config.ConfigManager.load",
            return_value=manager,
        ),
    ):
        bootstrap = bootstrap_api_runtime("config.json")

    assert bootstrap.runtime is None
    assert bootstrap.runtime_bootstrap_error == "boom"
    assert bootstrap.ipc_token == "degraded-secret"


def test_build_api_handler_class_attaches_runtime_state() -> None:
    runtime = mock.Mock()
    runtime.config.runtime.ipc_token = "local-secret"
    handler_cls = build_api_handler_class(
        _OpenMinionAPIHandler,
        config_path="config.json",
        bootstrap=APIRuntimeBootstrap(
            runtime=runtime,
            runtime_bootstrap_error="boom",
        ),
    )

    assert issubclass(handler_cls, _OpenMinionAPIHandler)
    assert handler_cls.config_path == "config.json"
    assert handler_cls.runtime is runtime
    assert handler_cls.runtime_bootstrap_error == "boom"
    assert handler_cls.ipc_token == "local-secret"


def test_resolve_and_close_api_runtime_if_owned() -> None:
    explicit_runtime = mock.Mock()
    active_runtime, own_runtime = resolve_api_runtime(
        config_path="config.json",
        runtime=explicit_runtime,
    )
    assert active_runtime is explicit_runtime
    assert own_runtime is False

    created_runtime = mock.Mock()
    with mock.patch(
        "openminion.api.config.APIRuntime.from_config_path",
        return_value=created_runtime,
    ) as factory:
        active_runtime, own_runtime = resolve_api_runtime(
            config_path="config.json",
            runtime=None,
        )
    factory.assert_called_once_with("config.json")
    assert active_runtime is created_runtime
    assert own_runtime is True

    close_api_runtime_if_owned(created_runtime, own_runtime=True)
    created_runtime.close.assert_called_once_with()
    close_api_runtime_if_owned(explicit_runtime, own_runtime=False)
    explicit_runtime.close.assert_not_called()


def test_resolve_api_config_display_path_and_hint() -> None:
    assert resolve_api_config_hint(None) == "~/.openminion/agents.json"
    assert resolve_api_config_hint("config.json") == "config.json"

    display_path = resolve_api_config_display_path("config.json")
    assert display_path
    assert display_path.endswith("config.json")

    runtime_path = resolve_api_config_display_path(Path("config.json"))
    assert runtime_path.endswith("config.json")
