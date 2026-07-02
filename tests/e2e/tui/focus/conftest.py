from __future__ import annotations

import os
from pathlib import Path
import re

import pytest

from tests.e2e.tui.focus.harness import FocusProbe
from tests.e2e.tui.focus.harness.artifacts import artifact_root


@pytest.fixture(scope="session")
def openminion_root() -> Path:
    return Path(__file__).resolve().parents[4]


@pytest.fixture(scope="session")
def framework_root(openminion_root: Path) -> Path:
    return openminion_root.parent


@pytest.fixture(scope="session")
def python_bin(openminion_root: Path) -> Path:
    local = openminion_root / ".venv" / "bin" / "python3.11"
    return local if local.exists() else Path(os.getenv("OPENMINION_PYTHON", "python3.11"))


@pytest.fixture(scope="session")
def minimax_config_path(framework_root: Path) -> Path:
    override = str(os.getenv("OPENMINION_TUI_FOCUS_E2E_CONFIG", "")).strip()
    if override:
        return Path(override).expanduser()
    return framework_root / "test-configs" / "per-agent-minimax-official.json"


@pytest.fixture(scope="session")
def minimax_agent_id() -> str:
    return str(os.getenv("OPENMINION_TUI_FOCUS_E2E_AGENT", "minimax-m2-7")).strip()


@pytest.fixture
def focus_probe(
    *,
    request: pytest.FixtureRequest,
    tmp_path: Path,
    python_bin: Path,
    openminion_root: Path,
    framework_root: Path,
    minimax_config_path: Path,
    minimax_agent_id: str,
) -> FocusProbe:
    if not minimax_config_path.exists():
        pytest.skip(f"missing MiniMax focus config: {minimax_config_path}")
    if os.name != "posix":
        pytest.skip("focus PTY E2E harness requires a POSIX platform")
    run_root = artifact_root(tmp_path)
    node_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", request.node.name).strip("-")
    data_root = run_root / "data" / (node_name or "focus-e2e")
    data_root.mkdir(parents=True, exist_ok=True)
    return FocusProbe(
        python_bin=python_bin,
        openminion_root=openminion_root,
        framework_root=framework_root,
        data_root=data_root,
        config_path=minimax_config_path,
        agent_id=minimax_agent_id,
        workdir=openminion_root,
    )


def require_live_focus() -> None:
    if str(os.getenv("OPENMINION_LIVE_TUI_FOCUS_E2E", "")).strip() != "1":
        pytest.skip(
            "OPENMINION_LIVE_TUI_FOCUS_E2E=1 not set; skipping live focus E2E."
        )


def require_complex_focus() -> None:
    require_live_focus()
    if str(os.getenv("OPENMINION_LIVE_TUI_FOCUS_COMPLEX_E2E", "")).strip() != "1":
        pytest.skip(
            "OPENMINION_LIVE_TUI_FOCUS_COMPLEX_E2E=1 not set; skipping complex focus E2E."
        )
