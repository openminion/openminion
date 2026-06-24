from __future__ import annotations

from argparse import Namespace
import json

import pytest

from openminion.cli.commands.toolctl import register, run_toolctl
from openminion.modules.tool import ToolRegistry

from ._helpers import (
    FakeExecResult,
    FakePolicyCtl,
    RecordingSandboxRunner,
    build_service,
)


def _seed_service(tmp_path):
    policy_ctl = FakePolicyCtl()
    service = build_service(
        tmp_path,
        registry=ToolRegistry(),
        policy_ctl=policy_ctl,
        sandbox_runner=RecordingSandboxRunner(
            FakeExecResult(returncode=0, stdout="1 passed in 0.01s\n")
        ),
    )
    draft = service.author_draft(
        {
            "name": "adder",
            "description": "Add two integers",
            "source_code": "def adder(x, y):\n    return x + y\n",
            "unit_tests_source": "def test_add():\n    assert True\n",
            "args_schema": {
                "type": "object",
                "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}},
                "required": ["x", "y"],
            },
            "returns_schema": {"type": "integer"},
            "requirements": [],
            "dependencies": [],
            "proposed_scope_tier": "POWER_USER",
        }
    )
    service.inspect_draft({"draft_id": draft["draft_id"], "run_tests": True})
    registered = service.register_draft(
        {"draft_id": draft["draft_id"]}, agent_id="agent-1"
    )
    return service, registered["tool_name"]


@pytest.fixture
def authored_tool_app(tmp_path):
    service, tool_name = _seed_service(tmp_path)
    yield Namespace(authored_tools=service), tool_name
    service.close()


def test_toolctl_list_and_get(capsys, authored_tool_app) -> None:
    app, tool_name = authored_tool_app
    assert (
        run_toolctl(
            Namespace(toolctl_command="list", tier="all", include_removed=False),
            app,
        )
        == 0
    )
    listed = json.loads(capsys.readouterr().out)
    assert listed["tools"][0]["tool_name"] == tool_name

    assert run_toolctl(Namespace(toolctl_command="get", tool_name=tool_name), app) == 0
    fetched = json.loads(capsys.readouterr().out)
    assert fetched["tool"]["tool_name"] == tool_name


def test_toolctl_promote_set_scope_and_remove(capsys, authored_tool_app) -> None:
    app, tool_name = authored_tool_app
    assert (
        run_toolctl(
            Namespace(toolctl_command="promote", tool_name=tool_name, force=True),
            app,
        )
        == 0
    )
    promoted = json.loads(capsys.readouterr().out)
    assert promoted["tier"] == "trusted"

    assert (
        run_toolctl(
            Namespace(
                toolctl_command="set-scope", tool_name=tool_name, scope="WRITE_SAFE"
            ),
            app,
        )
        == 0
    )
    scope_payload = json.loads(capsys.readouterr().out)
    assert scope_payload["min_scope"] == "WRITE_SAFE"

    assert (
        run_toolctl(
            Namespace(toolctl_command="remove", tool_name=tool_name, reason="cleanup"),
            app,
        )
        == 0
    )
    removed = json.loads(capsys.readouterr().out)
    assert removed["removed"] is True


def test_toolctl_help_mentions_restart_requirement() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    register(subparsers)
    help_text = parser.format_help()
    assert "toolctl" in help_text
