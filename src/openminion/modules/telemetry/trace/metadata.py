"""Stable metadata assembly for provider trace results."""

import json
import re
from typing import Any, cast


_SENSITIVE_FIELDS = frozenset(
    {
        "content",
        "error_text",
        "system_prompt",
        "system_instructions",
        "user_message",
        "history",
        "input_messages",
        "output_messages",
        "output_text",
        "arguments",
        "result",
        "tool_definitions",
        "file_path",
        "path",
        "command",
        "diff_body",
        "itinerary_details",
        "raw_source",
        "gen_ai.input.messages",
        "gen_ai.output.messages",
        "gen_ai.system_instructions",
        "gen_ai.tool.call.arguments",
        "gen_ai.tool.call.result",
        "gen_ai.tool.definitions",
    }
)
_PROHIBITED_FIELDS = frozenset(
    {
        "api_key",
        "access_token",
        "approval_token",
        "auth_header",
        "authorization",
        "cookie",
        "credentials",
        "environment",
        "endpoint",
        "env",
        "headers",
        "host",
        "hostname",
        "hidden_reasoning",
        "hidden_chain_of_thought",
        "private_key",
        "password",
        "pid",
        "process_id",
        "refresh_token",
        "raw_env",
        "reasoning_content",
        "reasoning_summary",
        "thinking",
        "thinking_blocks",
        "user",
        "username",
        "gen_ai.reasoning",
    }
)
_STRUCTURAL_SECURITY_AGENT_ID = "security-researcher-readonly"
_STRUCTURAL_SECURITY_FIELDS = frozenset(
    {
        "act.allowed_tools",
        "act.profile",
        "actor_type",
        "adaptive.allowed_tools",
        "adaptive.llm_calls",
        "adaptive.loop_iterations",
        "adaptive.mode",
        "adaptive.profile",
        "adaptive.termination_reason",
        "adaptive.tool_calls",
        "adaptive.tool_calls_total",
        "agent_id",
        "artifact_count",
        "artifact_refs",
        "assessment_id",
        "candidate_count",
        "check_count",
        "command_kind",
        "duration_ms",
        "error_code",
        "execution_id",
        "finding_count",
        "invocation_id",
        "kind",
        "mode_name",
        "mode_state",
        "operation",
        "permission_mode",
        "rejected_count",
        "report_published",
        "result_status",
        "route",
        "scanner",
        "scanner_version",
        "schema_version",
        "source_phase",
        "status",
        "status_key",
        "terminal",
        "tool_execution_count",
        "tool_name",
        "tool_results",
        "total_findings",
        "trace_id",
    }
)
_STRUCTURAL_TOOL_RESULT_FIELDS = frozenset(
    {"call_id", "data", "error_code", "ok", "source", "tool_name", "verified"}
)
_STRUCTURAL_TOOL_DATA_FIELDS = frozenset(
    {
        "artifact_count",
        "artifact_refs",
        "assessment_id",
        "candidate_count",
        "check_count",
        "duration_ms",
        "finding_count",
        "redaction_count",
        "rejected_count",
        "result_status",
        "returned_findings",
        "total_findings",
    }
)
_CANONICAL_ARTIFACT_REF = re.compile(r"^artifact://sha256/[0-9a-f]{64}$")


def _canonical_artifact_refs(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        item
        for item in value
        if isinstance(item, str) and _CANONICAL_ARTIFACT_REF.fullmatch(item)
    ]


def _structural_tool_results(items: list[Any]) -> list[dict[str, Any]]:
    results = []
    for item in items:
        if not isinstance(item, dict) or item.get("structural_only") is not True:
            continue
        result = {
            key: value
            for key, value in item.items()
            if key in _STRUCTURAL_TOOL_RESULT_FIELDS
        }
        data = item.get("data")
        result["data"] = (
            {
                key: (
                    _canonical_artifact_refs(value) if key == "artifact_refs" else value
                )
                for key, value in data.items()
                if key in _STRUCTURAL_TOOL_DATA_FIELDS
            }
            if isinstance(data, dict)
            else {}
        )
        results.append(result)
    return results


