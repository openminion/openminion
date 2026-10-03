from __future__ import annotations

import copy
import json
import subprocess
from types import MethodType

import pytest

from openminion.base.config.base import ConfigError
from openminion.base.runtime.sandbox import (
    ExecResult,
    ExecSpec,
)
from openminion.modules.runtime.sandboxes.security_lab import (
    SecurityLabConfig,
    SecurityLabExecutionScope,
    SecurityLabExecutionSpec,
    coerce_security_lab_config,
)
from openminion.modules.runtime.sandboxes.docker import (
    DockerSandboxError,
    DockerSandboxRunner,
)

_DIGEST = "sha256:" + "a" * 64
_IMAGE = f"security-worker@example.invalid/worker@{_DIGEST}"


def _config(**overrides: object) -> SecurityLabConfig:
    values: dict[str, object] = {
        "daemon_socket": "unix:///var/run/docker.sock",
        "target_container": "security-target",
        "worker_image": _IMAGE,
        "executable_allowlist": ["curl"],
        "worker_uid": 10001,
        "worker_gid": 10001,
        "agent_identity_id": "security-researcher-local-lab",
        "command_timeout_seconds": 30,
        "max_output_bytes": 4096,
        "cpu_limit": 0.5,
        "memory_bytes": 67_108_864,
        "pids_limit": 32,
        "label": "Local security lab",
    }
    values.update(overrides)
    parsed = coerce_security_lab_config(values)
    assert parsed is not None
    return parsed


def _target() -> dict[str, object]:
    return {
        "Id": "target-" + "1" * 56,
        "Image": "sha256:" + "b" * 64,
        "State": {"Running": True},
        "Config": {"User": "10000:10000", "Volumes": None},
        "HostConfig": {
            "NetworkMode": "none",
            "ReadonlyRootfs": True,
            "Privileged": False,
            "PidMode": "",
            "IpcMode": "private",
            "Binds": None,
            "Devices": None,
            "CapDrop": ["ALL"],
            "CapAdd": None,
            "NanoCpus": 500_000_000,
            "Memory": 67_108_864,
            "PidsLimit": 32,
            "SecurityOpt": ["no-new-privileges"],
            "RestartPolicy": {"Name": "no"},
        },
        "NetworkSettings": {"Ports": {}},
        "Mounts": [],
    }


def _completed(stdout: object = b"", stderr: bytes = b"", returncode: int = 0):
    encoded = json.dumps(stdout).encode() if not isinstance(stdout, bytes) else stdout
    return subprocess.CompletedProcess([], returncode, encoded, stderr)


def _fake_control(runner: DockerSandboxRunner, target: dict[str, object]):
    calls: list[tuple[str, ...]] = []

    def command(self, *args: str, check: bool = True):
        del self, check
        calls.append(args)
        if args[:1] == ("info",):
            return _completed({"ID": "daemon-local"})
        if args[:2] == ("container", "inspect"):
            return _completed([target])
        if args[:2] == ("image", "inspect"):
            return _completed(
                [
                    {
                        "Id": "sha256:" + "c" * 64,
                        "RepoDigests": [_IMAGE],
                    }
                ]
            )
        if args[:1] == ("ps",):
            return _completed()
        if args[:1] == ("create",):
            return _completed(b"worker-123\n")
        if args[:1] == ("rm",):
            return _completed()
        raise AssertionError(args)

    runner._command = MethodType(command, runner)  # type: ignore[method-assign]
    return calls


def _execution_spec(runner: DockerSandboxRunner, **overrides: object):
    scope = runner.preflight()
    values = {
        "workspace_root": "/workspace",
        "security_lab": SecurityLabExecutionScope(
            session_id="session-1",
            activation_id="activation-1",
            config_fingerprint=scope.config_fingerprint,
            resolved_scope_fingerprint="resolved-activation",
            daemon_id=scope.daemon_id,
            target_container_id=scope.target_container_id,
            target_image_id=scope.target_image_id,
            worker_image_digest=scope.worker_image_digest,
        ),
    }
    values.update(overrides)
    return SecurityLabExecutionSpec(**values)


