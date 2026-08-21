from __future__ import annotations

import threading
import time
import uuid

from openminion.tools.ops import (
    ContainerTransport,
    LocalTransport,
    OperationTarget,
)
from openminion.tools.ops.contracts import TransportFacts, TransportResult
from openminion.tools.ops.interfaces import FileReadTransport, TargetTransport


def test_local_transport_executes_argv_without_shell() -> None:
    result = LocalTransport().run(
        OperationTarget(target_id="local", kind="local"),
        ("printf", "%s", "hello"),
        timeout_seconds=2,
    )
    assert result.return_code == 0
    assert result.stdout == "hello"


def test_command_and_file_read_capabilities_are_independent() -> None:
    class CommandOnlyTransport:
        def connect(self, target):
            return TransportFacts(
                kind=target.kind,
                platform=target.platform,
                connected=True,
            )

        inspect = connect

        def run(self, target, argv, **kwargs):
            del target, kwargs
            return TransportResult(argv=argv, return_code=0)

        def cancel(self, operation_id):
            del operation_id
            return False

        def close(self):
            return None

    command_only = CommandOnlyTransport()

    assert isinstance(command_only, TargetTransport)
    assert not isinstance(command_only, FileReadTransport)
    assert isinstance(LocalTransport(), TargetTransport)
    assert isinstance(LocalTransport(), FileReadTransport)


def test_local_transport_reports_timeout() -> None:
    result = LocalTransport().run(
        OperationTarget(target_id="local", kind="local"),
        ("sleep", "1"),
        timeout_seconds=0.01,
    )
    assert result.timed_out is True
    assert result.return_code == 124


def test_local_transport_reports_missing_executable() -> None:
    result = LocalTransport().run(
        OperationTarget(target_id="local", kind="local"),
        (f"openminion-missing-{uuid.uuid4().hex}",),
        timeout_seconds=2,
    )

    assert result.return_code == 127
    assert result.stderr


def test_local_transport_cancels_active_operation() -> None:
    transport = LocalTransport()
    result = {}

    def run() -> None:
        result["value"] = transport.run(
            OperationTarget(target_id="local", kind="local"),
            ("sleep", "5"),
            timeout_seconds=10,
            operation_id="cancel-me",
        )

    thread = threading.Thread(target=run)
    thread.start()
    for _ in range(100):
        if transport.cancel("cancel-me"):
            break
        time.sleep(0.01)
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert result["value"].cancelled is True
    assert result["value"].return_code == 130


def test_container_transport_builds_runtime_argv(monkeypatch) -> None:
    captured = {}

    def fake_run(
        argv,
        *,
        timeout_seconds,
        operation_id,
        active,
        lock,
        output_sink,
        cwd,
    ):
        captured["argv"] = argv
        captured["timeout"] = timeout_seconds
        captured["operation_id"] = operation_id
        captured["has_active_registry"] = active is not None
        captured["has_lock"] = lock is not None
        captured["output_sink"] = output_sink
        captured["cwd"] = cwd
        return object()

    monkeypatch.setattr(
        "openminion.tools.ops.transports.runtime.run_process",
        fake_run,
    )
    result = ContainerTransport("podman").run(
        OperationTarget(target_id="container", kind="container", container="app"),
        ("uname", "-a"),
        timeout_seconds=3,
    )
    assert result is not None
    assert captured == {
        "argv": ("podman", "exec", "app", "uname", "-a"),
        "timeout": 3,
        "operation_id": "",
        "has_active_registry": True,
        "has_lock": True,
        "output_sink": None,
        "cwd": "",
    }


def test_container_transport_passes_remote_selection_per_call(monkeypatch) -> None:
    captured: list[tuple[str, ...]] = []

    def fake_run(argv, **kwargs):
        del kwargs
        captured.append(argv)
        return object()

    monkeypatch.setattr(
        "openminion.tools.ops.transports.runtime.run_process",
        fake_run,
    )
    transport = ContainerTransport()
    transport.run(
        OperationTarget(
            target_id="docker",
            kind="container",
            container="app",
            docker_context="staging",
        ),
        ("uname", "-a"),
        timeout_seconds=3,
    )
    transport.run(
        OperationTarget(
            target_id="podman",
            kind="container",
            container="app",
            container_runtime="podman",
            podman_connection="staging",
        ),
        ("uname", "-a"),
        timeout_seconds=3,
    )

    assert captured == [
        ("docker", "--context", "staging", "exec", "app", "uname", "-a"),
        (
            "podman",
            "--connection",
            "staging",
            "exec",
            "app",
            "uname",
            "-a",
        ),
    ]
