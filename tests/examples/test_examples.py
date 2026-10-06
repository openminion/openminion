from __future__ import annotations

import os
from pathlib import Path
from runpy import run_path
import shutil
import subprocess
import sys

import yaml

from openminion.base.version import OPENMINION_VERSION
from openminion.modules.tool import ToolExecutionContext
from openminion.modules.tool.contracts import ALL_MODEL_TOOL_IDS_SET
from openminion.services.runtime.plugins import discover_plugin_manifests

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "examples"
hello_tool = run_path(str(EXAMPLES / "starter" / "tool.py"))["hello_tool"]


def _demo_env(home: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["OPENMINION_HOME"] = str(home)
    env["OPENMINION_DATA_ROOT"] = str(home / "data")
    env["PYTHONPATH"] = str(ROOT / "src")
    return env


def _run(
    args: list[str],
    *,
    env: dict[str, str],
    cwd: Path = ROOT,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args],
        cwd=cwd,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def _init_demo(home: Path) -> dict[str, str]:
    env = _demo_env(home)
    _run(["-m", "openminion", "config", "init", "--provider", "echo"], env=env)
    return env


def test_starter_plugin_is_discoverable() -> None:
    discovered = discover_plugin_manifests([EXAMPLES / "starter"])

    assert len(discovered) == 1
    assert discovered[0].module_alias == "hello"
    assert discovered[0].manifest.id == "example.hello"
    assert discovered[0].manifest.version == OPENMINION_VERSION


def test_starter_tool_uses_current_execution_contract() -> None:
    result = hello_tool.tool_decl.handler(
        {"name": "developer"},
        ToolExecutionContext(channel="console", target="test"),
    )

    assert hello_tool.tool_decl.name == "hello_tool"
    assert result == "hello developer"


def test_quickstart_runs_with_fresh_demo_config(tmp_path: Path) -> None:
    env = _init_demo(tmp_path / "quickstart-home")
    script = tmp_path / "quickstart.py"
    shutil.copy(EXAMPLES / "starter" / "quickstart.py", script)

    result = _run(
        [str(script), "hello", "example"],
        env=env,
        cwd=tmp_path,
    )

    assert "reply:" in result.stdout
    assert "hello example" in result.stdout


def test_identity_examples_run_from_fresh_demo_config(tmp_path: Path) -> None:
    env = _init_demo(tmp_path / "identity-home")

    _run(
        [
            "-m",
            "openminion",
            "identity",
            "upsert",
            str(EXAMPLES / "identity" / "sample.yaml"),
        ],
        env=env,
    )
    rendered = _run(
        [
            "-m",
            "openminion",
            "identity",
            "render",
            "sample",
            "--purpose",
            "act",
            "--max-tokens",
            "180",
        ],
        env=env,
    )
    _run(
        [
            "-m",
            "openminion",
            "identity",
            "import",
            "--from-bundle",
            str(EXAMPLES / "agents" / "hello"),
        ],
        env=env,
    )

    assert "Provide pragmatic, accurate" in rendered.stdout


def test_identity_sample_uses_canonical_model_tool_ids() -> None:
    payload = yaml.safe_load((EXAMPLES / "identity" / "sample.yaml").read_text())
    tools = payload["profiles"]["sample"]["tool_posture"]["allowed_tools"]

    assert set(tools) <= ALL_MODEL_TOOL_IDS_SET