def test_security_lab_config_is_strict_and_opt_in() -> None:
    assert coerce_security_lab_config(None) is None
    assert _config().executable_allowlist == ("curl",)

    with pytest.raises(ConfigError, match="unix://"):
        _config(daemon_socket="tcp://docker.example:2376")
    with pytest.raises(ConfigError, match="sha256 digest"):
        _config(worker_image="worker:latest")
    with pytest.raises(ConfigError, match="unsupported fields"):
        _config(mounts=["/host:/workspace"])
    with pytest.raises(ConfigError, match="greater than zero"):
        _config(cpu_limit=float("inf"))


def test_preflight_freezes_exact_local_scope() -> None:
    runner = DockerSandboxRunner(_config(), docker_binary="/usr/bin/docker")
    try:
        _fake_control(runner, _target())
        scope = runner.preflight()
    finally:
        runner.close()

    assert scope.daemon_id == "daemon-local"
    assert scope.target_container_id.startswith("target-")
    assert scope.target_image_id.startswith("sha256:")
    assert scope.worker_image_digest == _IMAGE
    assert len(scope.config_fingerprint) == 64
    assert len(scope.resolved_scope_fingerprint) == 64


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("HostConfig", "NetworkMode"), "bridge"),
        (("HostConfig", "ReadonlyRootfs"), False),
        (("HostConfig", "Privileged"), True),
        (("HostConfig", "PidMode"), "host"),
        (("HostConfig", "IpcMode"), "container:peer"),
        (("HostConfig", "CapDrop"), []),
        (("HostConfig", "CapAdd"), ["NET_ADMIN"]),
        (("HostConfig", "NanoCpus"), 0),
        (("HostConfig", "Memory"), 0),
        (("HostConfig", "PidsLimit"), 0),
        (("HostConfig", "SecurityOpt"), []),
        (("HostConfig", "RestartPolicy"), {"Name": "always"}),
        (("State", "Running"), False),
        (("HostConfig", "Binds"), ["/host:/workspace"]),
        (("HostConfig", "Devices"), [{"PathOnHost": "/dev/null"}]),
        (("Config", "User"), "0"),
        (("Config", "Volumes"), {"/data": {}}),
        (("Mounts",), [{"Source": "/host"}]),
        (("NetworkSettings", "Ports"), {"8080/tcp": [{"HostPort": "8080"}]}),
    ],
)
def test_preflight_rejects_each_target_boundary(
    path: tuple[str, ...], value: object
) -> None:
    target = copy.deepcopy(_target())
    owner: dict[str, object] = target
    for key in path[:-1]:
        owner = owner[key]  # type: ignore[assignment]
    owner[path[-1]] = value
    runner = DockerSandboxRunner(_config(), docker_binary="/usr/bin/docker")
    try:
        _fake_control(runner, target)
        with pytest.raises(DockerSandboxError, match="hardening"):
            runner.preflight()
    finally:
        runner.close()


def test_run_uses_exact_direct_argv_and_always_removes_worker() -> None:
    runner = DockerSandboxRunner(_config(), docker_binary="/usr/bin/docker")
    calls = _fake_control(runner, _target())
    runner._start_worker = MethodType(  # type: ignore[method-assign]
        lambda self, worker_id, **kwargs: ExecResult(0, "ok", ""),
        runner,
    )
    try:
        result = runner.run_exec(
            ExecSpec(
                cmd=["curl", "--fail", "http://127.0.0.1:8080/defect"],
                cwd="/workspace",
            ),
            _execution_spec(
                runner,
                timeout_s=20,
                max_output_bytes=2048,
            ),
        )
    finally:
        runner.close()

    create = next(call for call in calls if call[0] == "create")
    assert result.stdout == "ok"
    assert "--pull=never" in create
    assert "--no-healthcheck" in create
    assert "--log-driver=none" in create
    assert "--network=container:" + str(_target()["Id"]) in create
    assert create[create.index("--entrypoint") + 1] == "curl"
    assert create[-3:] == (_IMAGE, "--fail", "http://127.0.0.1:8080/defect")
    assert "--read-only" in create
    assert "--cap-drop=ALL" in create
    assert "--security-opt=no-new-privileges" in create
    assert not any(
        value.startswith(("--env", "--volume", "--mount", "--device"))
        for value in create
    )
    assert "/var/run/docker.sock" not in create
    assert calls[-1] == ("rm", "--force", "worker-123")


