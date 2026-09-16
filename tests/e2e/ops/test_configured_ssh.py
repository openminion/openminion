from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import pytest
from typer.testing import CliRunner

from openminion.modules.policy.models import PolicyConfig
from openminion.modules.policy.runtime.service import PolicyCtl
from openminion.modules.runtime.credentials import CredentialRef
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.tools.ops.contracts import EndpointTrust, OperationTarget
from openminion.tools.ops import cli
from openminion.tools.ops.cli import app
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
    service_state: dict[str, Any]


@contextmanager
def _ssh_server() -> Iterator[_SshFixture]:
    ready = threading.Event()
    state: dict[str, Any] = {"cancel_started": threading.Event()}
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
        command = process.command
        if command.startswith("systemctl --user show openminion-fixture.service"):
            active = bool(state.get("service_active"))
            process.stdout.write(
                "ActiveState=active\nSubState=running\n"
                if active
                else "ActiveState=inactive\nSubState=dead\n"
            )
            process.exit(0)
        elif command == "systemctl --user restart openminion-fixture.service":
            state["service_active"] = True
            state["restart_count"] = int(state.get("restart_count", 0)) + 1
            process.exit(0)
        elif command == "systemctl --user restart openminion-unhealthy.service":
            process.exit(0)
        elif command.endswith("http://127.0.0.1:8765/health"):
            if state.get("service_active"):
                process.stdout.write("fixture-ready")
                process.exit(0)
            else:
                process.stderr.write("fixture unavailable")
                process.exit(22)
        elif command.endswith("http://127.0.0.1:8766/health"):
            process.stderr.write("fixture unavailable")
            process.exit(22)
        elif "stream-fixture" in command:
            process.stdout.write("early\n")

            def finish() -> None:
                process.stderr.write("late\n")
                process.exit(0)

            asyncio.get_running_loop().call_later(0.05, finish)
        elif "cancel-fixture" in command:
            state["cancel_started"].set()
        else:
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
            service_state=state,
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


def _service(target: OperationTarget, credential: str) -> OpsService:
    return OpsService(
        targets=TargetRegistry((target,)),
        transports={"ssh": SshTransport(lambda _ref: credential)},
        action_policy=PolicyCtl.with_sqlite(
            ":memory:", config=PolicyConfig(mode="enforce")
        ),
    )


