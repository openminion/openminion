"""Local Docker runner for the opt-in authorized security lab."""

from __future__ import annotations

import hashlib
import json
import os
import selectors
import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openminion.base.runtime.sandbox import (
    ExecResult,
    ExecSpec,
    ExecutionSandboxSpec,
    FsDeleteSpec,
    FsResult,
    FsWriteSpec,
    NetFetchSpec,
    NetResult,
)
from openminion.modules.runtime.constants import DOCKER_CONTROL_OUTPUT_LIMIT_BYTES
from openminion.modules.runtime.sandboxes.security_lab import (
    SecurityLabConfig,
    SecurityLabExecResult,
    SecurityLabExecutionFacts,
    SecurityLabExecutionSpec,
)


class DockerSandboxError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


@dataclass(frozen=True)
class DockerLabScope:
    daemon_id: str
    target_container_id: str
    target_image_id: str
    worker_image_id: str
    worker_image_digest: str
    config_fingerprint: str
    resolved_scope_fingerprint: str


def _fingerprint(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


class DockerSandboxRunner:
    """Execute one direct argv in a worker joined to a hardened target namespace."""

    name = "docker-security-lab"

    def __init__(self, config: SecurityLabConfig, *, docker_binary: str) -> None:
        self.config = config
        self._docker_binary = str(Path(docker_binary).resolve())
        self._runtime_root = tempfile.TemporaryDirectory(
            prefix="openminion-docker-lab-"
        )
        root = Path(self._runtime_root.name)
        self._docker_config = root / "docker-config"
        self._home = root / "home"
        self._docker_config.mkdir(mode=0o700)
        self._home.mkdir(mode=0o700)
        self._closed = False

    @classmethod
    def from_config(cls, config: SecurityLabConfig) -> "DockerSandboxRunner":
        docker_binary = shutil.which("docker")
        if not docker_binary:
            raise DockerSandboxError(
                "SANDBOX_UNAVAILABLE", "Docker client is not installed"
            )
        return cls(config, docker_binary=docker_binary)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._runtime_root.cleanup()

    def preflight(self) -> DockerLabScope:
        self._ensure_open()
        daemon = self._json_command("info", "--format", "{{json .}}")
        target = self._single_inspect("container", self.config.target_container)
        worker_image = self._single_inspect("image", self.config.worker_image)
        self._validate_target(target)

        daemon_id = str(daemon.get("ID") or "").strip()
        target_id = str(target.get("Id") or "").strip()
        target_image_id = str(target.get("Image") or "").strip()
        worker_image_id = str(worker_image.get("Id") or "").strip()
        worker_digests = {str(item) for item in worker_image.get("RepoDigests") or []}
        if not all((daemon_id, target_id, target_image_id, worker_image_id)):
            raise DockerSandboxError(
                "SANDBOX_UNAVAILABLE", "Docker preflight returned incomplete identities"
            )
        if self.config.worker_image not in worker_digests:
            raise DockerSandboxError(
                "POLICY_DENIED", "Docker worker image digest does not match config"
            )
        config_fingerprint = _fingerprint(asdict(self.config))
        scope_payload = {
            "daemon_id": daemon_id,
            "target_container_id": target_id,
            "target_image_id": target_image_id,
            "worker_image_id": worker_image_id,
            "worker_image_digest": self.config.worker_image,
            "config_fingerprint": config_fingerprint,
        }
        return DockerLabScope(
            **scope_payload,
            resolved_scope_fingerprint=_fingerprint(scope_payload),
        )

    def run_exec(self, spec: ExecSpec, sandbox: ExecutionSandboxSpec) -> ExecResult:
        scope = self.preflight()
        if not isinstance(sandbox, SecurityLabExecutionSpec):
            raise DockerSandboxError(
                "POLICY_DENIED", "security lab execution scope is required"
            )
        execution_scope = sandbox.security_lab
        if execution_scope is None or (
            execution_scope.config_fingerprint != scope.config_fingerprint
            or execution_scope.daemon_id != scope.daemon_id
            or execution_scope.target_container_id != scope.target_container_id
            or execution_scope.target_image_id != scope.target_image_id
            or execution_scope.worker_image_digest != scope.worker_image_digest
        ):
            raise DockerSandboxError(
                "POLICY_DENIED", "security lab scope changed before execution"
            )
        if not spec.cmd or spec.cmd[0] not in self.config.executable_allowlist:
            raise DockerSandboxError(
                "POLICY_DENIED", "security lab executable is not allowlisted"
            )
        if spec.env:
            raise DockerSandboxError(
                "POLICY_DENIED",
                "security lab commands cannot receive environment values",
            )
        if spec.cwd != "/workspace":
            raise DockerSandboxError(
                "POLICY_DENIED", "security lab commands require fixed worker scratch"
            )
        timeout = min(float(sandbox.timeout_s), self.config.command_timeout_seconds)
        output_limit = min(int(sandbox.max_output_bytes), self.config.max_output_bytes)
        session_token = hashlib.sha256(execution_scope.session_id.encode()).hexdigest()[
            :12
        ]
        worker_name = f"openminion-security-lab-{session_token}"
        self._refuse_stale_worker(worker_name)
        worker_id = ""
        try:
            worker_id = self._create_worker(
                worker_name=worker_name,
                target_id=scope.target_container_id,
                argv=spec.cmd,
                session_id=execution_scope.session_id,
                activation_id=execution_scope.activation_id,
            )
            return self._start_worker(
                worker_id,
                scope=scope,
                spec=spec,
                timeout=timeout,
                output_limit=output_limit,
            )
        finally:
            if worker_id:
                self._command("rm", "--force", worker_id, check=False)

    def fs_write(self, spec: FsWriteSpec, sandbox: ExecutionSandboxSpec) -> FsResult:
        del sandbox
        return FsResult(False, spec.path, "security lab runner supports exec only")

    def fs_delete(self, spec: FsDeleteSpec, sandbox: ExecutionSandboxSpec) -> FsResult:
        del sandbox
        return FsResult(False, spec.path, "security lab runner supports exec only")

    def net_fetch(self, spec: NetFetchSpec, sandbox: ExecutionSandboxSpec) -> NetResult:
        del spec, sandbox
        return NetResult(0, b"", error="security lab runner supports exec only")

    def _create_worker(
        self,
        *,
        worker_name: str,
        target_id: str,
        argv: list[str],
        session_id: str,
        activation_id: str,
    ) -> str:
        tmpfs_size = min(self.config.memory_bytes, 16 * 1024 * 1024)
        completed = self._command(
            "create",
            "--name",
            worker_name,
            "--label",
            f"openminion.security_lab.config={self._config_fingerprint}",
            "--label",
            f"openminion.security_lab.session={session_id}",
            "--label",
            f"openminion.security_lab.activation={activation_id}",
            "--pull=never",
            "--no-healthcheck",
            "--log-driver=none",
            f"--network=container:{target_id}",
            "--entrypoint",
            argv[0],
            "--workdir=/workspace",
            "--user",
            f"{self.config.worker_uid}:{self.config.worker_gid}",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            f"--pids-limit={self.config.pids_limit}",
            f"--cpus={self.config.cpu_limit}",
            f"--memory={self.config.memory_bytes}",
            f"--tmpfs=/tmp:rw,noexec,nosuid,nodev,size={tmpfs_size}",
            f"--tmpfs=/workspace:rw,noexec,nosuid,nodev,size={tmpfs_size}",
            self.config.worker_image,
            *argv[1:],
        )
        worker_id = completed.stdout.decode(errors="replace").strip()
        if not worker_id:
            raise DockerSandboxError(
                "SANDBOX_UNAVAILABLE", "Docker did not return a worker container ID"
            )
        return worker_id

    @property
    def _config_fingerprint(self) -> str:
        return _fingerprint(asdict(self.config))

    def _refuse_stale_worker(self, worker_name: str) -> None:
        completed = self._command(
            "ps",
            "--all",
            "--filter",
            f"name=^{worker_name}$",
            "--format",
            "{{.ID}}",
        )
        if completed.stdout.strip():
            raise DockerSandboxError(
                "SANDBOX_UNAVAILABLE",
                "A stale security lab worker requires operator cleanup",
                {"worker_name": worker_name},
            )

    def _start_worker(
        self,
        worker_id: str,
        *,
        scope: DockerLabScope,
        spec: ExecSpec,
        timeout: float,
        output_limit: int,
    ) -> ExecResult:
        started_at = datetime.now(timezone.utc).isoformat()
        started = time.monotonic()
        process = subprocess.Popen(  # noqa: S603 - fixed Docker binary and argv
            [*self._base_command, "start", "--attach", worker_id],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self._supervisor_env,
        )
        stdout, stderr, observed, timed_out, output_limited = self._read_bounded(
            process,
            timeout=timeout,
            output_limit=output_limit,
        )
        argv_payload = json.dumps(
            spec.cmd, ensure_ascii=False, separators=(",", ":")
        ).encode()
        return SecurityLabExecResult(
            returncode=int(process.returncode or 0),
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
            timed_out=timed_out,
            stdout_bytes=stdout,
            stderr_bytes=stderr,
            stdout_observed_bytes=observed["stdout"],
            stderr_observed_bytes=observed["stderr"],
            stdout_truncated=observed["stdout"] > len(stdout),
            stderr_truncated=observed["stderr"] > len(stderr),
            output_limited=output_limited,
            terminated=timed_out or output_limited,
            execution_facts=SecurityLabExecutionFacts(
                runner=self.name,
                worker_container_id=worker_id,
                worker_image_digest=scope.worker_image_digest,
                daemon_id=scope.daemon_id,
                target_container_id=scope.target_container_id,
                target_image_id=scope.target_image_id,
                isolation_mode="target-network-namespace",
                started_at=started_at,
                duration_ms=max(0, int((time.monotonic() - started) * 1000)),
                executable_basename=Path(spec.cmd[0]).name,
                argument_count=len(spec.cmd),
                canonical_argv_sha256=hashlib.sha256(argv_payload).hexdigest(),
                timeout_seconds=timeout,
                max_output_bytes=output_limit,
                cpu_limit=self.config.cpu_limit,
                memory_bytes=self.config.memory_bytes,
                pids_limit=self.config.pids_limit,
            ),
        )

    @staticmethod
    def _read_bounded(
        process: subprocess.Popen[bytes],
        *,
        timeout: float,
        output_limit: int,
    ) -> tuple[bytes, bytes, dict[str, int], bool, bool]:
        assert process.stdout is not None
        assert process.stderr is not None
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        captured = {"stdout": bytearray(), "stderr": bytearray()}
        observed = {"stdout": 0, "stderr": 0}
        deadline = time.monotonic() + timeout
        timed_out = False
        output_limited = False
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                process.terminate()
                break
            for key, _ in selector.select(min(remaining, 0.1)):
                chunk = os.read(key.fd, 65_536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                stream_name = str(key.data)
                observed[stream_name] += len(chunk)
                stream = captured[stream_name]
                retained = len(captured["stdout"]) + len(captured["stderr"])
                stream.extend(chunk[: max(0, output_limit - retained)])
                if sum(observed.values()) > output_limit:
                    output_limited = True
                    process.terminate()
                    break
            if output_limited:
                break
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        return (
            bytes(captured["stdout"]),
            bytes(captured["stderr"]),
            observed,
            timed_out,
            output_limited,
        )

    def _validate_target(self, target: dict[str, Any]) -> None:
        state = target.get("State") or {}
        host = target.get("HostConfig") or {}
        config = target.get("Config") or {}
        network = target.get("NetworkSettings") or {}
        restart = host.get("RestartPolicy") or {}
        user = str(config.get("User") or "").split(":", 1)[0]
        security_options = {str(item) for item in host.get("SecurityOpt") or []}
        valid = (
            state.get("Running") is True
            and host.get("NetworkMode") == "none"
            and not network.get("Ports")
            and user.isdigit()
            and int(user) > 0
            and host.get("ReadonlyRootfs") is True
            and host.get("Privileged") is False
            and str(host.get("PidMode") or "") in {"", "private"}
            and str(host.get("IpcMode") or "") in {"", "private"}
            and not target.get("Mounts")
            and not host.get("Binds")
            and not host.get("Devices")
            and not config.get("Volumes")
            and {str(item).upper() for item in host.get("CapDrop") or []} == {"ALL"}
            and not host.get("CapAdd")
            and (
                int(host.get("NanoCpus") or 0) > 0
                or (
                    int(host.get("CpuQuota") or 0) > 0
                    and int(host.get("CpuPeriod") or 0) > 0
                )
            )
            and int(host.get("Memory") or 0) > 0
            and int(host.get("PidsLimit") or 0) > 0
            and "no-new-privileges" in security_options
            and str(restart.get("Name") or "no") in {"", "no"}
        )
        if not valid:
            raise DockerSandboxError(
                "POLICY_DENIED", "Docker target does not satisfy security lab hardening"
            )

    def _single_inspect(self, object_type: str, identity: str) -> dict[str, Any]:
        payload = self._json_command(object_type, "inspect", identity)
        if not isinstance(payload, list) or len(payload) != 1:
            raise DockerSandboxError(
                "SANDBOX_UNAVAILABLE", f"Docker {object_type} identity is unavailable"
            )
        return dict(payload[0])

    def _json_command(self, *args: str) -> Any:
        completed = self._command(*args)
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise DockerSandboxError(
                "SANDBOX_UNAVAILABLE", "Docker returned invalid preflight data"
            ) from exc

    def _command(
        self,
        *args: str,
        check: bool = True,
    ) -> subprocess.CompletedProcess[bytes]:
        self._ensure_open()
        try:
            completed = subprocess.run(  # noqa: S603 - fixed Docker binary and argv
                [*self._base_command, *args],
                check=False,
                capture_output=True,
                timeout=15,
                env=self._supervisor_env,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DockerSandboxError(
                "SANDBOX_UNAVAILABLE", "Docker command failed"
            ) from exc
        if (
            len(completed.stdout) + len(completed.stderr)
            > DOCKER_CONTROL_OUTPUT_LIMIT_BYTES
        ):
            raise DockerSandboxError(
                "SANDBOX_UNAVAILABLE", "Docker control output exceeded its limit"
            )
        if check and completed.returncode != 0:
            raise DockerSandboxError(
                "SANDBOX_UNAVAILABLE",
                completed.stderr.decode(errors="replace").strip()
                or "Docker command failed",
            )
        return completed

    @property
    def _base_command(self) -> list[str]:
        return [
            self._docker_binary,
            "--config",
            str(self._docker_config),
            "--host",
            self.config.daemon_socket,
        ]

    @property
    def _supervisor_env(self) -> dict[str, str]:
        return {
            "HOME": str(self._home),
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin",
            "LANG": "C",
            "LC_ALL": "C",
        }

    def _ensure_open(self) -> None:
        if self._closed:
            raise DockerSandboxError("SANDBOX_UNAVAILABLE", "Docker runner is closed")


__all__ = ["DockerLabScope", "DockerSandboxError", "DockerSandboxRunner"]
