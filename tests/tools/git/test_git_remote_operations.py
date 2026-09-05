from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.registry import ToolRegistry
from openminion.tools.git import REGISTRAR
from openminion.tools.git.errors import (
    GIT_AUTH_FAILED,
    GIT_NON_FAST_FORWARD,
    GIT_REMOTE_NOT_FOUND,
    GIT_REMOTE_OUTCOME_UNCERTAIN,
)
from openminion.tools.git.remote import (
    GitFetchArgs,
    GitPushArgs,
    GitTagArgs,
    _h_fetch,
    _h_push,
    _h_tag,
)

_GIT = shutil.which("git")


def _run(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_GIT, *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def _init_repo(path: Path) -> None:
    path.mkdir()
    _run(["init", "-q", "-b", "main"], cwd=path)
    _run(["config", "user.email", "test@example.com"], cwd=path)
    _run(["config", "user.name", "Test User"], cwd=path)
    _run(["config", "commit.gpgsign", "false"], cwd=path)
    (path / "README.md").write_text("initial\n", encoding="utf-8")
    _run(["add", "README.md"], cwd=path)
    _run(["commit", "-q", "-m", "initial"], cwd=path)


def _remote_repo(tmp_path: Path) -> tuple[Path, Path]:
    remote = tmp_path / "remote.git"
    remote.mkdir()
    _run(["init", "-q", "--bare", "-b", "main"], cwd=remote)
    local = tmp_path / "local"
    _init_repo(local)
    _run(["remote", "add", "origin", str(remote)], cwd=local)
    return local, remote


def _ctx(workspace: Path) -> SimpleNamespace:
    return SimpleNamespace(
        policy=SimpleNamespace(
            raw={"workspace_root": str(workspace)},
            ensure_path_allowed=lambda *args, **kwargs: None,
        ),
        workspace=workspace,
        env={},
    )


@pytest.mark.skipif(_GIT is None, reason="git binary not on PATH")
def test_fetch_push_and_annotated_tag_round_trip(tmp_path: Path) -> None:
    local, remote = _remote_repo(tmp_path)
    context = _ctx(local)

    pushed = _h_push(
        {
            "remote": "origin",
            "source_ref": "refs/heads/main",
            "target_ref": "refs/heads/main",
        },
        context,
    )
    head = _run(["rev-parse", "HEAD"], cwd=local).stdout.strip()
    assert pushed["parsed"]["remote_oid"] == head

    clone = tmp_path / "clone"
    _run(["clone", "-q", str(remote), str(clone)], cwd=tmp_path)
    fetched = _h_fetch(
        {"remote": "origin", "ref": "refs/heads/main"},
        context,
    )
    assert fetched["parsed"]["fetched_oid"] == head

    created = _h_tag(
        {
            "action": "create",
            "name": "v1.0.0-rc1",
            "target_ref": "refs/heads/main",
            "message": "Release candidate 1",
        },
        context,
    )
    assert created["parsed"]["target_oid"] == head
    assert _run(["cat-file", "-t", "v1.0.0-rc1"], cwd=local).stdout.strip() == "tag"
    listed = _h_tag({"action": "list", "name": "v1.0.0-rc1"}, context)
    assert listed["parsed"]["tags"] == [
        {
            "name": "v1.0.0-rc1",
            "oid": created["parsed"]["oid"],
            "annotated": True,
            "target_oid": head,
            "message": "Release candidate 1",
        }
    ]
    published = _h_tag(
        {"action": "push", "name": "v1.0.0-rc1", "remote": "origin"},
        context,
    )
    assert published["parsed"]["remote_oid"] == created["parsed"]["oid"]
    assert published["parsed"]["remote_target_oid"] == head


@pytest.mark.skipif(_GIT is None, reason="git binary not on PATH")
def test_push_denies_non_fast_forward(tmp_path: Path) -> None:
    local, remote = _remote_repo(tmp_path)
    context = _ctx(local)
    _h_push(
        {
            "remote": "origin",
            "source_ref": "refs/heads/main",
            "target_ref": "refs/heads/main",
        },
        context,
    )
    clone = tmp_path / "clone"
    _run(["clone", "-q", str(remote), str(clone)], cwd=tmp_path)
    _run(["config", "user.email", "other@example.com"], cwd=clone)
    _run(["config", "user.name", "Other User"], cwd=clone)
    (clone / "other.txt").write_text("remote\n", encoding="utf-8")
    _run(["add", "other.txt"], cwd=clone)
    _run(["commit", "-q", "-m", "remote update"], cwd=clone)
    _run(["push", "-q", "origin", "main"], cwd=clone)
    (local / "local.txt").write_text("local\n", encoding="utf-8")
    _run(["add", "local.txt"], cwd=local)
    _run(["commit", "-q", "-m", "local update"], cwd=local)

    with pytest.raises(ToolRuntimeError) as caught:
        _h_push(
            {
                "remote": "origin",
                "source_ref": "refs/heads/main",
                "target_ref": "refs/heads/main",
            },
            context,
        )

    assert caught.value.code == GIT_NON_FAST_FORWARD


@pytest.mark.skipif(_GIT is None, reason="git binary not on PATH")
def test_missing_remote_and_authentication_failures_are_typed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    local, _ = _remote_repo(tmp_path)
    with pytest.raises(ToolRuntimeError) as missing:
        _h_fetch(
            {"remote": "missing", "ref": "refs/heads/main"},
            _ctx(local),
        )
    assert missing.value.code == GIT_REMOTE_NOT_FOUND

    denied_ssh = tmp_path / "denied-ssh"
    denied_ssh.write_text(
        "#!/bin/sh\necho 'Permission denied (publickey).' >&2\nexit 255\n",
        encoding="utf-8",
    )
    denied_ssh.chmod(0o700)
    _run(["remote", "add", "denied", "ssh://example.invalid/repo"], cwd=local)
    monkeypatch.setenv("GIT_SSH_COMMAND", str(denied_ssh))
    with pytest.raises(ToolRuntimeError) as auth:
        _h_fetch(
            {"remote": "denied", "ref": "refs/heads/main"},
            _ctx(local),
        )
    assert auth.value.code == GIT_AUTH_FAILED


@pytest.mark.skipif(_GIT is None, reason="git binary not on PATH")
def test_push_timeout_is_uncertain_and_not_retried(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    local, _ = _remote_repo(tmp_path)
    from openminion.tools.git import remote as git_remote

    original = git_remote.run_git
    push_calls = 0

    def timeout_push(args, **kwargs):
        nonlocal push_calls
        if args[0] == "push":
            push_calls += 1
            raise subprocess.TimeoutExpired(args, 30)
        return original(args, **kwargs)

    monkeypatch.setattr(git_remote, "run_git", timeout_push)
    with pytest.raises(ToolRuntimeError) as caught:
        _h_push(
            {
                "remote": "origin",
                "source_ref": "refs/heads/main",
                "target_ref": "refs/heads/main",
            },
            _ctx(local),
        )
    assert caught.value.code == GIT_REMOTE_OUTCOME_UNCERTAIN
    assert push_calls == 1


def test_remote_argument_models_exclude_unsafe_and_out_of_scope_inputs() -> None:
    for extra in ("force", "delete", "credential", "repository"):
        with pytest.raises(ValidationError):
            GitPushArgs.model_validate(
                {
                    "remote": "origin",
                    "source_ref": "refs/heads/main",
                    "target_ref": "refs/heads/main",
                    extra: True,
                }
            )
    with pytest.raises(ValidationError):
        GitTagArgs.model_validate({"action": "delete", "name": "v1"})
    with pytest.raises(ValidationError):
        GitPushArgs.model_validate(
            {
                "remote": "https://token@example.invalid/repo",
                "source_ref": "+refs/heads/main",
                "target_ref": "refs/heads/main:",
            }
        )
    assert (
        GitFetchArgs.model_validate(
            {"remote": "origin", "ref": "refs/heads/main"}
        ).remote
        == "origin"
    )


def test_git_manifest_owns_remote_contract_chain() -> None:
    manifest = REGISTRAR.get_manifest(None)
    assert manifest is not None
    bindings = {
        binding.model_tool_id: (
            binding.runtime_binding_id,
            binding.runtime_candidates,
        )
        for binding in manifest.runtime_bindings
    }
    assert bindings["git.fetch"] == ("runtime.git.fetch", ("git.fetch",))
    assert bindings["git.push"] == ("runtime.git.push", ("git.push",))
    assert bindings["git.tag"] == ("runtime.git.tag", ("git.tag",))
    registry = ToolRegistry([])
    REGISTRAR.register(registry)
    assert {"git.fetch", "git.push", "git.tag"} <= set(registry.list())