def _approve_and_run(service: OpsService, plan):
    with pytest.raises(ToolRuntimeError) as pending:
        service.run_plan(
            plan_id=plan.plan_id,
            plan_hash=plan.plan_hash,
            session_id=plan.session_id,
        )
    assert pending.value.code == "CONFIRM_REQUIRED"
    approval_id = str(pending.value.details["approval_id"])
    service.action_policy.resolve_confirmation(approval_id, "allow_once")
    return service.run_plan(
        plan_id=plan.plan_id,
        plan_hash=plan.plan_hash,
        session_id=plan.session_id,
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
        service = _service(target, credential)
        plan = service.plan_command(
            target_id=target.target_id,
            argv=("printf", "%s", "hello world"),
            session_id="ssh-e2e",
        )
        job = _approve_and_run(service, plan)
        assert job.status == "succeeded", job.error
        evidence = service.inspect_evidence(job.evidence_id)

    assert evidence.target_id == target.target_id
    assert evidence.approval_id
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
        service = _service(target, "fixture-password")
        plan = service.plan_command(
            target_id=target.target_id,
            argv=("uname", "-a"),
            session_id="ssh-e2e",
        )
        job = _approve_and_run(service, plan)

    assert job.status == "failed"
    assert job.evidence_id == ""
    assert job.error


def test_configured_ssh_cli_stream_keeps_final_json_on_stdout(monkeypatch) -> None:
    with _ssh_server() as fixture:
        target = _target(fixture, "password")
        service = _service(target, "fixture-password")
        monkeypatch.setattr(cli, "_configured_service", lambda _config: service)
        plan = service.plan_command(
            target_id=target.target_id,
            argv=("printf", "stream-fixture"),
            session_id="ssh-stream-e2e",
        )

        result = CliRunner().invoke(
            app,
            [
                "command-run",
                plan.plan_id,
                plan.plan_hash,
                "--session",
                plan.session_id,
                "--confirm",
                "--stream",
            ],
        )

    assert result.exit_code == 0
    assert json.loads(result.stdout)["status"] == "succeeded"
    assert result.stderr == "early\nlate\n"


def test_configured_ssh_user_service_workflow_verifies_separate_oracles() -> None:
    with _ssh_server() as fixture:
        target = _target(fixture, "password")
        service = _service(target, "fixture-password")
        probe = service.inspect_target_readiness(target.target_id, probe=True)
        assert probe["probe"]["status"] == "succeeded"

        def run(argv: tuple[str, ...]):
            plan = service.plan_command(
                target_id=target.target_id,
                argv=argv,
                session_id="ssh-service-e2e",
            )
            job = _approve_and_run(service, plan)
            return job, service.inspect_evidence(job.evidence_id)

        show = (
            "systemctl",
            "--user",
            "show",
            "openminion-fixture.service",
            "--property=ActiveState",
            "--property=SubState",
        )
        before_job, before = run(show)
        restart_job, restart = run(
            ("systemctl", "--user", "restart", "openminion-fixture.service")
        )
        repeated = service.run_plan(
            plan_id=restart_job.plan_id,
            plan_hash=service.plans.get(restart_job.plan_id).plan_hash,
            session_id="ssh-service-e2e",
        )
        state_job, after = run(show)
        health_job, health = run(
            (
                "curl",
                "--fail",
                "--silent",
                "--show-error",
                "--max-time",
                "5",
                "http://127.0.0.1:8765/health",
            )
        )

    assert before_job.status == "succeeded"
    assert before.stdout_preview == "ActiveState=inactive\nSubState=dead\n"
    assert restart_job.status == "succeeded"
    assert restart.command_hash
    assert repeated.job_id == restart_job.job_id
    assert fixture.service_state["restart_count"] == 1
    assert state_job.status == "succeeded"
    assert after.stdout_preview == "ActiveState=active\nSubState=running\n"
    assert health_job.status == "succeeded"
    assert health.stdout_preview == "fixture-ready"


def test_configured_ssh_zero_exit_does_not_replace_health_verification() -> None:
    with _ssh_server() as fixture:
        target = _target(fixture, "password")
        service = _service(target, "fixture-password")
        restart_plan = service.plan_command(
            target_id=target.target_id,
            argv=("systemctl", "--user", "restart", "openminion-unhealthy.service"),
            session_id="ssh-unhealthy-e2e",
        )
        restart = _approve_and_run(service, restart_plan)
        health_plan = service.plan_command(
            target_id=target.target_id,
            argv=(
                "curl",
                "--fail",
                "--silent",
                "--show-error",
                "--max-time",
                "5",
                "http://127.0.0.1:8766/health",
            ),
            session_id="ssh-unhealthy-e2e",
        )
        health = _approve_and_run(service, health_plan)

    assert restart.status == "succeeded"
    assert health.status == "failed"
    assert service.inspect_evidence(health.evidence_id).return_code == 22


def test_configured_ssh_cross_thread_cancel(monkeypatch) -> None:
    monkeypatch.setenv("PYTHONASYNCIODEBUG", "1")
    with _ssh_server() as fixture:
        transport = SshTransport(lambda _ref: "fixture-password")
        result: dict[str, Any] = {}

        def run() -> None:
            result["value"] = transport.run(
                _target(fixture, "password"),
                ("printf", "cancel-fixture"),
                timeout_seconds=5,
                operation_id="cancel-fixture",
            )

        thread = threading.Thread(target=run)
        thread.start()
        assert fixture.service_state["cancel_started"].wait(timeout=2)
        assert transport.cancel("cancel-fixture") is True
        thread.join(timeout=2)

    assert not thread.is_alive()
    assert result["value"].cancelled is True
