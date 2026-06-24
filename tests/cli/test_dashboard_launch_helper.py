from __future__ import annotations

from unittest.mock import patch

from openminion.cli.commands.tui import launch_dashboard


class _StubApp:
    instances: list["_StubApp"] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.ran = False
        _StubApp.instances.append(self)

    def run(self) -> int:
        self.ran = True
        return 0


def test_owns_runtime_true_fires_close_after_run() -> None:
    _StubApp.instances.clear()
    closed = {"called": False}

    def _close():
        closed["called"] = True

    with patch("openminion.cli.tui.app.OpenMinionApp", _StubApp):
        rc = launch_dashboard(
            app_runtime=object(),
            providers=object(),
            owns_runtime=True,
            close_runtime=_close,
        )
    assert rc == 0
    assert _StubApp.instances and _StubApp.instances[0].ran is True
    assert closed["called"] is True


def test_owns_runtime_false_does_not_fire_close() -> None:
    _StubApp.instances.clear()
    closed = {"called": False}

    def _close():
        closed["called"] = True

    with patch("openminion.cli.tui.app.OpenMinionApp", _StubApp):
        rc = launch_dashboard(
            app_runtime=object(),
            providers=object(),
            owns_runtime=False,
            close_runtime=_close,  # provided but should NOT be called
        )
    assert rc == 0
    assert closed["called"] is False, (
        "owns_runtime=False MUST NOT close the borrowed runtime"
    )


def test_owns_runtime_true_with_none_close_does_not_crash() -> None:
    _StubApp.instances.clear()
    with patch("openminion.cli.tui.app.OpenMinionApp", _StubApp):
        rc = launch_dashboard(
            app_runtime=object(),
            providers=object(),
            owns_runtime=True,
            close_runtime=None,  # demo path
        )
    assert rc == 0


def test_app_runtime_none_omits_runtime_kwarg() -> None:
    _StubApp.instances.clear()
    with patch("openminion.cli.tui.app.OpenMinionApp", _StubApp):
        launch_dashboard(
            app_runtime=None,
            providers=object(),
            no_picker=True,
            owns_runtime=True,
        )
    assert _StubApp.instances
    assert "runtime" not in _StubApp.instances[0].kwargs


def test_close_runtime_exception_is_swallowed(capsys) -> None:
    _StubApp.instances.clear()

    def _bad_close():
        raise RuntimeError("close failed")

    with patch("openminion.cli.tui.app.OpenMinionApp", _StubApp):
        rc = launch_dashboard(
            app_runtime=object(),
            providers=object(),
            owns_runtime=True,
            close_runtime=_bad_close,
        )
    assert rc == 0
    captured = capsys.readouterr()
    assert "close_runtime() raised" in captured.err


def test_helper_passes_optional_kwargs_through() -> None:
    _StubApp.instances.clear()
    sentinel_theme = object()
    sentinel_providers = object()
    sentinel_runtime = object()
    with patch("openminion.cli.tui.app.OpenMinionApp", _StubApp):
        launch_dashboard(
            app_runtime=sentinel_runtime,
            providers=sentinel_providers,
            no_picker=True,
            initial_tab="tab-agents",
            theme=sentinel_theme,
            owns_runtime=True,
        )
    kwargs = _StubApp.instances[0].kwargs
    assert kwargs["runtime"] is sentinel_runtime
    assert kwargs["providers"] is sentinel_providers
    assert kwargs["no_picker"] is True
    assert kwargs["initial_tab"] == "tab-agents"
    assert kwargs["theme"] is sentinel_theme
