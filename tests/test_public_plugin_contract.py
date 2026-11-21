from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path

from openminion import APIRuntime
from openminion.base.config import OpenMinionConfig, save_config
from tests._csc_fixtures import _csc_install_default_agent


def _write_plugin(root: Path) -> None:
    root.mkdir()
    (root / "public_example.py").write_text(
        """from pydantic import BaseModel

from openminion.tools import (
    Plugin,
    ToolDecl,
    ToolDependencyDecl,
    ToolDependencyStatus,
    ToolFamilySpec,
    build_registrar,
)


class EchoArgs(BaseModel):
    text: str


def echo(args, context):
    del context
    return {"text": args["text"]}


def missing_dependency(context):
    del context
    return ToolDependencyStatus(
        dependency_id="example:cli",
        state="missing",
        reason_code="binary_not_found",
        message="example CLI is not available",
    )


REGISTRAR = build_registrar(
    ToolFamilySpec(
        module_id="example.public",
        tools=(
            ToolDecl(
                name="plugin.example.public.echo",
                args_model=EchoArgs,
                handler=echo,
                description="Echo one value.",
                dangerous=True,
                dependencies=(
                    ToolDependencyDecl(
                        dependency_id="example:cli",
                        probe=missing_dependency,
                    ),
                ),
            ),
        ),
    )
)


class PublicExamplePlugin(Plugin):
    name = "public-example"
""",
        encoding="utf-8",
    )
    (root / "public_example.manifest.json").write_text(
        json.dumps(
            {
                "id": "example.public",
                "name": "Public example",
                "version": "1.0.0",
                "contract_version": "1",
                "description": "Public plugin contract fixture.",
                "config_schema": {"type": "object"},
                "trust_tier": "local-dev",
                "requested_capabilities": [],
                "provenance": {"source": "local-path", "verified": False},
            }
        ),
        encoding="utf-8",
    )


@contextmanager
def _plugin_path(root: Path):
    previous = os.environ.get("OPENMINION_PLUGIN_PATHS")
    os.environ["OPENMINION_PLUGIN_PATHS"] = str(root)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("OPENMINION_PLUGIN_PATHS", None)
        else:
            os.environ["OPENMINION_PLUGIN_PATHS"] = previous


def _runtime(tmp_path: Path, *, enabled: bool) -> APIRuntime:
    config = OpenMinionConfig()
    _csc_install_default_agent(config, provider="echo")
    config.storage.path = str(tmp_path / "state.db")
    config.enabled_plugins = ["example.public"] if enabled else []
    config_path = tmp_path / ("enabled.json" if enabled else "disabled.json")
    save_config(config, str(config_path))
    return APIRuntime.from_config_path(
        str(config_path),
        home_root=tmp_path / "home",
        data_root=tmp_path / "data",
    )


def test_public_manifest_plugin_runs_and_reports_policy_and_dependency(
    tmp_path: Path,
) -> None:
    plugin_root = tmp_path / "plugins"
    _write_plugin(plugin_root)

    with _plugin_path(plugin_root):
        runtime = _runtime(tmp_path, enabled=True)
    try:
        tool_name = "plugin.example.public.echo"
        tool = runtime.tools.get(tool_name)
        assert tool.handler({"text": "hello"}, object()) == {"text": "hello"}

        inventory = {
            item["name"]: item for item in runtime.tool_inventory_report(readiness=True)
        }
        assert inventory[tool_name]["dependency_readiness"][0]["state"] == "missing"

        decision = runtime.action_policy.check(
            {"tool": tool_name, "args": {"text": "hello"}},
            {},
        )
        assert decision.decision == "REQUIRE_CONFIRM"
    finally:
        runtime.close()


def test_disabled_public_manifest_plugin_does_not_register_tools(
    tmp_path: Path,
) -> None:
    plugin_root = tmp_path / "plugins"
    _write_plugin(plugin_root)

    with _plugin_path(plugin_root):
        runtime = _runtime(tmp_path, enabled=False)
    try:
        assert "plugin.example.public.echo" not in runtime.tools.list()
    finally:
        runtime.close()
