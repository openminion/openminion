from __future__ import annotations

from pathlib import Path

from tests.brain.diagnostics.phase_trace_grade import (
    TraceExpectation,
    grade_phase_trace,
)


def _fixture(name: str) -> Path:
    return (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "rsp_phase_contract_traces"
        / name
    )


def test_trace_grader_flags_invalid_decide_tool_execution() -> None:
    grade = grade_phase_trace(_fixture("invalid_decide"))

    assert grade.ok is False
    assert {item.code for item in grade.issues} == {
        "decide_emitted_execution_tool_call"
    }


def test_trace_grader_flags_invalid_judge_and_closure() -> None:
    grade = grade_phase_trace(_fixture("invalid_judge"))

    assert grade.ok is False
    assert {item.code for item in grade.issues} == {
        "judge_emitted_prose",
        "closure_after_invalid_judge",
    }


def test_trace_grader_flags_malformed_wrapper_shape() -> None:
    grade = grade_phase_trace(_fixture("malformed_wrapper"))

    assert grade.ok is False
    assert {item.code for item in grade.issues} == {"malformed_trace_response"}


def test_trace_grader_detects_compound_collapse_from_expectation(
    tmp_path: Path,
) -> None:
    trace_dir = tmp_path / "trace"
    trace_dir.mkdir()
    (trace_dir / "step01-call01.json").write_text(
        """
        {
          "inference_step": 1,
          "metadata": {"purpose": "decide"},
          "tool_choice": "{'type': 'function', 'function': {'name': 'submit_output'}}",
          "tools": [{"name": "submit_output", "parameters": {"type": "object", "properties": {}}}]
        }
        """.strip(),
        encoding="utf-8",
    )
    (trace_dir / "step01-call01-response.json").write_text(
        """
        {
          "finish_reason": "tool_calls",
          "tool_calls": [
            {
              "name": "submit_output",
              "arguments": {
                "mode": "act",
                "confidence": 0.9,
                "reason_code": "single_step",
                "act_profile": "general",
                "execution_target": {"kind": "local"},
                "sub_intents": ["go_google"]
              }
            }
          ]
        }
        """.strip(),
        encoding="utf-8",
    )

    grade = grade_phase_trace(
        trace_dir,
        expectation=TraceExpectation(expected_first_decide_mode="plan"),
    )

    assert grade.ok is False
    # PTO: act is now a recognized _DECISION_MODES entry, so plan→act mismatch
    # correctly produces synthetic_compound_to_single_collapse (not the generic
    # unexpected_first_decide_mode code that was used when act was not recognized).
    assert {item.code for item in grade.issues} == {
        "synthetic_compound_to_single_collapse"
    }


def test_trace_grader_accepts_auto_tool_choice_for_schema_only_compat_path(
    tmp_path: Path,
) -> None:
    trace_dir = tmp_path / "trace_auto"
    trace_dir.mkdir()
    (trace_dir / "step01-call01.json").write_text(
        """
        {
          "inference_step": 1,
          "metadata": {"purpose": "decide"},
          "tool_choice": "auto",
          "tools": [{"name": "submit_output", "parameters": {"type": "object", "properties": {}}}]
        }
        """.strip(),
        encoding="utf-8",
    )
    (trace_dir / "step01-call01-response.json").write_text(
        """
        {
          "finish_reason": "tool_calls",
          "tool_calls": [
            {
              "name": "submit_output",
              "arguments": {
                "mode": "respond",
                "confidence": 1.0,
                "reason_code": "greeting",
                "sub_intents": [],
                "rationale": "",
                "answer": "hi"
              }
            }
          ]
        }
        """.strip(),
        encoding="utf-8",
    )

    grade = grade_phase_trace(trace_dir)

    assert grade.ok is True
