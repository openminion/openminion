from pathlib import Path

from tests.helpers.live_e2e_profiles import resolve_live_framework_root


def test_live_framework_root_defaults_to_package_parent(
    monkeypatch, tmp_path: Path
) -> None:
    package_root = tmp_path / "openminion"
    monkeypatch.delenv("OPENMINION_LIVE_E2E_FRAMEWORK_ROOT", raising=False)

    assert resolve_live_framework_root(package_root) == tmp_path


def test_live_framework_root_honors_explicit_workspace(
    monkeypatch, tmp_path: Path
) -> None:
    package_root = tmp_path / "worktrees" / "openminion"
    workspace_root = tmp_path / "workspace"
    monkeypatch.setenv("OPENMINION_LIVE_E2E_FRAMEWORK_ROOT", str(workspace_root))

    assert resolve_live_framework_root(package_root) == workspace_root


def test_live_framework_root_finds_workspace_around_nested_worktree(
    monkeypatch, tmp_path: Path
) -> None:
    workspace_root = tmp_path / "workspace"
    package_root = workspace_root / "workspace-tmp" / "run" / "openminion"
    package_root.mkdir(parents=True)
    (workspace_root / "test-configs").mkdir()
    (workspace_root / "workspace-tmp" / "test-configs").mkdir()
    monkeypatch.delenv("OPENMINION_LIVE_E2E_FRAMEWORK_ROOT", raising=False)

    assert resolve_live_framework_root(package_root) == workspace_root
