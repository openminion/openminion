from __future__ import annotations

from pathlib import Path

from tests.e2e.tui.focus.harness.artifacts import artifact_root


def test_artifact_root_defaults_to_workspace_tmp(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("OPENMINION_TUI_FOCUS_E2E_ARTIFACT_ROOT", raising=False)

    root = artifact_root(tmp_path)

    assert root == (
            Path(__file__).resolve().parents[5]
            / "workspace-tmp"
            / "openminion-tui-focus-e2e"
            / tmp_path.parent.name
            / tmp_path.name
        )
    assert root.is_dir()
