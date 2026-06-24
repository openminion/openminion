from __future__ import annotations

import pytest

from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.runtime.policy import Policy
from openminion.modules.tool.runtime import RuntimeContext
from openminion.modules.tool.runtime.tools_core import (
    h_fs_copy,
    h_fs_list_dir,
    h_fs_search,
)


def _ctx(tmp_path, workspace):
    run_root = tmp_path / "run"
    run_root.mkdir(parents=True, exist_ok=True)
    policy = Policy(
        raw={
            "workspace_root": str(tmp_path / "runs"),
            "paths": {
                "read_allow": [str(workspace)],
                "write_allow": [str(workspace)],
                "deny": [],
            },
            "commands": {"mode": "allowlist", "allow": ["echo"]},
        }
    )
    return RuntimeContext(
        policy=policy,
        workspace=workspace,
        run_root=run_root,
        scope="WRITE_SAFE",
        confirm=False,
    )


def test_fs_list_dir_recursive_does_not_traverse_symlink_dirs(tmp_path):
    workspace = tmp_path / "ws"
    safe = workspace / "safe"
    outside = tmp_path / "outside"
    safe.mkdir(parents=True)
    outside.mkdir(parents=True)

    (outside / "secret.txt").write_text("secret")
    try:
        (safe / "leak").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Symlink creation not available in this environment")

    ctx = _ctx(tmp_path, workspace.resolve(strict=False))
    result = h_fs_list_dir(
        {"path": str(safe), "recursive": True, "include_hidden": True},
        ctx,
    )

    entries = result["entries"]
    paths = [item["path"] for item in entries]
    # Policy-denied symlink escapes are skipped entirely.
    assert all(item["name"] != "leak" for item in entries)
    assert not any(path.endswith("secret.txt") for path in paths)


def test_fs_search_skips_symlinked_files(tmp_path):
    workspace = tmp_path / "ws"
    safe = workspace / "safe"
    outside = tmp_path / "outside"
    safe.mkdir(parents=True)
    outside.mkdir(parents=True)

    secret = outside / "secret.txt"
    secret.write_text("needle")
    try:
        (safe / "leak.txt").symlink_to(secret)
    except OSError:
        pytest.skip("Symlink creation not available in this environment")

    ctx = _ctx(tmp_path, workspace.resolve(strict=False))
    result = h_fs_search(
        {"root": str(safe), "query": "needle", "regex": False},
        ctx,
    )

    assert result["matches"] == []
    assert result["count"] == 0


def test_fs_search_invalid_regex_returns_invalid_argument(tmp_path):
    workspace = tmp_path / "ws"
    safe = workspace / "safe"
    safe.mkdir(parents=True)
    (safe / "a.txt").write_text("sample")

    ctx = _ctx(tmp_path, workspace.resolve(strict=False))
    with pytest.raises(ToolRuntimeError) as excinfo:
        h_fs_search({"root": str(safe), "query": "[unterminated", "regex": True}, ctx)

    assert excinfo.value.code == "INVALID_ARGUMENT"


def test_fs_copy_directory_does_not_dereference_symlink_targets(tmp_path):
    workspace = tmp_path / "ws"
    src = workspace / "src"
    dst = workspace / "dst"
    outside = tmp_path / "outside"
    src.mkdir(parents=True)
    outside.mkdir(parents=True)

    secret = outside / "secret.txt"
    secret.write_text("secret")
    try:
        (src / "leak.txt").symlink_to(secret)
    except OSError:
        pytest.skip("Symlink creation not available in this environment")

    ctx = _ctx(tmp_path, workspace.resolve(strict=False))
    h_fs_copy(
        {
            "src": str(src),
            "dst": str(dst),
            "overwrite": False,
            "recursive": True,
            "preserve_metadata": False,
        },
        ctx,
    )

    copied = dst / "leak.txt"
    assert copied.exists()
    assert copied.is_symlink()
