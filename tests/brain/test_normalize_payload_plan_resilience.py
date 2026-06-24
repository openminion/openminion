from __future__ import annotations

from unittest.mock import MagicMock

from openminion.modules.brain.adapters.llm.normalize import (
    _normalize_plan_submit_output_payload,
)
from openminion.modules.brain.adapters.llm import LlmctlAdapter
from openminion.modules.brain.schemas import Plan
from openminion.modules.llm.schemas import ToolCall


def _plan_from_submit_output(arguments: dict) -> dict:
    client = MagicMock()
    response = MagicMock()
    response.ok = True
    response.tool_calls = [
        ToolCall(
            id="submit-1",
            name="submit_output",
            arguments=arguments,
        )
    ]
    response.output_text = ""
    client.call.return_value = response
    adapter = LlmctlAdapter(client)
    return adapter.call_structured(
        model="openai/gpt-4o-mini",
        purpose="plan",
        context={"messages": [{"role": "user", "content": "read a file"}]},
        schema=Plan,
    )


def test_command_id_not_moved_to_args() -> None:
    result = _plan_from_submit_output(
        {
            "objective": "read",
            "steps": [
                {
                    "kind": "tool",
                    "tool_name": "file.read",
                    "title": "Read file",
                    "command_id": "cmd-1",
                    "args": {"path": "/tmp/example.txt"},
                }
            ],
            "stop_conditions": ["done"],
            "assumptions": [],
            "risk_summary": "low",
            "success_criteria": {"status": "success"},
        }
    )

    step = result["steps"][0]
    assert step["command_id"] == "cmd-1"
    assert step["args"] == {"path": "/tmp/example.txt"}
    assert "command_id" not in step["args"]


def test_inputs_conflict_merges_inputs_into_args() -> None:
    result = _plan_from_submit_output(
        {
            "objective": "read",
            "steps": [
                {
                    "kind": "tool",
                    "tool_name": "file.read",
                    "title": "Read file",
                    "args": {"bogus": "junk"},
                    "inputs": {"path": "/correct/path.txt"},
                }
            ],
            "stop_conditions": ["done"],
            "assumptions": [],
            "risk_summary": "low",
            "success_criteria": {"status": "success"},
        }
    )

    step = result["steps"][0]
    assert step["args"] == {"path": "/correct/path.txt", "bogus": "junk"}
    assert step["inputs"] == {}


def test_plan_structural_metadata_fields_stay_outside_args() -> None:
    normalized = _normalize_plan_submit_output_payload(
        {
            "objective": "read",
            "steps": [
                {
                    "kind": "tool",
                    "tool_name": "file.read",
                    "title": "Read file",
                    "description": "Read the requested file",
                    "status": "ready",
                    "step_id": "step-1",
                    "args": {"path": "/tmp/example.txt"},
                }
            ],
            "stop_conditions": ["done"],
            "assumptions": [],
            "risk_summary": "low",
            "success_criteria": {"status": "success"},
        },
        response=MagicMock(),
    )

    step = normalized["steps"][0]
    assert step["description"] == "Read the requested file"
    assert step["status"] == "ready"
    assert step["step_id"] == "step-1"
    assert "description" not in step["args"]
    assert "status" not in step["args"]
    assert "step_id" not in step["args"]


def test_parameters_key_normalizes_like_args() -> None:
    result = _plan_from_submit_output(
        {
            "objective": "read",
            "steps": [
                {
                    "kind": "tool",
                    "tool_name": "file.read",
                    "title": "Read file",
                    "args": {},
                    "parameters": {"path": "/tmp/example.txt"},
                }
            ],
            "stop_conditions": ["done"],
            "assumptions": [],
            "risk_summary": "low",
            "success_criteria": {"status": "success"},
        }
    )

    step = result["steps"][0]
    assert step["args"] == {"path": "/tmp/example.txt"}
    assert "parameters" not in step
