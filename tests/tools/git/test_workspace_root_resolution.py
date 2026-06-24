from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from openminion.modules.tool.contracts.schemas import Scope
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.runtime import RuntimeContext
from openminion.modules.tool.runtime.policy import Policy
from openminion.tools.git.runtime import resolve_git_repo_root

_GIT = shutil.which("git")


def _run(cmd: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        cmd,
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )


def _init_repo(root: Path) -> None:
    _run([_GIT, "init", "-q", "-b", "main"], cwd=root)
    _run([_GIT, "config", "user.email", "test@example.com"], cwd=root)
    _run([_GIT, "config", "user.name", "Test User"], cwd=root)
    (root / "README.md").write_text("hello\n", encoding="utf-8")
    _run([_GIT, "add", "README.md"], cwd=root)
    _run([_GIT, "commit", "-q", "-m", "seed"], cwd=root)


def _ctx(
    workspace: Path,
    *,
    cwd: Path | None = None,
) -> RuntimeContext:
    raw: dict[str, object] = {"workspace_root": str(workspace)}
    if cwd is not None:
        raw["context_metadata"] = {"cwd": str(cwd)}
    policy = Policy(raw=raw)
    run_root = workspace / ".tmp-gtwr-run"
    run_root.mkdir(parents=True, exist_ok=True)
    scope: Scope = "WRITE_SAFE"
    return RuntimeContext(
        policy=policy,
        workspace=workspace,
        run_root=run_root,
        scope=scope,
        confirm=False,
    )


@pytest.mark.skipif(_GIT is None, reason="git binary not on PATH")
def test_resolves_when_seed_is_git_repo(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    ctx = _ctx(tmp_path, cwd=tmp_path)

    assert resolve_git_repo_root(ctx) == tmp_path.resolve(strict=False)


@pytest.mark.skipif(_GIT is None, reason="git binary not on PATH")
def test_resolves_repo_from_context_metadata_cwd(tmp_path: Path) -> None:
    openminion_repo = tmp_path / "openminion"
    docs_repo = tmp_path / "docs"
    openminion_repo.mkdir()
    docs_repo.mkdir()
    _init_repo(openminion_repo)
    _init_repo(docs_repo)

    ctx = _ctx(tmp_path, cwd=openminion_repo)
    assert resolve_git_repo_root(ctx) == openminion_repo.resolve(strict=False)


@pytest.mark.skipif(_GIT is None, reason="git binary not on PATH")
def test_emits_git_not_a_repository_when_no_git_found(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, cwd=tmp_path)
    with pytest.raises(ToolRuntimeError) as excinfo:
        resolve_git_repo_root(ctx)
    assert excinfo.value.code == "GIT_NOT_A_REPOSITORY"
    assert excinfo.value.details["candidate"] == str(tmp_path.resolve(strict=False))
    assert (
        str(tmp_path.resolve(strict=False)) in excinfo.value.details["searched_paths"]
    )


@pytest.mark.skipif(_GIT is None, reason="git binary not on PATH")
def test_emits_git_ambiguous_workspace_with_named_candidates(tmp_path: Path) -> None:
    openminion_repo = tmp_path / "openminion"
    docs_repo = tmp_path / "docs"
    openminion_repo.mkdir()
    docs_repo.mkdir()
    _init_repo(openminion_repo)
    _init_repo(docs_repo)

    ctx = _ctx(tmp_path)
    with pytest.raises(ToolRuntimeError) as excinfo:
        resolve_git_repo_root(ctx)
    assert excinfo.value.code == "GIT_AMBIGUOUS_WORKSPACE"
    candidates = excinfo.value.details["candidates"]
    assert str(openminion_repo.resolve(strict=False)) in candidates
    assert str(docs_repo.resolve(strict=False)) in candidates
    assert excinfo.value.details["candidate"] == str(tmp_path.resolve(strict=False))
