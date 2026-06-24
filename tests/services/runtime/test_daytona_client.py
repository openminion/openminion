from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

import pytest

from openminion.services.runtime.daytona.client import (
    DaytonaClient,
    DaytonaClientError,
    DaytonaConfig,
    DaytonaTransportError,
)


@dataclass
class _FakeTransport:
    opened_with: list[tuple[str, str]] = field(default_factory=list)
    closed: int = 0
    workspace_requests: list[dict[str, Any]] = field(default_factory=list)
    destroy_requests: list[str] = field(default_factory=list)
    command_requests: list[dict[str, Any]] = field(default_factory=list)
    session_start_requests: list[dict[str, Any]] = field(default_factory=list)
    session_poll_requests: list[dict[str, Any]] = field(default_factory=list)
    session_input_requests: list[dict[str, Any]] = field(default_factory=list)
    session_terminate_requests: list[dict[str, Any]] = field(default_factory=list)
    create_response: Mapping[str, Any] = field(
        default_factory=lambda: {
            "workspace_id": "ws-123",
            "name": "sandbox-a",
            "image": "img:latest",
            "metadata": {"tier": "default"},
        }
    )
    command_response: Mapping[str, Any] = field(
        default_factory=lambda: {
            "workspace_id": "ws-123",
            "returncode": 0,
            "stdout": "ok",
            "stderr": "",
            "timed_out": False,
        }
    )
    session_start_response: Mapping[str, Any] = field(
        default_factory=lambda: {
            "workspace_id": "ws-123",
            "session_id": "remote-sess-1",
        }
    )
    session_poll_response: Mapping[str, Any] = field(
        default_factory=lambda: {
            "workspace_id": "ws-123",
            "session_id": "remote-sess-1",
            "running": True,
            "stdout": "",
            "stderr": "",
        }
    )
    create_error: Exception | None = None
    command_error: Exception | None = None
    session_error: Exception | None = None

    def open(self, config: DaytonaConfig, *, api_key: str) -> None:
        self.opened_with.append((config.endpoint, api_key))

    def close(self) -> None:
        self.closed += 1

    def create_workspace(
        self,
        *,
        name: str,
        image: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        self.workspace_requests.append(
            {"name": name, "image": image, "metadata": dict(metadata or {})}
        )
        if self.create_error is not None:
            raise self.create_error
        return self.create_response

    def destroy_workspace(self, workspace_id: str) -> None:
        self.destroy_requests.append(workspace_id)

    def execute_command(
        self,
        *,
        workspace_id: str,
        command: list[str],
        cwd: str | None,
        env: Mapping[str, str],
        timeout_s: float,
        max_output_bytes: int,
    ) -> Mapping[str, Any]:
        self.command_requests.append(
            {
                "workspace_id": workspace_id,
                "command": list(command),
                "cwd": cwd,
                "env": dict(env),
                "timeout_s": timeout_s,
                "max_output_bytes": max_output_bytes,
            }
        )
        if self.command_error is not None:
            raise self.command_error
        return self.command_response

    def start_session(
        self,
        *,
        workspace_id: str,
        command: list[str],
        cwd: str | None,
        env: Mapping[str, str],
        timeout_s: float,
        max_output_bytes: int,
        use_pty: bool,
    ) -> Mapping[str, Any]:
        self.session_start_requests.append(
            {
                "workspace_id": workspace_id,
                "command": list(command),
                "cwd": cwd,
                "env": dict(env),
                "timeout_s": timeout_s,
                "max_output_bytes": max_output_bytes,
                "use_pty": use_pty,
            }
        )
        if self.session_error is not None:
            raise self.session_error
        return self.session_start_response

    def poll_session(
        self,
        *,
        workspace_id: str,
        session_id: str,
        max_output_bytes: int,
    ) -> Mapping[str, Any]:
        self.session_poll_requests.append(
            {
                "workspace_id": workspace_id,
                "session_id": session_id,
                "max_output_bytes": max_output_bytes,
            }
        )
        if self.session_error is not None:
            raise self.session_error
        return self.session_poll_response

    def send_session_input(
        self,
        *,
        workspace_id: str,
        session_id: str,
        payload: bytes,
    ) -> Mapping[str, Any] | None:
        self.session_input_requests.append(
            {
                "workspace_id": workspace_id,
                "session_id": session_id,
                "payload": payload,
            }
        )
        if self.session_error is not None:
            raise self.session_error
        return None

    def terminate_session(
        self,
        *,
        workspace_id: str,
        session_id: str,
        signal_name: str,
    ) -> Mapping[str, Any] | None:
        self.session_terminate_requests.append(
            {
                "workspace_id": workspace_id,
                "session_id": session_id,
                "signal_name": signal_name,
            }
        )
        if self.session_error is not None:
            raise self.session_error
        return {
            "workspace_id": workspace_id,
            "session_id": session_id,
            "running": False,
            "exit_code": -15,
            "killed": True,
            "stdout": "",
            "stderr": "",
        }


def test_client_open_close_and_refresh_resolve_api_key() -> None:
    transport = _FakeTransport()
    config = DaytonaConfig(
        endpoint="https://daytona.example",
        api_key="secret",
    )
    client = DaytonaClient(config=config, transport=transport)

    client.open()
    assert client.connected is True
    assert transport.opened_with == [("https://daytona.example", "secret")]

    client.refresh()
    assert transport.closed == 1
    assert transport.opened_with == [
        ("https://daytona.example", "secret"),
        ("https://daytona.example", "secret"),
    ]

    client.close()
    assert client.connected is False
    assert transport.closed == 2


def test_create_workspace_uses_default_image_and_metadata() -> None:
    transport = _FakeTransport()
    config = DaytonaConfig(
        endpoint="https://daytona.example",
        default_workspace_image="python:3.12",
    )
    client = DaytonaClient(config=config, transport=transport)

    workspace = client.create_workspace(
        name="job-a",
        metadata={"agent": "ops"},
    )

    assert workspace.workspace_id == "ws-123"
    assert transport.workspace_requests == [
        {
            "name": "job-a",
            "image": "python:3.12",
            "metadata": {"agent": "ops"},
        }
    ]


def test_execute_command_filters_env_by_allowlist() -> None:
    transport = _FakeTransport()
    config = DaytonaConfig(endpoint="https://daytona.example", command_timeout_s=45.0)
    client = DaytonaClient(config=config, transport=transport)

    result = client.execute_command(
        workspace_id="ws-123",
        command=["python", "-V"],
        cwd="/workspace",
        env={"HOME": "/tmp/home", "PATH": "/bin", "SECRET": "drop"},
        env_allowlist=["PATH"],
    )

    assert result.returncode == 0
    assert transport.command_requests == [
        {
            "workspace_id": "ws-123",
            "command": ["python", "-V"],
            "cwd": "/workspace",
            "env": {"PATH": "/bin"},
            "timeout_s": 45.0,
            "max_output_bytes": 1_048_576,
        }
    ]


def test_execute_command_truncates_output_to_cap() -> None:
    transport = _FakeTransport(
        command_response={
            "workspace_id": "ws-123",
            "returncode": 0,
            "stdout": "abcdef",
            "stderr": "123456",
            "truncated": False,
        }
    )
    config = DaytonaConfig(endpoint="https://daytona.example", max_output_bytes=8)
    client = DaytonaClient(config=config, transport=transport)

    result = client.execute_command(
        workspace_id="ws-123",
        command=["echo", "hello"],
    )

    assert result.stdout == "abcdef"
    assert result.stderr == "12"
    assert result.truncated is True


def test_session_methods_filter_env_and_normalize_results() -> None:
    transport = _FakeTransport(
        session_poll_response={
            "workspace_id": "ws-123",
            "session_id": "remote-sess-1",
            "running": False,
            "exit_code": 0,
            "stdout": "hello world",
            "stderr": "",
        }
    )
    client = DaytonaClient(
        config=DaytonaConfig(endpoint="https://daytona.example"),
        transport=transport,
    )

    started = client.start_session(
        workspace_id="ws-123",
        command=["cat"],
        cwd="/workspace",
        env={"PATH": "/bin", "SECRET": "drop"},
        env_allowlist=["PATH"],
        use_pty=True,
    )
    polled = client.poll_session(
        workspace_id=started.workspace_id,
        session_id=started.session_id,
    )
    client.send_session_input(
        workspace_id=started.workspace_id,
        session_id=started.session_id,
        payload=b"hello",
    )
    killed = client.terminate_session(
        workspace_id=started.workspace_id,
        session_id=started.session_id,
        signal_name="TERM",
    )

    assert started.session_id == "remote-sess-1"
    assert transport.session_start_requests[0]["env"] == {"PATH": "/bin"}
    assert transport.session_start_requests[0]["use_pty"] is True
    assert polled.running is False
    assert polled.exit_code == 0
    assert transport.session_input_requests[0]["payload"] == b"hello"
    assert killed.killed is True


@pytest.mark.parametrize(
    ("exc", "expected_code"),
    [
        (TimeoutError("boom"), "SANDBOX_RESOURCE_LIMIT"),
        (
            DaytonaTransportError(code="NETWORK_DENIED", message="egress denied"),
            "SANDBOX_NETWORK_DENIED",
        ),
        (
            DaytonaTransportError(code="TIMEOUT", message="deadline"),
            "SANDBOX_RESOURCE_LIMIT",
        ),
        (
            DaytonaTransportError(
                code="NO_ROUTE",
                message="down",
                status_code=403,
                details={"reason": "network policy denied"},
            ),
            "SANDBOX_NETWORK_DENIED",
        ),
        (
            DaytonaTransportError(code="UNAVAILABLE", message="offline"),
            "SANDBOX_UNAVAILABLE",
        ),
    ],
)
def test_error_mapping_for_create_workspace_and_execute(
    exc: Exception, expected_code: str
) -> None:
    transport = _FakeTransport(create_error=exc, command_error=exc)
    client = DaytonaClient(
        config=DaytonaConfig(endpoint="https://daytona.example"),
        transport=transport,
    )

    with pytest.raises(DaytonaClientError) as create_error:
        client.create_workspace(name="job-a")
    assert create_error.value.code == expected_code

    with pytest.raises(DaytonaClientError) as exec_error:
        client.execute_command(workspace_id="ws-123", command=["echo", "hi"])
    assert exec_error.value.code == expected_code


def test_execute_command_rejects_empty_command() -> None:
    client = DaytonaClient(
        config=DaytonaConfig(endpoint="https://daytona.example"),
        transport=_FakeTransport(),
    )

    with pytest.raises(DaytonaClientError) as exc:
        client.execute_command(workspace_id="ws-123", command=[])

    assert exc.value.code == "SANDBOX_UNAVAILABLE"
