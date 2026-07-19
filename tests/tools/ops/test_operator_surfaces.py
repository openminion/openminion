from __future__ import annotations

import json

from typer.testing import CliRunner

from openminion.tools.ops import cli
from openminion.tools.ops.api import operator_state
from openminion.tools.ops.cli import app
from openminion.tools.ops.service import local_ops_service


def test_operator_state_is_redacted_and_renderer_neutral() -> None:
    state = operator_state(local_ops_service())

    assert state["ok"] is True
    assert set(state["data"]) == {
        "tool_family",
        "targets",
        "jobs",
        "evidence",
        "pending_approvals",
        "disabled_reasons",
    }
    assert state["data"]["tool_family"]["id"] == "ops"
    assert state["data"]["tool_family"]["guidance"] == "ops.safety.v1"
    target = state["data"]["targets"][0]
    assert "credential_ref" not in target
    assert "endpoint_trust" not in target


def test_cli_status_matches_shared_api_envelope(monkeypatch) -> None:
    service = local_ops_service()
    monkeypatch.setattr(cli, "local_ops_service", lambda: service)

    result = CliRunner().invoke(app, ["status"])

    assert result.exit_code == 0
    expected = json.loads(json.dumps(operator_state(service)))
    assert json.loads(result.stdout) == expected
