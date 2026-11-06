from __future__ import annotations

import os
from pathlib import Path
from runpy import run_path
import shutil
import subprocess
import sys

import pytest
import yaml

from openminion import tool
from openminion.base.config import OpenMinionConfig
from openminion.base.version import OPENMINION_VERSION
from openminion.modules.policy.runtime.action_policy import build_action_policy_service
from openminion.modules.tool.framework import build_registrar
from openminion.modules.tool.registry import ToolRegistry
from openminion.modules.tool import ToolExecutionContext
from openminion.modules.tool.contracts import ALL_MODEL_TOOL_IDS_SET
from openminion.services.runtime.plugins import discover_plugin_manifests

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "examples"
SDK_EXAMPLES = EXAMPLES / "sdk"
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


def test_sdk_examples_expose_help_without_provider_calls() -> None:
    scripts = sorted(SDK_EXAMPLES.glob("*.py"))

    assert {script.name for script in scripts} == {
        "application_runtime.py",
        "delegation.py",
        "error_handling.py",
        "provider_switching.py",
        "sessions.py",
        "streaming_progress.py",
        "structured_output.py",
        "submitted_stream.py",
        "tool_agent.py",
    }
    for script in scripts:
        result = _run([str(script), "--help"], env=_demo_env(ROOT / ".tmp-help"))
        assert "usage:" in result.stdout


def test_sdk_tool_example_uses_public_decorator_contract() -> None:
    module = run_path(str(SDK_EXAMPLES / "tool_agent.py"))
    tool_decl = module["lookup_order"].tool_decl

    assert tool_decl.name == "lookup_order"
    assert "order" in tool_decl.description.lower()


def _default_policy_decision(tool_fn, tmp_path: Path):
    registry = ToolRegistry()
    build_registrar(tool_fn.tool_family_spec()).register(registry)
    policy = build_action_policy_service(
        config=OpenMinionConfig(), tool_registry=registry, data_root=tmp_path
    )
    try:
        return policy.check(
            {
                "tool": "lookup_order",
                "method": "default",
                "args": {"order_id": "A-104"},
            },
            {},
        )
    finally:
        policy.close()


def _undeclared_lookup_order():
    @tool
    def lookup_order(order_id: str) -> str:
        """Return the current status for one order."""

        return f"order {order_id}: ready"

    return lookup_order


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("example", ("ALLOW", "READ_ONLY_ALLOW", "read", "reversible")),
        (
            "undeclared",
            ("REQUIRE_CONFIRM", "UNKNOWN_REVERSIBILITY", "write", "unknown"),
        ),
    ],
)
def test_sdk_tool_example_declares_read_only_lookup(tmp_path, source, expected):
    tool_fn = (
        run_path(str(SDK_EXAMPLES / "tool_agent.py"))["lookup_order"]
        if source == "example"
        else _undeclared_lookup_order()
    )
    decision = _default_policy_decision(tool_fn, tmp_path)

    assert (
        decision.decision,
        decision.reason_code,
        decision.risk.risk_class,
        decision.risk.reversibility,
    ) == expected


def test_sdk_error_example_handles_existing_turn_timeout(monkeypatch, capsys):
    from openminion import Agent, TurnTimeoutError

    class TimedOutRuntime:
        def run_turn(self, **kwargs):
            raise TurnTimeoutError("Turn execution timed out.")

    module = run_path(str(SDK_EXAMPLES / "error_handling.py"))
    main = module["main"]
    monkeypatch.setitem(
        main.__globals__,
        "Agent",
        lambda **kwargs: Agent(runtime=TimedOutRuntime(), **kwargs),
    )
    monkeypatch.setattr(sys, "argv", ["error_handling.py"])
    assert main() == 3
    assert "turn timed out: Turn execution timed out." in capsys.readouterr().out
