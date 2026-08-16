from __future__ import annotations

import io
import json
from argparse import Namespace
from contextlib import redirect_stdout
from types import SimpleNamespace

import pytest

from openminion.cli.commands.gateway import run_gateway
from openminion.cli.parser import build_parser


def _report(status: str) -> dict[str, object]:
    return {
        "schema_version": "openminion.invocation_lifecycle_repair.v1",
        "session_id": "session-1",
        "high_water_event_id": 10,
        "status": status,
        "created_count": 1 if status == "repaired" else 0,
        "identical_count": 0,
        "invalid_count": 0,
        "conflict_count": 0,
        "failed_count": 0,
        "diagnostics": [],
        "diagnostics_truncated": False,
    }


@pytest.mark.parametrize(
    ("status", "expected_exit"),
    [
        ("repaired", 0),
        ("unchanged", 0),
        ("not_found", 1),
        ("invalid_source", 1),
        ("conflict", 1),
        ("error", 3),
    ],
)
def test_gateway_repair_lifecycle_json_schema_and_exit(
    status: str,
    expected_exit: int,
) -> None:
    gateway = SimpleNamespace(
        repair_invocation_lifecycle=lambda **_kwargs: _report(status)
    )
    app = SimpleNamespace(resolve_gateway=lambda _agent_id: gateway)
    args = Namespace(
        gateway_command="repair-lifecycle",
        session_id="session-1",
        json=True,
        quiet=False,
    )
    output = io.StringIO()

    with redirect_stdout(output):
        exit_code = run_gateway(args, app)
    payload = json.loads(output.getvalue())

    assert exit_code == expected_exit
    assert set(payload) == {
        "schema_version",
        "session_id",
        "high_water_event_id",
        "status",
        "created_count",
        "identical_count",
        "invalid_count",
        "conflict_count",
        "failed_count",
        "diagnostics",
        "diagnostics_truncated",
    }


def test_gateway_repair_lifecycle_requires_session_id() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit) as raised:
        parser.parse_args(["gateway", "repair-lifecycle", "--json"])
    assert raised.value.code == 2


def test_gateway_repair_lifecycle_rejects_blank_session_id() -> None:
    gateway = SimpleNamespace(
        repair_invocation_lifecycle=lambda **_kwargs: pytest.fail("repair must not run")
    )
    app = SimpleNamespace(resolve_gateway=lambda _agent_id: gateway)
    args = Namespace(
        gateway_command="repair-lifecycle",
        session_id="   ",
        json=True,
        quiet=False,
    )
    output = io.StringIO()

    with redirect_stdout(output):
        exit_code = run_gateway(args, app)

    assert exit_code == 2
    assert json.loads(output.getvalue()) == {"error": "session_id is required"}
