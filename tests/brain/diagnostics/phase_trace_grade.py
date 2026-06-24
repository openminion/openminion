from __future__ import annotations

import ast
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

_SUMMARY_FILE_RE = re.compile(r"step\d+-call\d+\.json$")
_SCHEMA_ONLY_PHASES = {"decide", "plan", "judge", "validate"}
_DECISION_MODES = {"respond", "act", "plan", "ask_user"}
_JUDGE_ACTIONS = {"close", "continue", "replan"}


@dataclass(frozen=True)
class TraceExpectation:
    expected_first_decide_mode: str | None = None
    required_purposes: tuple[str, ...] = ()
    max_llm_calls: int | None = None


@dataclass(frozen=True)
class TraceIssue:
    code: str
    message: str
    inference_step: int | None = None
    purpose: str = ""


@dataclass(frozen=True)
class PhaseTraceCall:
    summary_path: str
    response_path: str
    inference_step: int
    purpose: str
    tool_names: tuple[str, ...]
    tool_choice: str | dict[str, Any] | None
    finish_reason: str
    output_text: str
    response_tool_calls: tuple[dict[str, Any], ...]
    submit_output_payload: dict[str, Any] | None
    malformed_response: bool = False


@dataclass(frozen=True)
class PhaseTraceGrade:
    trace_dir: str
    call_count: int
    purposes: tuple[str, ...]
    first_decide_mode: str | None
    issues: tuple[TraceIssue, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.issues

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_dir": self.trace_dir,
            "call_count": self.call_count,
            "purposes": list(self.purposes),
            "first_decide_mode": self.first_decide_mode,
            "ok": self.ok,
            "issues": [asdict(item) for item in self.issues],
        }


