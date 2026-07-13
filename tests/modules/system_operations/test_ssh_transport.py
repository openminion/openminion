from __future__ import annotations

import asyncio
import sys
import threading
import time
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from openminion.modules.runtime.credentials import CredentialRef
from openminion.modules.system_operations.schemas import (
    EndpointTrust,
    OperationTarget,
    TransportResult,
)
from openminion.modules.system_operations.transports import SshTransport


def _target() -> OperationTarget:
    return OperationTarget(
        target_id="remote",
        kind="ssh",
        address="ops.example.test",
        username="operator",
        credential_ref=CredentialRef(
            credential_id="remote",
            scope_kind="tool_family",
            scope_id="system_operations",
            source_kind="env",
            env_name="OPS_PASSWORD",
            rotation_policy="static",
        ),
        endpoint_trust=EndpointTrust(host_key="ssh-ed25519 fixture-key"),
    )


def test_ssh_transport_uses_pinned_key_and_closed_argv(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class Connection:
        async def run(self, command: str, *, check: bool):
            captured["command"] = command
            captured["check"] = check
            return SimpleNamespace(stdout="Linux\n", stderr="", exit_status=0)

        def close(self) -> None:
            captured["closed"] = True

        async def wait_closed(self) -> None:
            captured["waited"] = True

    async def connect(address: str, **kwargs: object) -> Connection:
        captured["address"] = address
        captured.update(kwargs)
        return Connection()

    fake_asyncssh = SimpleNamespace(
        import_public_key=lambda value: f"parsed:{value}",
        connect=connect,
    )
    monkeypatch.setitem(sys.modules, "asyncssh", fake_asyncssh)

    result = SshTransport(lambda _: "password").run(
        _target(),
        ("printf", "%s", "hello world"),
        timeout_seconds=2,
    )

    assert result.return_code == 0
    assert result.stdout == "Linux\n"
    assert captured["address"] == "ops.example.test"
    assert captured["password"] == "password"
    assert captured["known_hosts"] == (["parsed:ssh-ed25519 fixture-key"], [], [])
    assert captured["command"] == "printf %s 'hello world'"
    assert captured["closed"] is True
    assert captured["waited"] is True


def test_ssh_transport_reports_missing_remote_extra(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "asyncssh", None)

    with pytest.raises(RuntimeError, match="optional 'remote' dependency"):
        SshTransport(lambda _: "password").run(
            _target(),
            ("uname", "-a"),
            timeout_seconds=2,
        )


def test_ssh_transport_times_out_and_closes_connection(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class Connection:
        async def run(self, command: str, *, check: bool):
            del command, check
            await asyncio.sleep(1)

        def close(self) -> None:
            captured["closed"] = True

        async def wait_closed(self) -> None:
            captured["waited"] = True

    async def connect(address: str, **kwargs: object) -> Connection:
        del address, kwargs
        return Connection()

    monkeypatch.setitem(
        sys.modules,
        "asyncssh",
        SimpleNamespace(import_public_key=lambda value: value, connect=connect),
    )

    result = SshTransport(lambda _: "password").run(
        _target(),
        ("uname", "-a"),
        timeout_seconds=0.01,
    )

    assert result.return_code == 124
    assert result.timed_out is True
    assert captured == {"closed": True, "waited": True}


def test_ssh_transport_cancels_active_operation(monkeypatch) -> None:
    started = threading.Event()

    class Connection:
        def __init__(self) -> None:
            self.closed = False

        async def run(self, command: str, *, check: bool):
            del command, check
            started.set()
            while not self.closed:
                await asyncio.sleep(0.01)
            raise ConnectionError("connection closed")

        def close(self) -> None:
            self.closed = True

        async def wait_closed(self) -> None:
            return None

    async def connect(address: str, **kwargs: object) -> Connection:
        del address, kwargs
        return Connection()

    monkeypatch.setitem(
        sys.modules,
        "asyncssh",
        SimpleNamespace(import_public_key=lambda value: value, connect=connect),
    )
    transport = SshTransport(lambda _: "password")
    result: dict[str, TransportResult] = {}

    def run() -> None:
        result["value"] = transport.run(
            _target(),
            ("uname", "-a"),
            timeout_seconds=2,
            operation_id="remote-observation",
        )

    thread = threading.Thread(target=run)
    thread.start()
    assert started.wait(timeout=1)
    for _ in range(100):
        if transport.cancel("remote-observation"):
            break
        time.sleep(0.01)
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert result["value"].cancelled is True
    assert result["value"].return_code == 130


def test_ssh_target_rejects_environment_forwarding() -> None:
    payload = _target().model_dump()
    payload["environment_variables"] = {"TOKEN": "secret"}

    with pytest.raises(ValidationError, match="environment_variables"):
        OperationTarget.model_validate(payload)