def structural_security_payload(
    payload: dict[str, Any], agent_id: str | None
) -> dict[str, Any]:
    if agent_id != _STRUCTURAL_SECURITY_AGENT_ID:
        return payload
    structural = {
        key: value
        for key, value in payload.items()
        if key in _STRUCTURAL_SECURITY_FIELDS
    }
    if "artifact_refs" in structural:
        structural["artifact_refs"] = _canonical_artifact_refs(
            structural["artifact_refs"]
        )
    tool_results = structural.get("tool_results")
    if not isinstance(tool_results, list):
        return structural
    structural["tool_results"] = _structural_tool_results(tool_results)
    published = next(
        (
            item
            for item in reversed(structural["tool_results"])
            if item.get("tool_name") == "security.publish_report"
            and item.get("ok") is True
        ),
        None,
    )
    if published is not None:
        structural["report_published"] = True
        report_data = published["data"]
        structural["result_status"] = report_data.get("result_status", "")
        structural["assessment_id"] = report_data.get("assessment_id", "")
    return structural


def apply_content_policy(
    payload: dict[str, Any],
    *,
    allow_sensitive_content: bool,
    allowed_sensitive_fields: frozenset[str] = frozenset(),
    max_string_length: int = 4096,
) -> dict[str, Any]:
    removed: list[str] = []
    truncated: list[dict[str, int | str]] = []

    def clean(value: Any, path: str) -> Any:
        if isinstance(value, dict):
            result: dict[str, Any] = {}
            for raw_key, item in value.items():
                key = str(raw_key)
                field = key.lower()
                item_path = f"{path}.{key}" if path else key
                if field == "error" and not allow_sensitive_content:
                    if isinstance(item, dict):
                        structural = {
                            nested_key: nested_value
                            for nested_key, nested_value in item.items()
                            if str(nested_key).lower() in {"code", "type", "category"}
                        }
                        if structural:
                            result[key] = clean(structural, item_path)
                    else:
                        removed.append(item_path)
                    continue
                if field in _PROHIBITED_FIELDS or field.endswith("_secret"):
                    removed.append(item_path)
                    continue
                if (
                    field in _SENSITIVE_FIELDS
                    and not allow_sensitive_content
                    and field not in allowed_sensitive_fields
                ):
                    removed.append(item_path)
                    continue
                result[key] = clean(item, item_path)
            return result
        if isinstance(value, list):
            return [clean(item, f"{path}[]") for item in value]
        if isinstance(value, str) and len(value) > max_string_length:
            truncated.append(
                {
                    "field": path,
                    "original_size": len(value),
                    "retained_size": max_string_length,
                }
            )
            return value[:max_string_length]
        return value

    cleaned = clean(dict(payload or {}), "")
    cleaned["_telemetry_policy"] = {
        "sensitive_content": "included" if allow_sensitive_content else "omitted",
        "removed_fields": sorted(set(removed)),
        "truncations": truncated,
    }
    return cast(dict[str, Any], cleaned)


def merge_trace_metadata(
    metadata: dict[str, str],
    *,
    model: str | None,
    provider_name: str,
    inference_steps: int,
    untrusted_metadata: dict[str, str],
    untrusted_events: list[dict[str, str]],
    self_improvement_metadata: dict[str, str],
) -> dict[str, str]:
    merged = dict(metadata)
    merged.setdefault("model_tool_name", "")
    merged.setdefault("runtime_binding_id", "")
    merged.setdefault("runtime_tool_name", "")
    merged.setdefault("runtime_fallback_chain", "[]")
    merged.setdefault("runtime_fallback_used", "false")
    merged.setdefault("runtime_resolution_source", "")
    if model and not merged.get("model"):
        merged["model"] = str(model)
    merged.setdefault("provider", provider_name)
    merged["inference_steps"] = str(inference_steps)
    merged.update(untrusted_metadata)
    merged.update(self_improvement_metadata)
    events: list[dict[str, str]] = []
    raw_events = str(merged.get("security_events", "")).strip()
    if raw_events:
        try:
            parsed = json.loads(raw_events)
        except json.JSONDecodeError:
            parsed = []
        if isinstance(parsed, list):
            events.extend(
                {str(key): str(value) for key, value in item.items()}
                for item in parsed
                if isinstance(item, dict)
            )
    events.extend(untrusted_events)
    if events:
        merged["security_events"] = json.dumps(events, sort_keys=True)
    return merged


__all__ = [
    "apply_content_policy",
    "merge_trace_metadata",
    "structural_security_payload",
]
