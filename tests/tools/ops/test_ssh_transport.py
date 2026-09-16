from __future__ import annotations

import asyncio
import sys
import threading
import time
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic import ValidationError

from openminion.modules.runtime.credentials import CredentialRef
from openminion.tools.ops.contracts import (
    EndpointTrust,
    OperationTarget,
    TransportResult,
)
from openminion.tools.ops.transports import SshTransport
from openminion.tools.ops.transports.ssh import SshConnectionError
from openminion.tools.ops.transports.runtime import OUTPUT_LIMIT


class _Reader:
    def __init__(self, chunks: tuple[bytes, ...] = ()) -> None:
        self._chunks = list(chunks)

    async def read(self, count: int) -> bytes:
        del count
        return self._chunks.pop(0) if self._chunks else b""


class _Process:
    def __init__(
        self,
        stdout: tuple[bytes, ...] = (),
        stderr: tuple[bytes, ...] = (),
    ) -> None:
        self.stdout = _Reader(stdout)
        self.stderr = _Reader(stderr)
        self.exit_status = 0

    async def wait(self, *, check: bool = False):
        del check
        return SimpleNamespace(exit_status=self.exit_status)


def _fake_asyncssh(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "Error": OSError,
        "HostKeyNotVerifiable": type("HostKeyNotVerifiable", (OSError,), {}),
        "PermissionDenied": type("PermissionDenied", (OSError,), {}),
        "KeyImportError": type("KeyImportError", (ValueError,), {}),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _target() -> OperationTarget:
    return OperationTarget(
        target_id="remote",
        kind="ssh",
        address="ops.example.test",
        username="operator",
        credential_ref=CredentialRef(
            credential_id="remote",
            scope_kind="tool_family",
            scope_id="ops",
            source_kind="env",
            env_name="OPS_PASSWORD",
            rotation_policy="static",
        ),
        endpoint_trust=EndpointTrust(host_key="ssh-ed25519 fixture-key"),
    )


def test_ssh_transport_uses_pinned_key_and_closed_argv(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class Connection:
        async def create_process(self, command: str, *, encoding: object):
            captured["command"] = command
            captured["encoding"] = encoding
            return _Process((b"Linux\n",))

        def close(self) -> None:
            captured["closed"] = True

        async def wait_closed(self) -> None:
            captured["waited"] = True

        def abort(self) -> None:
            captured["aborted"] = True

    async def connect(address: str, **kwargs: object) -> Connection:
        captured["address"] = address
        captured.update(kwargs)
        return Connection()

    fake_asyncssh = _fake_asyncssh(
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
    assert captured["client_keys"] is None
    assert captured["config"] is None
    assert captured["agent_path"] is None
    assert captured["known_hosts"] == (["parsed:ssh-ed25519 fixture-key"], [], [])
    assert captured["command"] == "printf %s 'hello world'"
    assert captured["encoding"] is None
    assert captured["closed"] is True
    assert captured["waited"] is True


def test_ssh_transport_uses_private_key_without_password(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class Connection:
        async def create_process(self, command: str, *, encoding: object):
            del command, encoding
            return _Process((b"ok\n",))

        def close(self) -> None:
            return None

        async def wait_closed(self) -> None:
            return None

        def abort(self) -> None:
            return None

    async def connect(address: str, **kwargs: object) -> Connection:
        del address
        captured.update(kwargs)
        return Connection()

    monkeypatch.setitem(
        sys.modules,
        "asyncssh",
        _fake_asyncssh(
            import_public_key=lambda value: value,
            import_private_key=lambda value: f"parsed:{value}",
            connect=connect,
        ),
    )
    target = _target().model_copy(update={"ssh_auth_mode": "private_key"})

    result = SshTransport(lambda _: "PRIVATE KEY").run(
        target,
        ("uname", "-a"),
        timeout_seconds=2,
    )

    assert result.return_code == 0
    assert captured["password"] is None
    assert captured["client_keys"] == ["parsed:PRIVATE KEY"]


def test_ssh_transport_reports_missing_remote_extra(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "asyncssh", None)

    with pytest.raises(RuntimeError, match="dependency_missing"):
        SshTransport(lambda _: "password").run(
            _target(),
            ("uname", "-a"),
            timeout_seconds=2,
        )


def test_ssh_transport_times_out_and_closes_connection(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class Connection:
        async def create_process(self, command: str, *, encoding: object):
            del command, encoding
            await asyncio.sleep(1)

        def close(self) -> None:
            captured["closed"] = True

        async def wait_closed(self) -> None:
            captured["waited"] = True

        def abort(self) -> None:
            captured["aborted"] = True

    async def connect(address: str, **kwargs: object) -> Connection:
        del address, kwargs
        return Connection()

    monkeypatch.setitem(
        sys.modules,
        "asyncssh",
        _fake_asyncssh(
            import_public_key=lambda value: value,
            connect=connect,
        ),
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

        async def create_process(self, command: str, *, encoding: object):
            del command, encoding
            started.set()
            connection = self

            class Reader:
                async def read(self, count: int) -> bytes:
                    del count
                    while not connection.closed:
                        await asyncio.sleep(0.01)
                    raise ConnectionError("connection closed")

            class Process:
                stdout = Reader()
                stderr = Reader()
                exit_status = 130

                async def wait(self, *, check: bool = False):
                    del check
                    while not connection.closed:
                        await asyncio.sleep(0.01)
                    raise ConnectionError("connection closed")

            return Process()

        def close(self) -> None:
            self.closed = True

        async def wait_closed(self) -> None:
            return None

        def abort(self) -> None:
            self.closed = True

    async def connect(address: str, **kwargs: object) -> Connection:
        del address, kwargs
        return Connection()

    monkeypatch.setitem(
        sys.modules,
        "asyncssh",
        _fake_asyncssh(
            import_public_key=lambda value: value,
            connect=connect,
        ),
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


def test_ssh_transport_cancel_enqueues_before_active_cleanup() -> None:
    transport = SshTransport(lambda _: "password")
    begin_cleanup = threading.Event()
    cleanup_acquired = threading.Event()

    class Connection:
        closed = False

        def close(self) -> None:
            self.closed = True

    connection = Connection()

    class Loop:
        def call_soon_threadsafe(self, callback) -> None:
            begin_cleanup.set()
            assert not cleanup_acquired.wait(timeout=0.05)
            callback()

    with transport._lock:
        transport._active["near-complete"] = (
            cast(Any, Loop()),
            cast(Any, connection),
        )

    def cleanup() -> None:
        assert begin_cleanup.wait(timeout=1)
        with transport._lock:
            transport._active.pop("near-complete", None)
            transport._cancelled.discard("near-complete")
            cleanup_acquired.set()

    thread = threading.Thread(target=cleanup)
    thread.start()
    assert transport.cancel("near-complete") is True
    thread.join(timeout=1)

    assert connection.closed is True
    assert cleanup_acquired.is_set()


def test_ssh_target_rejects_environment_forwarding() -> None:
    payload = _target().model_dump()
    payload["environment_variables"] = {"TOKEN": "secret"}

    with pytest.raises(ValidationError, match="environment_variables"):
        OperationTarget.model_validate(payload)


@pytest.mark.parametrize("path", ["/srv/app/status", "/srv/app/../secret", "/link"])
def test_ssh_transport_refuses_direct_file_read_without_io(path: str) -> None:
    transport = SshTransport(lambda _: pytest.fail("credential access is remote I/O"))

    with pytest.raises(RuntimeError, match="remote_scope_unavailable"):
        transport.read(
            _target(),
            path,
            max_bytes=1024,
            timeout_seconds=2,
        )


def test_ssh_probe_authenticates_and_closes(monkeypatch) -> None:
    calls: list[str] = []

    class Connection:
        def close(self) -> None:
            calls.append("close")

        def abort(self) -> None:
            calls.append("abort")

        async def wait_closed(self) -> None:
            calls.append("wait_closed")

    async def connect(address: str, **kwargs: object) -> Connection:
        del address, kwargs
        calls.append("connect")
        return Connection()

    monkeypatch.setitem(
        sys.modules,
        "asyncssh",
        _fake_asyncssh(
            import_public_key=lambda value: value,
            connect=connect,
        ),
    )

    facts = SshTransport(lambda _: "password").connect(_target())

    assert facts.connected is True
    assert facts.capabilities == ("command",)
    assert calls == ["connect", "close", "wait_closed"]


@pytest.mark.parametrize(
    ("exception_name", "reason_code"),
    [
        ("HostKeyNotVerifiable", "host_key_mismatch"),
        ("PermissionDenied", "authentication_failed"),
        ("Error", "connection_failed"),
    ],
)
def test_ssh_probe_maps_connection_failure_reason(
    monkeypatch, exception_name: str, reason_code: str
) -> None:
    fake = _fake_asyncssh(import_public_key=lambda value: value)

    async def connect(address: str, **kwargs: object):
        del address, kwargs
        raise getattr(fake, exception_name)("failed")

    fake.connect = connect
    monkeypatch.setitem(sys.modules, "asyncssh", fake)

    with pytest.raises(SshConnectionError) as failure:
        SshTransport(lambda _: "password").connect(_target())

    assert failure.value.reason_code == reason_code


def test_ssh_probe_reports_unavailable_credential_before_connect(monkeypatch) -> None:
    async def connect(address: str, **kwargs: object):
        del address, kwargs
        pytest.fail("connection must not start without a credential")

    monkeypatch.setitem(
        sys.modules,
        "asyncssh",
        _fake_asyncssh(import_public_key=lambda value: value, connect=connect),
    )

    with pytest.raises(SshConnectionError) as failure:
        SshTransport(lambda _: "").connect(_target())

    assert failure.value.reason_code == "credential_unavailable"


def test_ssh_streams_early_and_redacts_split_secret(monkeypatch) -> None:
    emitted: list[tuple[str, str]] = []
    release = asyncio.Event()

    class StreamingReader:
        def __init__(self) -> None:
            self._read_count = 0

        async def read(self, count: int) -> bytes:
            del count
            self._read_count += 1
            if self._read_count == 1:
                return b"token=pass"
            if self._read_count == 2:
                await release.wait()
                return b"word ok"
            return b""

    class Process:
        stdout = StreamingReader()
        stderr = _Reader((b"err",))
        exit_status = 0

        async def wait(self, *, check: bool = False):
            del check
            assert ("stdout", "token=") in emitted
            release.set()
            return SimpleNamespace(exit_status=0)

    class Connection:
        async def create_process(self, command: str, *, encoding: object):
            del command, encoding
            return Process()

        def close(self) -> None:
            return None

        def abort(self) -> None:
            return None

        async def wait_closed(self) -> None:
            return None

    async def connect(address: str, **kwargs: object) -> Connection:
        del address, kwargs
        return Connection()

    monkeypatch.setitem(
        sys.modules,
        "asyncssh",
        _fake_asyncssh(import_public_key=lambda value: value, connect=connect),
    )

    result = SshTransport(lambda _: "password").run(
        _target(),
        ("printf", "ready"),
        timeout_seconds=2,
        output_sink=lambda stream, chunk: emitted.append((stream, chunk)),
    )

    assert result.stdout == "token=[REDACTED] ok"
    assert result.stderr == "err"
    assert "password" not in "".join(chunk for _, chunk in emitted)
    assert "".join(chunk for stream, chunk in emitted if stream == "stdout") == (
        result.stdout
    )


def test_ssh_masks_partial_secret_at_eof(monkeypatch) -> None:
    class Connection:
        async def create_process(self, command: str, *, encoding: object):
            del command, encoding
            return _Process((b"token=pass",))

        def close(self) -> None:
            return None

        def abort(self) -> None:
            return None

        async def wait_closed(self) -> None:
            return None

    async def connect(address: str, **kwargs: object) -> Connection:
        del address, kwargs
        return Connection()

    monkeypatch.setitem(
        sys.modules,
        "asyncssh",
        _fake_asyncssh(import_public_key=lambda value: value, connect=connect),
    )

    result = SshTransport(lambda _: "password").run(
        _target(), ("printf", "ready"), timeout_seconds=2
    )

    assert result.stdout == "token=[REDACTED]"


def test_ssh_caps_utf8_bytes_without_splitting_codepoint(monkeypatch) -> None:
    payload = ("é" * (OUTPUT_LIMIT // 2 + 10)).encode()

    class Connection:
        async def create_process(self, command: str, *, encoding: object):
            del command, encoding
            return _Process((payload,), (payload,))

        def close(self) -> None:
            return None

        def abort(self) -> None:
            return None

        async def wait_closed(self) -> None:
            return None

    async def connect(address: str, **kwargs: object) -> Connection:
        del address, kwargs
        return Connection()

    monkeypatch.setitem(
        sys.modules,
        "asyncssh",
        _fake_asyncssh(import_public_key=lambda value: value, connect=connect),
    )

    result = SshTransport(lambda _: "password").run(
        _target(), ("printf", "ready"), timeout_seconds=2
    )

    assert result.truncated is True
    assert len(result.stdout.encode()) == OUTPUT_LIMIT
    assert len(result.stderr.encode()) == OUTPUT_LIMIT


def test_ssh_continues_draining_after_output_cap(monkeypatch) -> None:
    class CountingReader(_Reader):
        reads = 0

        async def read(self, count: int) -> bytes:
            self.reads += 1
            return await super().read(count)

    stdout = CountingReader((b"x" * (OUTPUT_LIMIT + 1), b"discarded"))

    class Process:
        stderr = _Reader()
        exit_status = 0

        async def wait(self, *, check: bool = False):
            del check
            return SimpleNamespace(exit_status=0)

    process = Process()
    process.stdout = stdout

    class Connection:
        async def create_process(self, command: str, *, encoding: object):
            del command, encoding
            return process

        def close(self) -> None:
            return None

        def abort(self) -> None:
            return None

        async def wait_closed(self) -> None:
            return None

    async def connect(address: str, **kwargs: object) -> Connection:
        del address, kwargs
        return Connection()

    monkeypatch.setitem(
        sys.modules,
        "asyncssh",
        _fake_asyncssh(import_public_key=lambda value: value, connect=connect),
    )

    result = SshTransport(lambda _: "password").run(
        _target(), ("printf", "ready"), timeout_seconds=2
    )

    assert result.truncated is True
    assert stdout.reads == 3
    assert len(result.stdout.encode()) == OUTPUT_LIMIT


def test_ssh_masks_secret_prefix_at_output_cap(monkeypatch) -> None:
    payload = b"x" * (OUTPUT_LIMIT - 4) + b"pass"

    class Connection:
        async def create_process(self, command: str, *, encoding: object):
            del command, encoding
            return _Process((payload,))

        def close(self) -> None:
            return None

        def abort(self) -> None:
            return None

        async def wait_closed(self) -> None:
            return None

    async def connect(address: str, **kwargs: object) -> Connection:
        del address, kwargs
        return Connection()

    monkeypatch.setitem(
        sys.modules,
        "asyncssh",
        _fake_asyncssh(import_public_key=lambda value: value, connect=connect),
    )

    result = SshTransport(lambda _: "password").run(
        _target(), ("printf", "ready"), timeout_seconds=2
    )

    assert "pass" not in result.stdout
    assert result.truncated is True


def test_ssh_connection_and_command_share_one_deadline(monkeypatch) -> None:
    class Connection:
        async def create_process(self, command: str, *, encoding: object):
            del command, encoding
            await asyncio.sleep(0.04)
            return _Process((b"late",))

        def close(self) -> None:
            return None

        def abort(self) -> None:
            return None

        async def wait_closed(self) -> None:
            return None

    async def connect(address: str, **kwargs: object) -> Connection:
        del address, kwargs
        await asyncio.sleep(0.04)
        return Connection()

    monkeypatch.setitem(
        sys.modules,
        "asyncssh",
        _fake_asyncssh(import_public_key=lambda value: value, connect=connect),
    )
    started = time.monotonic()

    result = SshTransport(lambda _: "password").run(
        _target(), ("printf", "ready"), timeout_seconds=0.06
    )

    assert result.timed_out is True
    assert time.monotonic() - started < 0.15


def test_ssh_output_sink_failure_closes_connection(monkeypatch) -> None:
    closed = threading.Event()

    class Connection:
        async def create_process(self, command: str, *, encoding: object):
            del command, encoding
            return _Process((b"ready",))

        def close(self) -> None:
            closed.set()

        def abort(self) -> None:
            closed.set()

        async def wait_closed(self) -> None:
            return None

    async def connect(address: str, **kwargs: object) -> Connection:
        del address, kwargs
        return Connection()

    monkeypatch.setitem(
        sys.modules,
        "asyncssh",
        _fake_asyncssh(import_public_key=lambda value: value, connect=connect),
    )

    def fail_sink(_stream: str, _chunk: str) -> None:
        raise ValueError("sink failed")

    with pytest.raises(ValueError, match="sink failed"):
        SshTransport(lambda _: "password").run(
            _target(),
            ("printf", "ready"),
            timeout_seconds=2,
            output_sink=fail_sink,
        )

    assert closed.is_set()