def grade_phase_trace(
    trace_dir: str | Path,
    *,
    expectation: TraceExpectation | None = None,
) -> PhaseTraceGrade:
    trace_path = Path(trace_dir).expanduser().resolve()
    calls = load_phase_trace_calls(trace_path)
    issues: list[TraceIssue] = []
    purposes = tuple(call.purpose for call in calls if call.purpose)
    first_decide_mode: str | None = None
    invalid_judge_steps: list[int] = []

    for index, call in enumerate(calls):
        if call.purpose in _SCHEMA_ONLY_PHASES:
            if any(name != "submit_output" for name in call.tool_names):
                issues.append(
                    TraceIssue(
                        code="schema_phase_exposed_execution_tools",
                        message=(
                            f"{call.purpose} call exposed execution tools: "
                            + ", ".join(call.tool_names)
                        ),
                        inference_step=call.inference_step,
                        purpose=call.purpose,
                    )
                )
            if not _valid_schema_only_tool_choice(call.tool_choice):
                issues.append(
                    TraceIssue(
                        code="schema_phase_invalid_tool_choice",
                        message=f"{call.purpose} call used invalid tool_choice for schema-only phase",
                        inference_step=call.inference_step,
                        purpose=call.purpose,
                    )
                )

        if call.purpose == "decide":
            if first_decide_mode is None and isinstance(
                call.submit_output_payload, Mapping
            ):
                mode = str(
                    call.submit_output_payload.get("route", "")
                    or call.submit_output_payload.get("mode", "")
                    or ""
                ).strip()
                if mode in _DECISION_MODES:
                    first_decide_mode = mode
            bad_tool_names = [
                str(item.get("name", "") or "").strip()
                for item in call.response_tool_calls
                if str(item.get("name", "") or "").strip()
                and str(item.get("name", "") or "").strip() != "submit_output"
            ]
            if bad_tool_names:
                issues.append(
                    TraceIssue(
                        code="decide_emitted_execution_tool_call",
                        message=(
                            "decide response emitted execution tool calls: "
                            + ", ".join(bad_tool_names)
                        ),
                        inference_step=call.inference_step,
                        purpose=call.purpose,
                    )
                )

        if call.purpose == "judge":
            bad_tool_names = [
                str(item.get("name", "") or "").strip()
                for item in call.response_tool_calls
                if str(item.get("name", "") or "").strip()
                and str(item.get("name", "") or "").strip() != "submit_output"
            ]
            if bad_tool_names:
                issues.append(
                    TraceIssue(
                        code="judge_emitted_execution_tool_call",
                        message=(
                            "judge response emitted execution tool calls: "
                            + ", ".join(bad_tool_names)
                        ),
                        inference_step=call.inference_step,
                        purpose=call.purpose,
                    )
                )
            if not _valid_judge_payload(call.submit_output_payload):
                issues.append(
                    TraceIssue(
                        code="judge_emitted_prose",
                        message="judge did not return a valid submit_output closure payload",
                        inference_step=call.inference_step,
                        purpose=call.purpose,
                    )
                )
                invalid_judge_steps.append(index)

        if any(
            not isinstance(item, Mapping) for item in list(call.response_tool_calls)
        ):
            issues.append(
                TraceIssue(
                    code="malformed_trace_response",
                    message="trace response contained non-object tool_calls entries",
                    inference_step=call.inference_step,
                    purpose=call.purpose,
                )
            )
        elif call.malformed_response:
            issues.append(
                TraceIssue(
                    code="malformed_trace_response",
                    message="trace response tool_calls field was not a list",
                    inference_step=call.inference_step,
                    purpose=call.purpose,
                )
            )

    if invalid_judge_steps and invalid_judge_steps[-1] == len(calls) - 1:
        last_call = calls[invalid_judge_steps[-1]]
        issues.append(
            TraceIssue(
                code="closure_after_invalid_judge",
                message="trace ended immediately after an invalid judge response",
                inference_step=last_call.inference_step,
                purpose=last_call.purpose,
            )
        )

    if expectation is not None:
        if (
            expectation.expected_first_decide_mode
            and first_decide_mode != expectation.expected_first_decide_mode
        ):
            code = "unexpected_first_decide_mode"
            if (
                expectation.expected_first_decide_mode == "plan"
                and first_decide_mode == "act"
            ):
                code = "synthetic_compound_to_single_collapse"
            issues.append(
                TraceIssue(
                    code=code,
                    message=(
                        "expected first decide mode "
                        f"{expectation.expected_first_decide_mode!r} but saw "
                        f"{first_decide_mode!r}"
                    ),
                    inference_step=calls[0].inference_step if calls else None,
                    purpose="decide",
                )
            )

        missing_purposes = [
            item for item in expectation.required_purposes if item not in purposes
        ]
        if missing_purposes:
            issues.append(
                TraceIssue(
                    code="missing_phase_call",
                    message="trace is missing required phase calls: "
                    + ", ".join(missing_purposes),
                )
            )

        if (
            expectation.max_llm_calls is not None
            and len(calls) > expectation.max_llm_calls
        ):
            issues.append(
                TraceIssue(
                    code="llm_call_budget_exceeded",
                    message=(
                        f"trace used {len(calls)} llm calls, expected at most "
                        f"{expectation.max_llm_calls}"
                    ),
                )
            )

    return PhaseTraceGrade(
        trace_dir=str(trace_path),
        call_count=len(calls),
        purposes=purposes,
        first_decide_mode=first_decide_mode,
        issues=tuple(issues),
    )


