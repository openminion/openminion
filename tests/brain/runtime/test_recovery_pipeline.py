from __future__ import annotations

import importlib.util
import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from openminion.modules.brain.execution.validation import normalize_execution_result
from openminion.modules.brain.runtime.memory import stage_declared_goal
from openminion.modules.brain.runtime.recovery import (
    FailClosedReason,
    RepairType,
    TCRPContext,
    TCRPRetryBudget,
    ValidationErrorCode,
    aggregate_stage_events,
    build_retry_tool_message,
    validate_payload,
)
from openminion.modules.brain.runtime.recovery.pipeline import REPAIR_REGISTRY
from openminion.modules.brain.schemas import DecisionAdapter
from openminion.modules.tool.base import ToolExecutionContext
from openminion.modules.tool.runtime.registry_toolspec import execute_tool_spec_call


def _load_validator_module():
    script_path = (
        Path(__file__).resolve().parents[3]
        / "scripts"
        / "validate/recovery_pipeline_contract.py"
    )
    spec = importlib.util.spec_from_file_location(
        "validate_recovery_pipeline_contract",
        script_path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VALIDATOR = _load_validator_module()


class _QueryPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    q: str


class _AliasedToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    q: str = Field(alias="query")


class _EchoSearchTool:
    name = "test.search"
    args_model = _AliasedToolArgs

    @staticmethod
    def handler(arguments, ctx):
        del ctx
        return {"ok": True, "content": arguments["q"]}


@dataclass
class _MemoryApiStub:
    staged: list[dict[str, Any]]

    def stage_candidate(
        self,
        *,
        scope: str,
        record_type: str,
        title: str,
        content: dict[str, Any],
        tags: list[str],
        evidence_refs: list[str],
        confidence: float,
        meta: dict[str, Any],
    ) -> str:
        self.staged.append(
            {
                "scope": scope,
                "record_type": record_type,
                "title": title,
                "content": dict(content),
                "tags": list(tags),
                "evidence_refs": list(evidence_refs),
                "confidence": confidence,
                "meta": dict(meta),
            }
        )
        return "cand-1"


def _tool_context(tmp_path: Path) -> ToolExecutionContext:
    return ToolExecutionContext(
        channel="console",
        target="tests",
        session_id="session-tcrp-1",
        metadata={
            "workspace_root": str(tmp_path),
            "agent_id": "agent-tcrp",
            "trace_id": "trace-tcrp",
        },
    )


@pytest.mark.parametrize(
    ("repair_type", "payload", "kwargs", "expected"),
    [
        (
            RepairType.STRINGIFIED_JSON,
            json.dumps('{"q":"foo"}'),
            {},
            {"q": "foo"},
        ),
        (
            RepairType.TRAILING_COMMA,
            '{"q":"foo",}',
            {},
            '{"q":"foo"}',
        ),
        (
            RepairType.FIELD_ALIAS,
            {"query": "foo"},
            {"alias_map": {"query": "q"}},
            {"q": "foo"},
        ),
        (
            RepairType.TYPE_COERCION,
            {"limit": "2"},
            {"type_coercions": {"limit": int}},
            {"limit": 2},
        ),
        (
            RepairType.CODE_FENCE_STRIP,
            '```json\n{"q":"foo"}\n```',
            {},
            '{"q":"foo"}',
        ),
        (
            RepairType.SMART_QUOTE_NORMALIZE,
            "\u201cfoo\u201d",
            {},
            '"foo"',
        ),
        (
            RepairType.WHITESPACE_NORMALIZE,
            "  foo  ",
            {},
            "foo",
        ),
    ],
)
def test_each_repair_type_is_deterministic(
    repair_type: RepairType,
    payload: Any,
    kwargs: dict[str, Any],
    expected: Any,
) -> None:
    repair_fn = REPAIR_REGISTRY[repair_type]

    normalized, changed = repair_fn(
        payload,
        alias_map=kwargs.get("alias_map"),
        type_coercions=kwargs.get("type_coercions"),
        allow_code_fence=True,
    )

    assert changed is True
    assert normalized == expected


def test_validate_payload_emits_typed_retry_message() -> None:
    result = validate_payload(
        {},
        model=_QueryPayload,
        ctx=TCRPContext(channel_name="tool.args"),
        retry_budget=TCRPRetryBudget(channel_name="tool.args", max_retries=2),
        tool_name="web.search",
        tool_call_id="call-1",
    )

    assert result.should_retry is True
    assert result.retry_reason is not None
    assert result.retry_reason.value == ValidationErrorCode.MISSING_REQUIRED.value
    assert result.validation_errors[0].field_path == "q"
    assert result.retry_message is not None
    payload = json.loads(result.retry_message.content)
    assert payload["status"] == "retry"
    assert payload["error"]["code"] == "TCRP_MISSING_REQUIRED"
    assert "field=q" in payload["summary"]


def test_validate_payload_fail_closes_when_budget_exhausted() -> None:
    result = validate_payload(
        {},
        model=_QueryPayload,
        ctx=TCRPContext(channel_name="tool.args"),
        retry_budget=TCRPRetryBudget(channel_name="tool.args", max_retries=0),
    )

    assert result.should_retry is False
    assert result.fail_closed_reason == FailClosedReason.VALIDATION_BUDGET_EXHAUSTED
    assert (
        result.validation_errors[0].error_code == ValidationErrorCode.MISSING_REQUIRED
    )


def test_aggregate_stage_events_reports_typed_metrics() -> None:
    result = validate_payload(
        {},
        model=_QueryPayload,
        ctx=TCRPContext(channel_name="tool.args"),
        retry_budget=TCRPRetryBudget(channel_name="tool.args", max_retries=2),
        tool_name="web.search",
        tool_call_id="call-1",
    )

    aggregates = aggregate_stage_events(result.events)

    assert aggregates.raw_event_count == len(result.events)
    assert aggregates.validation_failure_rate > 0.0
    assert aggregates.retry_depth_p95 == 1
    assert aggregates.event_counts_by_stage["validation"] >= 1


def test_execute_tool_spec_call_uses_tcrp_alias_validation(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(tmp_path / ".openminion"))

    result = execute_tool_spec_call(
        tool=_EchoSearchTool(),
        arguments={"query": "orion"},
        context=_tool_context(tmp_path),
    )

    assert result.ok is True
    assert result.content == "orion"


def test_execute_tool_spec_call_returns_typed_validation_details_on_failure(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(tmp_path / ".openminion"))

    result = execute_tool_spec_call(
        tool=_EchoSearchTool(),
        arguments={},
        context=_tool_context(tmp_path),
    )

    assert result.ok is False
    assert "Invalid tool arguments: query missing_required" == result.error
    assert result.data["error_code"] == "invalid_arguments"
    assert result.data["reason_code"] == "tool_arg_validation_failed"
    assert result.data["tcrp.validation_errors"][0]["field_path"] == "query"


def test_decision_adapter_validate_json_accepts_code_fenced_payload() -> None:
    decision = DecisionAdapter.validate_json(
        """```json
        {"route":"respond","respond_kind":"answer","confidence":0.9,"answer":"ok"}
        ```"""
    )

    assert decision.route == "respond"
    assert decision.answer == "ok"


def test_decision_adapter_validate_json_raises_validation_error_with_tcrp_message() -> (
    None
):
    with pytest.raises(ValidationError) as excinfo:
        DecisionAdapter.validate_json(
            """```json
            {"route":"respond","respond_kind":"answer","confidence":0.9}
            ```"""
        )

    assert "tcrp_validation" in str(excinfo.value)
    assert "expected required got missing" in str(excinfo.value)


def test_stage_declared_goal_invalid_mapping_raises_deterministic_error() -> None:
    runner = SimpleNamespace(
        memory_api=_MemoryApiStub(staged=[]),
        profile=SimpleNamespace(agent_id="agent-1"),
    )
    state = SimpleNamespace(memory_candidates=[])

    with pytest.raises(ValueError) as excinfo:
        stage_declared_goal(
            runner,
            state=state,
            goal={"trigger": "because", "priority": "low", "action_type": "watch"},
        )

    assert "memory.declared_goal validation failed" in str(excinfo.value)
    assert "goal" in str(excinfo.value)


def test_normalize_execution_result_ignores_invalid_typed_metrics() -> None:
    action_result, job = normalize_execution_result(
        command_id="cmd-1",
        provider="tool",
        raw={
            "status": "success",
            "summary": "done",
            "metrics": {"elapsed_ms": "fast"},
            "error": {"code": "E", "message": 123},
        },
    )

    assert job is None
    assert action_result.status == "success"
    assert action_result.metrics is None
    assert action_result.error is None


def test_build_retry_tool_message_is_deterministic() -> None:
    message = build_retry_tool_message(
        tool_call_id="call-1",
        tool_name="web.search",
        validation_error=SimpleNamespace(
            field_path="args.q",
            error_code=ValidationErrorCode.MISSING_REQUIRED,
            expected_type="required",
            actual_type="missing",
        ),
        command_id="cmd-1",
    )

    payload = json.loads(message.content)
    assert payload == {
        "status": "retry",
        "summary": (
            "validation_error field=args.q code=missing_required "
            "expected=required actual=missing"
        ),
        "error": {
            "code": "TCRP_MISSING_REQUIRED",
            "message": (
                "validation_error field=args.q code=missing_required "
                "expected=required actual=missing"
            ),
        },
    }


GOOD_SCHEMAS = """
from enum import Enum

class RepairType(str, Enum):
    STRINGIFIED_JSON = "stringified_json"
    TRAILING_COMMA = "trailing_comma"
    FIELD_ALIAS = "field_alias"
    TYPE_COERCION = "type_coercion"
    CODE_FENCE_STRIP = "code_fence_strip"
    SMART_QUOTE_NORMALIZE = "smart_quote_normalize"
    WHITESPACE_NORMALIZE = "whitespace_normalize"
"""


GOOD_PIPELINE = """
from typing import Any

from .schemas import RepairType

RepairPayload = dict[str, Any] | str | bytes
REPAIR_REGISTRY = {}

def register_repair(repair_type):
    def _decorator(func):
        REPAIR_REGISTRY[repair_type] = func
        return func
    return _decorator

class TCRPValidationError:
    field_path = "q"
    expected_type = "required"
    actual_type = "missing"

def _base_event():
    return {"channel_name": "x", "stage": "validation", "duration_ms": 0}

def _deterministic_validation_message(error: TCRPValidationError) -> str:
    return f"{error.field_path}:{error.expected_type}:{error.actual_type}"

@register_repair(RepairType.STRINGIFIED_JSON)
def repair_stringified_json(payload: RepairPayload) -> tuple[RepairPayload, bool]:
    return payload, False

@register_repair(RepairType.TRAILING_COMMA)
def repair_trailing_comma(payload: RepairPayload) -> tuple[RepairPayload, bool]:
    return payload, False

@register_repair(RepairType.FIELD_ALIAS)
def repair_field_alias(payload: RepairPayload) -> tuple[RepairPayload, bool]:
    return payload, False

@register_repair(RepairType.TYPE_COERCION)
def repair_type_coercion(payload: RepairPayload) -> tuple[RepairPayload, bool]:
    return payload, False

@register_repair(RepairType.CODE_FENCE_STRIP)
def repair_code_fence_strip(payload: RepairPayload) -> tuple[RepairPayload, bool]:
    return payload, False

@register_repair(RepairType.SMART_QUOTE_NORMALIZE)
def repair_smart_quote_normalize(payload: RepairPayload) -> tuple[RepairPayload, bool]:
    return payload, False

@register_repair(RepairType.WHITESPACE_NORMALIZE)
def repair_whitespace_normalize(payload: RepairPayload) -> tuple[RepairPayload, bool]:
    return payload, False
"""


def _write_recovery_tree(
    tmp_path: Path,
    *,
    schemas_text: str = GOOD_SCHEMAS,
    pipeline_text: str = GOOD_PIPELINE,
) -> Path:
    recovery_root = tmp_path / "recovery"
    recovery_root.mkdir(parents=True, exist_ok=True)
    (recovery_root / "schemas.py").write_text(schemas_text, encoding="utf-8")
    (recovery_root / "pipeline.py").write_text(pipeline_text, encoding="utf-8")
    return recovery_root


def test_tcrp_validator_passes_live_tree(capsys) -> None:
    rc = VALIDATOR.main()
    captured = capsys.readouterr().out.strip().splitlines()[-1]
    payload = json.loads(captured)
    assert rc == 0
    assert payload["ok"] is True


def test_tcrp_validator_flags_stage3_prose_parameter(tmp_path: Path) -> None:
    recovery_root = _write_recovery_tree(
        tmp_path,
        pipeline_text=GOOD_PIPELINE.replace(
            "def repair_stringified_json(payload: RepairPayload) -> tuple[RepairPayload, bool]:",
            "def repair_stringified_json(payload: RepairPayload, assistant_body: str = '') -> tuple[RepairPayload, bool]:",
        ),
    )

    findings = VALIDATOR.validate(recovery_root)

    assert any("assistant_body" in finding for finding in findings)


def test_tcrp_validator_flags_unregistered_enum_member_gap(tmp_path: Path) -> None:
    recovery_root = _write_recovery_tree(
        tmp_path,
        pipeline_text=GOOD_PIPELINE.replace(
            "@register_repair(RepairType.WHITESPACE_NORMALIZE)\ndef repair_whitespace_normalize(payload: RepairPayload) -> tuple[RepairPayload, bool]:\n    return payload, False\n",
            "",
        ),
    )

    findings = VALIDATOR.validate(recovery_root)

    assert any("missing registered repairs" in finding for finding in findings)


def test_tcrp_validator_flags_stage5b_raw_model_output_dependency(
    tmp_path: Path,
) -> None:
    recovery_root = _write_recovery_tree(
        tmp_path,
        pipeline_text=GOOD_PIPELINE.replace(
            'def _deterministic_validation_message(error: TCRPValidationError) -> str:\n    return f"{error.field_path}:{error.expected_type}:{error.actual_type}"',
            "def _deterministic_validation_message(error: TCRPValidationError, model_output: str = '') -> str:\n    return model_output",
        ),
    )

    findings = VALIDATOR.validate(recovery_root)

    assert any("model_output" in finding for finding in findings)


def test_tcrp_validator_flags_stage7_free_form_event_field(tmp_path: Path) -> None:
    recovery_root = _write_recovery_tree(
        tmp_path,
        pipeline_text=GOOD_PIPELINE.replace(
            'return {"channel_name": "x", "stage": "validation", "duration_ms": 0}',
            'return {"channel_name": "x", "stage": "validation", "duration_ms": 0, "summary": "oops"}',
        ),
    )

    findings = VALIDATOR.validate(recovery_root)

    assert any("free-form field `summary`" in finding for finding in findings)


def test_tcrp_validator_flags_llm_call_in_pipeline(tmp_path: Path) -> None:
    recovery_root = _write_recovery_tree(
        tmp_path,
        pipeline_text=GOOD_PIPELINE
        + "\n\ndef call_bad_llm():\n    llm_client.complete('fix it')\n",
    )

    findings = VALIDATOR.validate(recovery_root)

    assert any("must not call LLM surface" in finding for finding in findings)


def test_tcrp_validator_flags_suspicious_repair_helper_name(tmp_path: Path) -> None:
    recovery_root = _write_recovery_tree(
        tmp_path,
        pipeline_text=GOOD_PIPELINE + "\n\ndef rescue_payload():\n    return None\n",
    )

    findings = VALIDATOR.validate(recovery_root)

    assert any("suspicious recovery helper name" in finding for finding in findings)
