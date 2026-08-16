from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import pytest

from openminion.modules.runtime.credentials import CredentialRef
from openminion.tools.ops.contracts import EndpointTrust, OperationTarget
from openminion.tools.ops.registry import TargetRegistry
from openminion.tools.ops.service import OpsService
from openminion.tools.ops.transports import SshTransport

asyncssh = pytest.importorskip("asyncssh")
pytestmark = pytest.mark.e2e


@dataclass
class _SshFixture:
    port: int
    host_key: str
    private_key: str


@contextmanager
def _ssh_server() -> Iterator[_SshFixture]:
    ready = threading.Event()
    state: dict[str, Any] = {}
    client_key = asyncssh.generate_private_key("ssh-ed25519")
    client_public = client_key.export_public_key()

    class Server(asyncssh.SSHServer):
        def begin_auth(self, username: str) -> bool:
            return username == "openminion"

        def password_auth_supported(self) -> bool:
            return True

        def validate_password(self, username: str, password: str) -> bool:
            return username == "openminion" and password == "fixture-password"

        def public_key_auth_supported(self) -> bool:
            return True

        def validate_public_key(self, username: str, key: Any) -> bool:
            return username == "openminion" and key.export_public_key() == client_public

    def process(process: Any) -> None:
        process.stdout.write(f"executed:{process.command}\n")
        process.exit(0)

    def serve() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        host_key = asyncssh.generate_private_key("ssh-ed25519")
        listener = loop.run_until_complete(
            asyncssh.create_server(
                Server,
                "127.0.0.1",
                0,
                server_host_keys=[host_key],
                process_factory=process,
            )
        )
        state.update(loop=loop, listener=listener, host_key=host_key)
        ready.set()
        loop.run_forever()
        listener.close()
        loop.run_until_complete(listener.wait_closed())
        loop.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    assert ready.wait(timeout=5)
    loop = state["loop"]

    def stop() -> None:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)

    try:
        yield _SshFixture(
            port=int(state["listener"].get_port()),
            host_key=state["host_key"].export_public_key().decode(),
            private_key=client_key.export_private_key().decode(),
        )
    finally:
        stop()


def _target(fixture: _SshFixture, auth_mode: str) -> OperationTarget:
    return OperationTarget(
        target_id=f"fixture-{auth_mode}",
        kind="ssh",
        environment="fixture",
        address="127.0.0.1",
        port=fixture.port,
        username="openminion",
        ssh_auth_mode=auth_mode,
        credential_ref=CredentialRef(
            credential_id=f"fixture-{auth_mode}",
            scope_kind="tool_family",
            scope_id="ops",
            source_kind="env",
            env_name="UNUSED_FIXTURE_CREDENTIAL",
            rotation_policy="static",
        ),
        endpoint_trust=EndpointTrust(host_key=fixture.host_key),
    )


@pytest.mark.parametrize("auth_mode", ["password", "private_key"])
def test_configured_ssh_plan_run_and_evidence(
    auth_mode: str,
) -> None:
    with _ssh_server() as fixture:
        target = _target(fixture, auth_mode)
        credential = (
            "fixture-password" if auth_mode == "password" else fixture.private_key
        )
        service = OpsService(
            targets=TargetRegistry((target,)),
            transports={"ssh": SshTransport(lambda _ref: credential)},
        )
        plan = service.plan_command(
            target_id=target.target_id,
            argv=("printf", "%s", "hello world"),
            session_id="ssh-e2e",
        )
        job = service.run_plan(
            plan_id=plan.plan_id,
            plan_hash=plan.plan_hash,
            approval_id="fixture-approval",
        )
        assert job.status == "succeeded", job.error
        evidence = service.inspect_evidence(job.evidence_id)

    assert evidence.target_id == target.target_id
    assert evidence.approval_id == "fixture-approval"
    assert "executed:printf %s 'hello world'" in evidence.stdout_preview


def test_configured_ssh_rejects_changed_host_key_before_evidence() -> None:
    with _ssh_server() as fixture:
        target = _target(fixture, "password").model_copy(
            update={
                "endpoint_trust": EndpointTrust(
                    host_key=asyncssh.generate_private_key("ssh-ed25519")
                    .export_public_key()
                    .decode()
                )
            }
        )
        service = OpsService(
            targets=TargetRegistry((target,)),
            transports={"ssh": SshTransport(lambda _ref: "fixture-password")},
        )
        plan = service.plan_command(target_id=target.target_id, argv=("uname", "-a"))
        job = service.run_plan(
            plan_id=plan.plan_id,
            plan_hash=plan.plan_hash,
            approval_id="fixture-approval",
        )

    assert job.status == "failed"
    assert job.evidence_id == ""
    assert job.error
