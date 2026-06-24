"""Local and bubblewrap sandbox runners."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from urllib.parse import urlparse

from .interfaces import RUNTIME_INTERFACE_VERSION
from .sandbox import (
    ExecResult,
    ExecSpec,
    ExecutionSandboxSpec,
    FsDeleteSpec,
    FsResult,
    FsWriteSpec,
    NetFetchSpec,
    NetResult,
)
from .constants import RUNTIME_NET_MODE_DENY


def _realpath(path: str) -> str:
    try:
        return os.path.realpath(path)
    except Exception:
        return path


def _check_fs_path(path: str, allow_list: list[str]) -> None:
    real = _realpath(path)
    for allowed in allow_list:
        real_allowed = _realpath(allowed)
        if real == real_allowed or real.startswith(real_allowed + os.sep):
            return
    raise PermissionError(
        f"Path {path!r} (resolved: {real!r}) is outside allowed roots {allow_list!r}"
    )


def _check_cmd(cmd: list[str], allowlist: list[str]) -> None:
    if not allowlist:
        raise PermissionError(
            f"Command {cmd[0]!r} blocked: cmd_allowlist is empty (deny-all)"
        )
    executable = os.path.basename(cmd[0])
    if executable not in allowlist and cmd[0] not in allowlist:
        raise PermissionError(
            f"Command {cmd[0]!r} (basename: {executable!r}) not in cmd_allowlist {allowlist!r}"
        )


def _check_net(url: str, sandbox: ExecutionSandboxSpec) -> None:
    if sandbox.net_mode == RUNTIME_NET_MODE_DENY:
        raise PermissionError("Network access denied: net_mode=deny")
    if sandbox.allowed_domains:
        hostname = urlparse(url).hostname or ""
        if not any(
            hostname == d or hostname.endswith("." + d) for d in sandbox.allowed_domains
        ):
            raise PermissionError(
                f"Domain {hostname!r} not in allowed_domains {sandbox.allowed_domains!r}"
            )


def _filter_env(spec: ExecSpec, sandbox: ExecutionSandboxSpec) -> dict[str, str] | None:
    if not spec.env and not sandbox.env_allowlist:
        return None
    if sandbox.env_allowlist:
        return {k: v for k, v in spec.env.items() if k in sandbox.env_allowlist}
    return dict(spec.env)


def _check_cwd(cwd: str, workspace_root: str) -> str:
    real_cwd = _realpath(cwd)
    real_ws = _realpath(workspace_root)
    if real_cwd != real_ws and not real_cwd.startswith(real_ws + os.sep):
        raise PermissionError(
            f"cwd {cwd!r} (resolved: {real_cwd!r}) is outside workspace_root {workspace_root!r}"
        )
    return real_cwd


def _trim_exec_output(
    stdout: str, stderr: str, max_output_bytes: int
) -> tuple[str, str]:
    if not max_output_bytes:
        return stdout, stderr
    stdout = stdout[:max_output_bytes]
    return stdout, stderr[: max(0, max_output_bytes - len(stdout))]


class LocalRunner:
    name = "local"
    contract_version = RUNTIME_INTERFACE_VERSION

    def run_exec(self, spec: ExecSpec, sandbox: ExecutionSandboxSpec) -> ExecResult:
        _check_cmd(spec.cmd, sandbox.cmd_allowlist)
        cwd = spec.cwd or sandbox.workspace_root
        real_cwd = _check_cwd(cwd, sandbox.workspace_root)
        env = _filter_env(spec, sandbox)
        try:
            proc = subprocess.run(
                spec.cmd,
                cwd=real_cwd,
                env=env,
                input=spec.stdin,
                capture_output=True,
                text=True,
                timeout=sandbox.timeout_s,
            )
            stdout, stderr = _trim_exec_output(
                proc.stdout, proc.stderr, sandbox.max_output_bytes
            )
            return ExecResult(returncode=proc.returncode, stdout=stdout, stderr=stderr)
        except subprocess.TimeoutExpired:
            return ExecResult(returncode=-1, stdout="", stderr="", timed_out=True)

    def fs_write(self, spec: FsWriteSpec, sandbox: ExecutionSandboxSpec) -> FsResult:
        _check_fs_path(spec.path, sandbox.write_allow)
        real_path = _realpath(spec.path)
        try:
            parent = os.path.dirname(real_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            mode = "wb" if isinstance(spec.content, bytes) else "w"
            with open(real_path, mode) as fh:
                fh.write(spec.content)
            return FsResult(success=True, path=real_path)
        except Exception as exc:
            return FsResult(success=False, path=real_path, error=str(exc))

    def fs_delete(self, spec: FsDeleteSpec, sandbox: ExecutionSandboxSpec) -> FsResult:
        _check_fs_path(spec.path, sandbox.delete_allow)
        real_path = _realpath(spec.path)
        try:
            if os.path.isdir(real_path):
                shutil.rmtree(real_path)
            else:
                os.remove(real_path)
            return FsResult(success=True, path=real_path)
        except FileNotFoundError:
            return FsResult(success=False, path=real_path, error="not_found")
        except Exception as exc:
            return FsResult(success=False, path=real_path, error=str(exc))

    def net_fetch(self, spec: NetFetchSpec, sandbox: ExecutionSandboxSpec) -> NetResult:
        _check_net(spec.url, sandbox)
        import urllib.request

        try:
            req = urllib.request.Request(
                spec.url,
                method=spec.method,
                headers=spec.headers,
                data=spec.body,
            )
            with urllib.request.urlopen(req, timeout=sandbox.timeout_s) as resp:
                body = resp.read()
                if sandbox.max_output_bytes:
                    body = body[: sandbox.max_output_bytes]
                return NetResult(
                    status=resp.status,
                    body=body,
                    headers=dict(resp.headers),
                )
        except Exception as exc:
            return NetResult(status=0, body=b"", error=str(exc))


class BwrapRunner:
    name = "bwrap"
    contract_version = RUNTIME_INTERFACE_VERSION

    def __init__(self, bwrap_path: str = "bwrap") -> None:
        if platform.system() != "Linux":
            raise RuntimeError(
                f"BwrapRunner requires Linux; current platform is {platform.system()!r}"
            )
        resolved = shutil.which(bwrap_path)
        if resolved is None:
            raise RuntimeError(
                f"bubblewrap not found at {bwrap_path!r}; install bwrap and try again"
            )
        self._bwrap = resolved
        self._local = LocalRunner()

    def _build_bwrap_cmd(
        self, spec: ExecSpec, sandbox: ExecutionSandboxSpec
    ) -> list[str]:
        workspace_real = _realpath(sandbox.workspace_root)
        args: list[str] = [
            self._bwrap,
            "--unshare-net",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
        ]
        for sys_dir in ("/usr", "/lib", "/lib64", "/etc"):
            if os.path.exists(sys_dir):
                args += ["--ro-bind", sys_dir, sys_dir]
        args += ["--bind", workspace_real, workspace_real]
        for mount in sandbox.ro_mounts:
            real_mount = _realpath(mount)
            if os.path.exists(real_mount):
                args += ["--ro-bind", real_mount, real_mount]
        cwd = spec.cwd or sandbox.workspace_root
        real_cwd = _realpath(cwd)
        args += ["--chdir", real_cwd, "--"]
        args += spec.cmd
        return args

    def run_exec(self, spec: ExecSpec, sandbox: ExecutionSandboxSpec) -> ExecResult:
        _check_cmd(spec.cmd, sandbox.cmd_allowlist)
        env = _filter_env(spec, sandbox)
        bwrap_cmd = self._build_bwrap_cmd(spec, sandbox)
        try:
            proc = subprocess.run(
                bwrap_cmd,
                env=env,
                input=spec.stdin,
                capture_output=True,
                text=True,
                timeout=sandbox.timeout_s,
            )
            stdout, stderr = _trim_exec_output(
                proc.stdout, proc.stderr, sandbox.max_output_bytes
            )
            return ExecResult(returncode=proc.returncode, stdout=stdout, stderr=stderr)
        except subprocess.TimeoutExpired:
            return ExecResult(returncode=-1, stdout="", stderr="", timed_out=True)

    def fs_write(self, spec: FsWriteSpec, sandbox: ExecutionSandboxSpec) -> FsResult:
        return self._local.fs_write(spec, sandbox)

    def fs_delete(self, spec: FsDeleteSpec, sandbox: ExecutionSandboxSpec) -> FsResult:
        return self._local.fs_delete(spec, sandbox)

    def net_fetch(self, spec: NetFetchSpec, sandbox: ExecutionSandboxSpec) -> NetResult:
        return self._local.net_fetch(spec, sandbox)
