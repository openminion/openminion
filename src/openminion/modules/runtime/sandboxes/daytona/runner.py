from typing import Any
import uuid

from openminion.base.runtime.interfaces import RUNTIME_INTERFACE_VERSION
from openminion.base.runtime.runners import (
    LocalRunner,
    _check_cmd,
    _check_cwd,
)
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
from openminion.modules.runtime.constants import SANDBOX_RESOURCE_LIMIT
from .client import DaytonaClient, DaytonaClientError
from .session import DaytonaSessionManager


def _workspace_name() -> str:
    return f"openminion-{uuid.uuid4().hex[:12]}"


def _filter_exec_env(
    env: dict[str, str],
    *,
    allowlist: list[str],
) -> dict[str, str]:
    if not allowlist:
        return {}
    allowed = {str(name).strip() for name in allowlist if str(name).strip()}
    filtered = {key: value for key, value in env.items() if key in allowed}
    return {
        key: value
        for key, value in filtered.items()
        if not key.startswith("OPENMINION_") or key in allowed
    }


class DaytonaRunner:
    """Concrete sandbox runner backed by the Daytona client."""

    name = "daytona"
    contract_version = RUNTIME_INTERFACE_VERSION

    def __init__(
        self,
        *,
        client: DaytonaClient,
        local_runner: LocalRunner | None = None,
    ) -> None:
        self._client = client
        self._local = local_runner or LocalRunner()
        self._sessions = DaytonaSessionManager(client=client)

    def run_exec(self, spec: ExecSpec, sandbox: ExecutionSandboxSpec) -> ExecResult:
        _check_cmd(spec.cmd, sandbox.cmd_allowlist)
        cwd = spec.cwd or sandbox.workspace_root
        real_cwd = _check_cwd(cwd, sandbox.workspace_root)
        filtered_env = _filter_exec_env(spec.env, allowlist=sandbox.env_allowlist)
        workspace = self._create_workspace_for_exec(sandbox=sandbox)
        try:
            if not self._client.connected:
                self._client.open()
            result = self._client.execute_command(
                workspace_id=workspace.workspace_id,
                command=list(spec.cmd),
                cwd=real_cwd,
                env=filtered_env,
                env_allowlist=sandbox.env_allowlist,
                timeout_s=sandbox.timeout_s,
                max_output_bytes=sandbox.max_output_bytes,
            )
            return ExecResult(
                returncode=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                timed_out=result.timed_out,
            )
        except DaytonaClientError as exc:
            if exc.code == SANDBOX_RESOURCE_LIMIT:
                return ExecResult(
                    returncode=-1,
                    stdout="",
                    stderr=exc.message,
                    timed_out=True,
                )
            raise
        finally:
            try:
                self._client.destroy_workspace(workspace.workspace_id)
            except DaytonaClientError:
                pass

    def fs_write(self, spec: FsWriteSpec, sandbox: ExecutionSandboxSpec) -> FsResult:
        return self._local.fs_write(spec, sandbox)

    def fs_delete(self, spec: FsDeleteSpec, sandbox: ExecutionSandboxSpec) -> FsResult:
        return self._local.fs_delete(spec, sandbox)

    def net_fetch(self, spec: NetFetchSpec, sandbox: ExecutionSandboxSpec) -> NetResult:
        return self._local.net_fetch(spec, sandbox)

    def close(self) -> None:
        self._sessions.close()
        if not self._client.connected:
            return
        self._client.close()

    @property
    def session_manager(self) -> DaytonaSessionManager:
        return self._sessions

    def _create_workspace_for_exec(
        self,
        *,
        sandbox: ExecutionSandboxSpec,
    ) -> Any:
        metadata = {
            "workspace_root": sandbox.workspace_root,
            "read_allow": list(sandbox.read_allow),
            "write_allow": list(sandbox.write_allow),
            "delete_allow": list(sandbox.delete_allow),
            "cmd_allowlist": list(sandbox.cmd_allowlist),
            "env_allowlist": list(sandbox.env_allowlist),
            "timeout_s": sandbox.timeout_s,
            "max_output_bytes": sandbox.max_output_bytes,
            "address_space_bytes": sandbox.address_space_bytes,
            "cpu_seconds": sandbox.cpu_seconds,
            "session_mode": sandbox.session_mode,
            "net_mode": sandbox.net_mode,
            "allowed_domains": list(sandbox.allowed_domains),
            "idempotency_key": sandbox.idempotency_key,
        }
        return self._client.create_workspace(
            name=_workspace_name(),
            metadata=metadata,
        )


__all__ = ["DaytonaRunner"]
