from __future__ import annotations

import shlex
from types import SimpleNamespace

import pytest

from openminion.modules.runtime.sandboxes.daytona import (
    DaytonaConfig,
    DaytonaSdkTransport,
    DaytonaTransportError,
)


class _Process:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None, dict[str, str], int]] = []

    def exec(self, command: str, *, cwd: str | None, env: dict[str, str], timeout: int):
        self.calls.append((command, cwd, env, timeout))
        return SimpleNamespace(exit_code=0, result="hello")


class _Sandbox:
    id = "sandbox-1"

    def __init__(self) -> None:
        self.process = _Process()
        self.deleted = False

    def get_user_root_dir(self) -> str:
        return "/home/daytona"

    def delete(self, *, wait: bool) -> None:
        assert wait is True
        self.deleted = True


class _Daytona:
    instances: list["_Daytona"] = []

    def __init__(self, config) -> None:
        self.config = config
        self.sandbox = _Sandbox()
        self.create_args = None
        self.create_timeout = None
        self.instances.append(self)

    def create(self, params, *, timeout: float):
        self.create_args = params
        self.create_timeout = timeout
        return self.sandbox


def _fake_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    class SdkConfig:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    class Image:
        @staticmethod
        def base(value: str) -> str:
            return value

    class CreateSandboxFromImageParams:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    monkeypatch.setitem(
        __import__("sys").modules,
        "daytona",
        SimpleNamespace(
            Daytona=_Daytona,
            DaytonaConfig=SdkConfig,
            Image=Image,
            CreateSandboxFromImageParams=CreateSandboxFromImageParams,
        ),
    )


def test_sdk_transport_create_execute_and_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_sdk(monkeypatch)
    transport = DaytonaSdkTransport()
    config = DaytonaConfig(
        endpoint="https://daytona.example/api",
        api_key="secret",
        connect_timeout_s=8,
    )

    transport.open(config, api_key="secret")
    workspace = transport.create_workspace(
        name="test",
        image="python:3.11",
        metadata={"net_mode": "allow", "allowed_domains": ["pypi.org"]},
    )
    result = transport.execute_command(
        workspace_id="sandbox-1",
        command=["python3", "-c", "print('hello world')"],
        cwd="/home/daytona",
        env={"PYTHONHASHSEED": "0"},
        timeout_s=2.2,
        max_output_bytes=100,
    )
    sdk = _Daytona.instances[-1]

    assert sdk.config.kwargs == {
        "api_key": "secret",
        "api_url": "https://daytona.example/api",
    }
    assert sdk.create_args.kwargs["domain_allow_list"] == "pypi.org"
    assert workspace["metadata"] == {"root_dir": "/home/daytona"}
    assert result["stdout"] == "hello"
    command, cwd, env, timeout = sdk.sandbox.process.calls[0]
    assert shlex.split(command) == ["python3", "-c", "print('hello world')"]
    assert cwd == "/home/daytona"
    assert env == {"PYTHONHASHSEED": "0"}
    assert timeout == 3

    transport.destroy_workspace("sandbox-1")
    assert sdk.sandbox.deleted is True


def test_sdk_transport_requires_api_key() -> None:
    transport = DaytonaSdkTransport()
    with pytest.raises(DaytonaTransportError, match="API key"):
        transport.open(
            DaytonaConfig(endpoint="https://daytona.example/api"), api_key=""
        )
