import os
import sys
import tempfile
import pytest
from openminion.base.runtime.runners import LocalRunner
from openminion.base.runtime.sandbox import (
    ExecutionSandboxSpec,
    ExecSpec,
    FsWriteSpec,
    FsDeleteSpec,
)


def _sandbox(tmp_path, **overrides) -> ExecutionSandboxSpec:
    ws = str(tmp_path)
    defaults = dict(
        workspace_root=ws,
        read_allow=[ws],
        write_allow=[ws],
        delete_allow=[ws],
        cmd_allowlist=["echo", "python3.11", "python", sys.executable],
        timeout_s=10.0,
        max_output_bytes=65536,
        net_mode="deny",
    )
    defaults.update(overrides)
    return ExecutionSandboxSpec(**defaults)


runner = LocalRunner()


# exec tests


def test_exec_simple_command(tmp_path):
    sb = _sandbox(tmp_path)
    result = runner.run_exec(ExecSpec(cmd=["echo", "hello"]), sb)
    assert result.returncode == 0
    assert "hello" in result.stdout


def test_exec_blocked_command(tmp_path):
    sb = _sandbox(tmp_path, cmd_allowlist=["ls"])
    with pytest.raises(PermissionError, match="not in cmd_allowlist"):
        runner.run_exec(ExecSpec(cmd=["echo", "hi"]), sb)


def test_exec_empty_cmd_allowlist_denies_all(tmp_path):
    sb = _sandbox(tmp_path, cmd_allowlist=[])
    with pytest.raises(PermissionError, match="deny-all"):
        runner.run_exec(ExecSpec(cmd=["echo", "hi"]), sb)


def test_exec_cwd_outside_workspace_denied(tmp_path):
    sb = _sandbox(tmp_path)
    with pytest.raises(PermissionError, match="outside workspace_root"):
        runner.run_exec(ExecSpec(cmd=["echo", "hi"], cwd="/tmp"), sb)


def test_exec_cwd_inside_workspace_allowed(tmp_path):
    subdir = tmp_path / "sub"
    subdir.mkdir()
    sb = _sandbox(tmp_path)
    result = runner.run_exec(ExecSpec(cmd=["echo", "ok"], cwd=str(subdir)), sb)
    assert result.returncode == 0


def test_exec_timeout(tmp_path):
    sb = _sandbox(
        tmp_path, cmd_allowlist=["python3.11", "python", sys.executable], timeout_s=0.1
    )
    result = runner.run_exec(
        ExecSpec(cmd=[sys.executable, "-c", "import time; time.sleep(5)"]),
        sb,
    )
    assert result.timed_out is True
    assert result.returncode == -1


def test_exec_output_truncated(tmp_path):
    sb = _sandbox(tmp_path, max_output_bytes=10)
    result = runner.run_exec(
        ExecSpec(cmd=[sys.executable, "-c", "print('a' * 100)"]), sb
    )
    assert len(result.stdout) <= 10


def test_exec_env_filtered_by_allowlist(tmp_path):
    sb = _sandbox(tmp_path, env_allowlist=["MY_ALLOWED"])
    result = runner.run_exec(
        ExecSpec(
            cmd=[
                sys.executable,
                "-c",
                "import os; print(os.environ.get('MY_ALLOWED', 'MISSING'))",
            ],
            env={"MY_ALLOWED": "present", "MY_BLOCKED": "secret"},
        ),
        sb,
    )
    assert result.returncode == 0
    assert "present" in result.stdout


def test_exec_blocked_env_not_passed(tmp_path):
    sb = _sandbox(tmp_path, env_allowlist=["MY_ALLOWED"])
    result = runner.run_exec(
        ExecSpec(
            cmd=[
                sys.executable,
                "-c",
                "import os; print(os.environ.get('MY_BLOCKED', 'MISSING'))",
            ],
            env={"MY_ALLOWED": "ok", "MY_BLOCKED": "secret"},
        ),
        sb,
    )
    assert result.returncode == 0
    assert "MISSING" in result.stdout


