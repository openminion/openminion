"""Slack Socket Mode distinguishes an absent extra from an import-time fault."""

from __future__ import annotations

import sys

import pytest

from openminion.modules.controlplane.channels.slack.adapter import (
    MissingSlackSocketDependency,
    SlackSdkSocketClient,
)


class _RaisingFinder:
    """Intercept the Slack SDK import and raise the fault under test."""

    def __init__(self, error: BaseException) -> None:
        self.error = error

    def find_spec(
        self, fullname: str, path: object = None, target: object = None
    ) -> object:
        if fullname == "slack_sdk" or fullname.startswith("slack_sdk."):
            raise self.error
        return None


def _build(monkeypatch: pytest.MonkeyPatch, error: BaseException) -> None:
    monkeypatch.setattr(sys, "meta_path", [_RaisingFinder(error), *sys.meta_path])
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
