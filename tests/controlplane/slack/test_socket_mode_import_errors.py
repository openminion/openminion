"""Slack Socket Mode distinguishes an absent extra from an import-time fault."""

from __future__ import annotations

import sys
from types import ModuleType

import pytest

from openminion.modules.controlplane.channels.slack.adapter import (
    MissingSlackSocketDependency,
    SlackSdkSocketClient,
)


class _RaisingFinder:
    """Intercept a Slack SDK import and raise the fault under test."""

    def __init__(self, error: BaseException, *, submodules_only: bool) -> None:
        self.error = error
        self.submodules_only = submodules_only

    def find_spec(
        self, fullname: str, path: object = None, target: object = None
    ) -> object:
        if self.submodules_only:
            if fullname.startswith("slack_sdk."):
                raise self.error
            return None
        if fullname == "slack_sdk" or fullname.startswith("slack_sdk."):
            raise self.error
        return None


def _build(
    monkeypatch: pytest.MonkeyPatch,
    error: BaseException,
    *,
    submodules_only: bool = False,
) -> None:
    finder = _RaisingFinder(error, submodules_only=submodules_only)
    monkeypatch.setattr(sys, "meta_path", [finder, *sys.meta_path])
    if submodules_only:
        # The installed-SDK case: the top-level package resolves, a submodule faults.
        monkeypatch.setitem(sys.modules, "slack_sdk", ModuleType("slack_sdk"))
    SlackSdkSocketClient(app_token="xapp-1", bot_token="xoxb-1")


def test_a_genuinely_missing_sdk_keeps_the_install_guidance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(MissingSlackSocketDependency) as excinfo:
        _build(monkeypatch, ModuleNotFoundError("No module named 'slack_sdk'"))

    assert "openminion[slack]" in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__, ModuleNotFoundError)


def test_an_unrelated_import_time_fault_keeps_its_own_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(RuntimeError, match="socket-mode import exploded"):
        _build(monkeypatch, RuntimeError("socket-mode import exploded"))


def test_an_import_error_from_inside_the_installed_sdk_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An installed SDK missing its own dependency is not our missing extra."""
    with pytest.raises(ImportError) as excinfo:
        _build(
            monkeypatch,
            ImportError("cannot import name 'x' from slack_sdk"),
            submodules_only=True,
        )

    assert not isinstance(excinfo.value, MissingSlackSocketDependency)


def test_a_missing_module_inside_the_sdk_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ModuleNotFoundError) as excinfo:
        _build(
            monkeypatch,
            ModuleNotFoundError("No module named 'dependency_inside_sdk'"),
            submodules_only=True,
        )

    assert not isinstance(excinfo.value, MissingSlackSocketDependency)