# fs_write tests


def test_fs_write_allowed_path(tmp_path):
    target = tmp_path / "out.txt"
    sb = _sandbox(tmp_path)
    result = runner.fs_write(FsWriteSpec(path=str(target), content="hello"), sb)
    assert result.success is True
    assert target.read_text() == "hello"


def test_fs_write_outside_workspace_denied(tmp_path):
    sb = _sandbox(tmp_path, write_allow=[str(tmp_path)])
    with pytest.raises(PermissionError, match="outside allowed roots"):
        runner.fs_write(FsWriteSpec(path="/tmp/evil.txt", content="x"), sb)


def test_fs_write_bytes(tmp_path):
    target = tmp_path / "bin.dat"
    sb = _sandbox(tmp_path)
    result = runner.fs_write(FsWriteSpec(path=str(target), content=b"\x00\x01\x02"), sb)
    assert result.success is True
    assert target.read_bytes() == b"\x00\x01\x02"


def test_fs_write_creates_parent_dirs(tmp_path):
    target = tmp_path / "a" / "b" / "file.txt"
    sb = _sandbox(tmp_path)
    result = runner.fs_write(FsWriteSpec(path=str(target), content="nested"), sb)
    assert result.success is True
    assert target.read_text() == "nested"


# fs_delete tests


def test_fs_delete_allowed(tmp_path):
    target = tmp_path / "todelete.txt"
    target.write_text("bye")
    sb = _sandbox(tmp_path)
    result = runner.fs_delete(FsDeleteSpec(path=str(target)), sb)
    assert result.success is True
    assert not target.exists()


def test_fs_delete_outside_workspace_denied(tmp_path):
    sb = _sandbox(tmp_path, delete_allow=[str(tmp_path)])
    with pytest.raises(PermissionError, match="outside allowed roots"):
        runner.fs_delete(FsDeleteSpec(path="/tmp/evil.txt"), sb)


def test_fs_delete_nonexistent_returns_error(tmp_path):
    sb = _sandbox(tmp_path)
    result = runner.fs_delete(FsDeleteSpec(path=str(tmp_path / "ghost.txt")), sb)
    assert result.success is False
    assert result.error == "not_found"


def test_fs_delete_directory(tmp_path):
    subdir = tmp_path / "dir"
    subdir.mkdir()
    (subdir / "file.txt").write_text("inner")
    sb = _sandbox(tmp_path)
    result = runner.fs_delete(FsDeleteSpec(path=str(subdir)), sb)
    assert result.success is True
    assert not subdir.exists()


# net_fetch tests


def test_net_fetch_denied_when_net_mode_deny(tmp_path):
    sb = _sandbox(tmp_path, net_mode="deny")
    from openminion.base.runtime.sandbox import NetFetchSpec

    with pytest.raises(PermissionError, match="net_mode=deny"):
        runner.net_fetch(NetFetchSpec(url="https://example.com"), sb)


def test_net_fetch_domain_not_in_allowlist_denied(tmp_path):
    sb = _sandbox(tmp_path, net_mode="allow", allowed_domains=["trusted.com"])
    from openminion.base.runtime.sandbox import NetFetchSpec

    with pytest.raises(PermissionError, match="not in allowed_domains"):
        runner.net_fetch(NetFetchSpec(url="https://evil.com/api"), sb)


# Symlink escape tests (SPEC-X04)


def test_symlink_escape_denied(tmp_path):
    outside = tempfile.mkdtemp()
    try:
        link = tmp_path / "escape"
        os.symlink(outside, str(link))
        sb = _sandbox(tmp_path, write_allow=[str(tmp_path)])
        # Writing to symlink target that resolves outside workspace should be denied
        with pytest.raises(PermissionError):
            runner.fs_write(FsWriteSpec(path=str(link / "evil.txt"), content="x"), sb)
    finally:
        import shutil

        shutil.rmtree(outside, ignore_errors=True)
