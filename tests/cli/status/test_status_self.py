from __future__ import annotations

import io
import json
from argparse import Namespace
from contextlib import redirect_stdout

import openminion.cli.commands.status as status_commands
from openminion.cli.commands.status import run_status
from openminion.cli.commands.status import self as status_self


def _payload(*, health: str = "degraded") -> dict:
    return {
        "ok": True,
        "health": health,
        "self_model": {
            "schema_version": "self_model.v1",
            "health": health,
            "agent_id": "mini",
            "identity": {
                "status": "ok",
                "facts": {"display_name": "Mini", "mission": "Help."},
                "degraded_reasons": [],
            },
            "capabilities": {
                "status": "ok",
                "facts": {
                    "provider": "echo",
                    "model": "echo-small",
                    "tool_count": 4,
                    "enabled_tool_count": 3,
                },
                "degraded_reasons": [],
            },
            "memory_state": {
                "status": "degraded",
                "facts": {"provider": "none", "provenance_available": False},
                "degraded_reasons": ["memory_unavailable"],
            },
            "context_state": {
                "status": "ok",
                "facts": {"budget_total": 4096, "compaction_state": "available"},
                "degraded_reasons": [],
            },
            "improvement_state": {
                "status": "degraded",
                "facts": {"policy": "never", "promotion_posture": "bsil_only"},
                "degraded_reasons": ["generic_candidate_registry_unavailable"],
            },
            "degraded_reasons": [
                "memory_unavailable",
                "generic_candidate_registry_unavailable",
            ],
        },
    }


def test_status_self_json_round_trips_snapshot(monkeypatch) -> None:
    monkeypatch.setattr(
        status_self,
        "_load_runtime_surface_payload",
        lambda **_kwargs: ("daemon", _payload()),
    )
    args = Namespace(config=None, json=True)
    out = io.StringIO()

    with redirect_stdout(out):
        code = status_self.run_self_status(args, config=None)

    assert code == 0
    result = json.loads(out.getvalue())
    assert result["source"] == "daemon"
    assert result["health"] == "degraded"
    assert result["self_model"]["agent_id"] == "mini"


def test_status_self_human_output_lists_operator_sections(monkeypatch) -> None:
    monkeypatch.setattr(
        status_self,
        "_load_runtime_surface_payload",
        lambda **_kwargs: ("inproc", _payload()),
    )
    args = Namespace(config=None, json=False)
    out = io.StringIO()

    with redirect_stdout(out):
        code = status_self.run_self_status(args, config=None)

    text = out.getvalue()
    assert code == 0
    assert "status self: source=inproc health=degraded agent=mini" in text
    assert "- capabilities: provider=echo model=echo-small tools=3/4" in text
    assert "- memory: provider=none provenance=False" in text
    assert "generic_candidate_registry_unavailable" in text


def test_status_self_registered_through_run_status(monkeypatch) -> None:
    monkeypatch.setattr(status_commands, "_load_status_config", lambda _path: object())
    monkeypatch.setattr(
        status_self,
        "_load_runtime_surface_payload",
        lambda **_kwargs: ("daemon", _payload(health="ok")),
    )
    args = Namespace(config=None, status_command="self", json=True)
    out = io.StringIO()

    with redirect_stdout(out):
        code = run_status(args)

    assert code == 0
    assert json.loads(out.getvalue())["self_model"]["agent_id"] == "mini"