def test_run_removes_worker_when_execution_fails() -> None:
    runner = DockerSandboxRunner(_config(), docker_binary="/usr/bin/docker")
    calls = _fake_control(runner, _target())

    def fail(self, worker_id: str, **kwargs: object):
        del self, worker_id, kwargs
        raise DockerSandboxError("SANDBOX_UNAVAILABLE", "start failed")

    runner._start_worker = MethodType(fail, runner)  # type: ignore[method-assign]
    try:
        with pytest.raises(DockerSandboxError, match="start failed"):
            runner.run_exec(
                ExecSpec(cmd=["curl", "http://127.0.0.1"], cwd="/workspace"),
                _execution_spec(runner),
            )
    finally:
        runner.close()
    assert calls[-1] == ("rm", "--force", "worker-123")


@pytest.mark.parametrize(
    "spec",
    [
        ExecSpec(cmd=["sh", "-c", "id"], cwd="/workspace"),
        ExecSpec(cmd=["curl", "localhost"], cwd="/tmp"),
        ExecSpec(cmd=["curl", "localhost"], cwd="/workspace", env={"TOKEN": "x"}),
    ],
)
def test_run_rejects_command_environment_or_workspace_drift(spec: ExecSpec) -> None:
    runner = DockerSandboxRunner(_config(), docker_binary="/usr/bin/docker")
    calls = _fake_control(runner, _target())
    try:
        with pytest.raises(DockerSandboxError, match="allowlisted|environment|scratch"):
            runner.run_exec(spec, _execution_spec(runner))
    finally:
        runner.close()
    assert not any(call[0] == "create" for call in calls)


def test_preflight_rejects_worker_digest_drift() -> None:
    runner = DockerSandboxRunner(
        _config(worker_image="other.invalid/worker@sha256:" + "d" * 64),
        docker_binary="/usr/bin/docker",
    )
    try:
        _fake_control(runner, _target())
        with pytest.raises(DockerSandboxError, match="digest"):
            runner.preflight()
    finally:
        runner.close()


def test_run_refuses_stale_worker_before_create() -> None:
    runner = DockerSandboxRunner(_config(), docker_binary="/usr/bin/docker")
    calls = _fake_control(runner, _target())
    original = runner._command

    def command(self, *args: str, check: bool = True):
        if args[:1] == ("ps",):
            calls.append(args)
            return _completed(b"stale-worker\n")
        return original(*args, check=check)

    runner._command = MethodType(command, runner)  # type: ignore[method-assign]
    try:
        with pytest.raises(DockerSandboxError, match="stale"):
            runner.run_exec(
                ExecSpec(cmd=["curl", "http://127.0.0.1"], cwd="/workspace"),
                _execution_spec(runner),
            )
    finally:
        runner.close()
    assert not any(call[0] == "create" for call in calls)


def test_docker_supervisor_environment_ignores_ambient_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = DockerSandboxRunner(_config(), docker_binary="/usr/bin/docker")
    captured: dict[str, object] = {}

    def fake_run(argv: list[str], **kwargs: object):
        captured["argv"] = argv
        captured["env"] = kwargs["env"]
        return _completed()

    monkeypatch.setenv("DOCKER_HOST", "tcp://attacker.invalid:2375")
    monkeypatch.setenv("HTTPS_PROXY", "http://secret.invalid")
    monkeypatch.setattr(subprocess, "run", fake_run)
    try:
        runner._command("version")  # noqa: SLF001
    finally:
        runner.close()

    env = captured["env"]
    assert isinstance(env, dict)
    assert "DOCKER_HOST" not in env
    assert "HTTPS_PROXY" not in env
    assert captured["argv"][:5] == [
        "/usr/bin/docker",
        "--config",
        captured["argv"][2],
        "--host",
        "unix:///var/run/docker.sock",
    ]
