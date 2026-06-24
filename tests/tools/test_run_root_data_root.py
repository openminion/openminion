from pathlib import Path

from openminion.modules.tool.base import ToolExecutionContext
from openminion.modules.tool.registry import ToolRegistry


def _safe_session_id(session_id: str) -> str:
    safe = "".join(
        ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in session_id
    ).strip("-")
    return safe or "default"


def test_tool_run_root_uses_data_root(monkeypatch, tmp_path: Path) -> None:
    data_root = tmp_path / "data-root"
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(data_root))

    context = ToolExecutionContext(
        channel="console",
        target="unit-test",
        session_id="artifact smoke",
    )

    run_root = ToolRegistry._resolve_run_root(workspace=workspace, context=context)
    expected = (
        data_root / "tool-runtime" / "sessions" / _safe_session_id(context.session_id)
    )

    assert run_root == expected
    assert (run_root / "artifacts").is_dir()


def test_tool_run_root_uses_openminion_home_when_data_root_unset(
    monkeypatch, tmp_path: Path
) -> None:
    home_root = tmp_path / "home-root"
    workspace = tmp_path / "nested-workspace"
    home_root.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("OPENMINION_HOME", str(home_root))
    monkeypatch.delenv("OPENMINION_DATA_ROOT", raising=False)

    context = ToolExecutionContext(
        channel="console",
        target="unit-test",
        session_id="nested workspace",
    )

    run_root = ToolRegistry._resolve_run_root(workspace=workspace, context=context)
    expected = (
        home_root
        / ".openminion"
        / "tool-runtime"
        / "sessions"
        / _safe_session_id(context.session_id)
    )

    assert run_root == expected
    assert (run_root / "artifacts").is_dir()