def load_phase_trace_calls(trace_dir: str | Path) -> list[PhaseTraceCall]:
    trace_path = Path(trace_dir).expanduser().resolve()
    calls: list[PhaseTraceCall] = []
    for summary_path in sorted(trace_path.iterdir()):
        if not summary_path.is_file():
            continue
        if not _SUMMARY_FILE_RE.search(summary_path.name):
            continue
        if summary_path.name.endswith("-response.json") or summary_path.name.endswith(
            "-http.json"
        ):
            continue
        summary = _load_json(summary_path)
        response_path = summary_path.with_name(summary_path.stem + "-response.json")
        response = _load_json(response_path)
        raw_tool_calls = response.get("tool_calls", [])
        malformed_trace_response = not isinstance(raw_tool_calls, list)
        tool_calls = tuple(
            item if isinstance(item, Mapping) else {"_invalid": item}
            for item in (raw_tool_calls if isinstance(raw_tool_calls, list) else [])
        )
        submit_output_payload = _extract_submit_output_payload(tool_calls)
        if malformed_trace_response:
            tool_calls = tool_calls + ({"_invalid": raw_tool_calls},)
        calls.append(
            PhaseTraceCall(
                summary_path=str(summary_path),
                response_path=str(response_path),
                inference_step=int(summary.get("inference_step", 0) or 0),
                purpose=_extract_purpose(summary),
                tool_names=tuple(_extract_tool_names(summary)),
                tool_choice=_parse_tool_choice(summary.get("tool_choice")),
                finish_reason=str(response.get("finish_reason", "") or ""),
                output_text=str(response.get("output_text", "") or ""),
                response_tool_calls=tool_calls,
                submit_output_payload=submit_output_payload,
                malformed_response=malformed_trace_response,
            )
        )
    return sorted(calls, key=lambda item: (item.inference_step, item.summary_path))


def _extract_purpose(summary: Mapping[str, Any]) -> str:
    metadata = summary.get("metadata")
    if isinstance(metadata, Mapping):
        purpose = str(metadata.get("purpose", "") or "").strip().lower()
        if purpose:
            return purpose
    system_prompt = str(summary.get("system_prompt", "") or "")
    match = re.search(r"purpose:\s*([a-z_]+)", system_prompt)
    return str(match.group(1) if match else "").strip().lower()


def _extract_tool_names(summary: Mapping[str, Any]) -> list[str]:
    tools = summary.get("tools")
    if not isinstance(tools, list):
        return []
    names: list[str] = []
    for item in tools:
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name", "") or "").strip()
        if name:
            names.append(name)
    return names


def _extract_submit_output_payload(
    tool_calls: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    for item in tool_calls:
        if str(item.get("name", "") or "").strip() != "submit_output":
            continue
        arguments = item.get("arguments", {})
        parsed = _decode_json_like(arguments)
        if isinstance(parsed, Mapping):
            return dict(parsed)
    return None


def _parse_tool_choice(value: Any) -> str | dict[str, Any] | None:
    if isinstance(value, Mapping):
        return dict(value)
    if not isinstance(value, str):
        return None
    token = value.strip()
    if not token:
        return None
    if token.startswith("{") and token.endswith("}"):
        try:
            parsed = ast.literal_eval(token)
        except Exception:
            return token
        if isinstance(parsed, Mapping):
            return dict(parsed)
    return token


def _valid_schema_only_tool_choice(value: str | dict[str, Any] | None) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"required", "auto"}
    if isinstance(value, Mapping):
        if str(value.get("type", "")).strip().lower() != "function":
            return False
        payload = value.get("function")
        if not isinstance(payload, Mapping):
            return False
        return str(payload.get("name", "")).strip() == "submit_output"
    return False


def _valid_judge_payload(payload: Mapping[str, Any] | None) -> bool:
    if not isinstance(payload, Mapping):
        return False
    next_action = str(payload.get("next_action", "") or "").strip()
    return (
        isinstance(payload.get("satisfied"), bool)
        and "reason" in payload
        and next_action in _JUDGE_ACTIONS
    )


def _decode_json_like(value: Any) -> Any:
    if isinstance(value, str):
        token = value.strip()
        if token.startswith("{") or token.startswith("["):
            try:
                return _decode_json_like(json.loads(token))
            except json.JSONDecodeError:
                return value
        return value
    if isinstance(value, Mapping):
        return {str(key): _decode_json_like(raw) for key, raw in value.items()}
    if isinstance(value, list):
        return [_decode_json_like(item) for item in value]
    return value


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}
