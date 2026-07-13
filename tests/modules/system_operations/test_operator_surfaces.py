from __future__ import annotations

import json

from typer.testing import CliRunner

from openminion.modules.system_operations import cli
from openminion.modules.system_operations.api import operator_state
from openminion.modules.system_operations.cli import app
from openminion.modules.system_operations.service import local_operations_service


def test_operator_state_is_redacted_and_renderer_neutral() -> None:
    state = operator_state(local_operations_service())

    assert state["ok"] is True
    assert set(state["data"]) == {
        "pack",
        "targets",
        "jobs",
        "evidence",
        "pending_approvals",
        "disabled_reasons",
    }
    target = state["data"]["targets"][0]
    assert "credential_ref" not in target
    assert "endpoint_trust" not in target


def test_cli_status_matches_shared_api_envelope(monkeypatch) -> None:
    service = local_operations_service()
    monkeypatch.setattr(cli, "local_operations_service", lambda: service)

    result = CliRunner().invoke(app, ["status"])

    assert result.exit_code == 0
    expected = json.loads(json.dumps(operator_state(service)))
    assert json.loads(result.stdout) == expected
