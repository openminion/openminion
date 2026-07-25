from __future__ import annotations

import argparse
import json
import statistics
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openminion.modules.brain.config import TOOL_SCHEMA_SHORTLIST_THRESHOLD
from openminion.modules.brain.loop.tools.shortlisting import shortlist_tool_schemas
from openminion.modules.llm.schemas import LLMResponse, Message, ToolSpec, UsageInfo

ARTIFACT_VERSION = "tool_schema_shortlisting_measurement.v1"
LANE_DIR = "openminion-tool-schema-shortlisting-measurement-2026-07-19"


@dataclass(frozen=True, slots=True)
class Scenario:
    scenario_id: str
    user_text: str
    selected_tool_names: tuple[str, ...]
    expected_tool_names: tuple[str, ...]


class _DeterministicShortlistRuntime:
    def __init__(self, *, selected_tool_names: Sequence[str], latency_ms: float = 0.0):
        self._selected_tool_names = tuple(selected_tool_names)
        self._latency_ms = float(latency_ms)
        self.calls: list[dict[str, Any]] = []

    def complete(
        self,
        *,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec],
        model: str,
        tool_choice: str = "auto",
        max_output_tokens: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> LLMResponse:
        started = time.perf_counter_ns()
        if self._latency_ms > 0:
            time.sleep(self._latency_ms / 1000.0)
        self.calls.append(
            {
                "messages": list(messages),
                "tools": list(tools),
                "model": model,
                "tool_choice": tool_choice,
                "max_output_tokens": max_output_tokens,
                "metadata": dict(metadata or {}),
                "wall_time_ms": _elapsed_ms(started),
            }
        )
        payload = {"tool_ids": list(self._selected_tool_names)}
        return LLMResponse(
            ok=True,
            provider="deterministic-local",
            model=model,
            output_text=json.dumps(payload),
            usage=UsageInfo(
                input_tokens=_token_proxy(_messages_text(messages)),
                output_tokens=_token_proxy(json.dumps(payload)),
            ),
        )


def default_tool_inventory() -> list[ToolSpec]:
    specs = [
        ("file.read", "Read a UTF-8 text file from the workspace."),
        ("file.write", "Write or replace a UTF-8 text file in the workspace."),
        ("file.search", "Search workspace files by literal or regular expression."),
        ("web.search", "Search the web for current information."),
        ("web.fetch", "Fetch and extract a web page."),
        ("weather", "Return current weather for a location."),
        ("time", "Return the current time for a timezone."),
        ("git.status", "Inspect current Git status."),
        ("git.diff", "Inspect current Git diff."),
        ("task.list", "List task-plan items."),
        ("task.update", "Update task-plan item status."),
        ("memory.search", "Search durable memory records."),
        ("session.recall", "Recall recent session context."),
        ("artifact.read", "Read a referenced artifact."),
    ]
    if len(specs) <= TOOL_SCHEMA_SHORTLIST_THRESHOLD:
        raise RuntimeError("default inventory must exceed the shortlist threshold")
    return [
        ToolSpec(
            name=name,
            description=description,
            input_schema={"type": "object", "properties": {}},
        )
        for name, description in specs
    ]


def default_scenarios() -> list[Scenario]:
    return [
        Scenario(
            scenario_id="web_research",
            user_text="Research the latest AI browser-agent UX patterns and summarize sources.",
            selected_tool_names=("web.search", "web.fetch"),
            expected_tool_names=("web.search", "web.fetch"),
        ),
        Scenario(
            scenario_id="workspace_code_review",
            user_text="Review changed Python files and explain risky edits.",
            selected_tool_names=("git.diff", "file.read", "file.search"),
            expected_tool_names=("git.diff", "file.read"),
        ),
    ]


def run_measurement(*, samples: int, latency_ms: float) -> dict[str, Any]:
    if samples < 1:
        raise ValueError("samples must be >= 1")
    tool_specs = default_tool_inventory()
    scenarios = default_scenarios()
    pairs: list[dict[str, Any]] = []
    for scenario in scenarios:
        for sample_index in range(samples):
            disabled = _measure_disabled(
                scenario=scenario,
                sample_index=sample_index,
                tool_specs=tool_specs,
            )
            enabled = _measure_enabled(
                scenario=scenario,
                sample_index=sample_index,
                tool_specs=tool_specs,
                latency_ms=latency_ms,
            )
            pairs.append(
                {
                    "scenario_id": scenario.scenario_id,
                    "disabled": disabled,
                    "enabled": enabled,
                }
            )
    return {
        "artifact_schema_version": ARTIFACT_VERSION,
        "measurement_mode": "deterministic-local",
        "samples_per_scenario": samples,
        "tool_inventory_count": len(tool_specs),
        "threshold": TOOL_SCHEMA_SHORTLIST_THRESHOLD,
        "metrics": {
            "prompt_tool_schema_chars": "JSON character count of provider-visible tool specs",
            "prompt_tool_schema_token_proxy": "Whitespace token proxy over provider-visible tool schema JSON",
            "extra_model_calls": "Additional shortlist model call count before the main turn",
            "ttft_ms": "Deterministic local proxy: shortlist helper wall time; disabled path is zero",
            "wall_time_ms": "Harness-measured local wall time for the shortlist decision only",
            "correct": "All expected exact tool names are visible after the path runs",
        },
        "scenario_summaries": _summarize_pairs(pairs),
        "pairs": pairs,
        "recommendation": _recommend(pairs),
    }


def write_artifact(artifact: dict[str, Any], *, output: Path | None = None) -> Path:
    out = output or _default_artifact_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(artifact, indent=2, sort_keys=True))
    return out


def _measure_disabled(
    *, scenario: Scenario, sample_index: int, tool_specs: Sequence[ToolSpec]
) -> dict[str, Any]:
    started = time.perf_counter_ns()
    visible_specs = tuple(tool_specs)
    return _sample_payload(
        scenario=scenario,
        sample_index=sample_index,
        mode="disabled",
        visible_specs=visible_specs,
        all_specs=tool_specs,
        selected_tool_names=tuple(spec.name for spec in visible_specs),
        expected_tool_names=scenario.expected_tool_names,
        reason="disabled_full_inventory_baseline",
        extra_model_calls=0,
        ttft_ms=0.0,
        wall_time_ms=_elapsed_ms(started),
        skip_or_fallback_reason="disabled_by_measurement_control",
    )


def _measure_enabled(
    *,
    scenario: Scenario,
    sample_index: int,
    tool_specs: Sequence[ToolSpec],
    latency_ms: float,
) -> dict[str, Any]:
    runtime = _DeterministicShortlistRuntime(
        selected_tool_names=scenario.selected_tool_names,
        latency_ms=latency_ms,
    )
    started = time.perf_counter_ns()
    result = shortlist_tool_schemas(
        runtime=runtime,
        model="deterministic-shortlist-v1",
        user_messages=[Message(role="user", content=scenario.user_text)],
        tool_specs=tool_specs,
        metadata={
            "purpose": "tool_schema_shortlisting_measurement",
            "scenario": scenario.scenario_id,
        },
    )
    wall_time_ms = _elapsed_ms(started)
    return _sample_payload(
        scenario=scenario,
        sample_index=sample_index,
        mode="enabled",
        visible_specs=result.active_tool_specs,
        all_specs=tool_specs,
        selected_tool_names=result.selected_tool_names,
        expected_tool_names=scenario.expected_tool_names,
        reason=result.reason,
        extra_model_calls=1 if result.llm_call_made else 0,
        ttft_ms=wall_time_ms,
        wall_time_ms=wall_time_ms,
        skip_or_fallback_reason=(None if result.enabled else result.reason),
        shortlist_tokens={
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "total_tokens": result.total_tokens,
        },
    )


def _sample_payload(
    *,
    scenario: Scenario,
    sample_index: int,
    mode: str,
    visible_specs: Sequence[ToolSpec],
    all_specs: Sequence[ToolSpec],
    selected_tool_names: Sequence[str],
    expected_tool_names: Sequence[str],
    reason: str,
    extra_model_calls: int,
    ttft_ms: float,
    wall_time_ms: float,
    skip_or_fallback_reason: str | None,
    shortlist_tokens: dict[str, int] | None = None,
) -> dict[str, Any]:
    schema_json = _tool_schema_json(visible_specs)
    selected = tuple(str(name) for name in selected_tool_names)
    expected = tuple(str(name) for name in expected_tool_names)
    missing = tuple(name for name in expected if name not in set(selected))
    return {
        "scenario_id": scenario.scenario_id,
        "sample_index": sample_index,
        "mode": mode,
        "tool_inventory_count": len(all_specs),
        "visible_tool_count": len(visible_specs),
        "selected_tool_names": list(selected),
        "expected_tool_names": list(expected),
        "missing_expected_tool_names": list(missing),
        "correct": not missing,
        "reason": reason,
        "skip_or_fallback_reason": skip_or_fallback_reason,
        "prompt_tool_schema_chars": len(schema_json),
        "prompt_tool_schema_token_proxy": _token_proxy(schema_json),
        "extra_model_calls": extra_model_calls,
        "ttft_ms": round(ttft_ms, 3),
        "wall_time_ms": round(wall_time_ms, 3),
        "shortlist_tokens": shortlist_tokens
        or {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
    }


def _summarize_pairs(pairs: Sequence[dict[str, Any]]) -> dict[str, Any]:
    summaries: dict[str, Any] = {}
    for scenario_id in sorted({str(pair["scenario_id"]) for pair in pairs}):
        scenario_pairs = [pair for pair in pairs if pair["scenario_id"] == scenario_id]
        summaries[scenario_id] = {
            "sample_count": len(scenario_pairs),
            "disabled": _summarize_samples(
                [pair["disabled"] for pair in scenario_pairs]
            ),
            "enabled": _summarize_samples([pair["enabled"] for pair in scenario_pairs]),
        }
        summaries[scenario_id]["delta"] = _delta_summary(
            summaries[scenario_id]["disabled"], summaries[scenario_id]["enabled"]
        )
    return summaries


def _summarize_samples(samples: Sequence[dict[str, Any]]) -> dict[str, Any]:
    return {
        "sample_count": len(samples),
        "correct_count": sum(1 for sample in samples if sample["correct"]),
        "reason_counts": _counts(sample["reason"] for sample in samples),
        "fallback_counts": _counts(
            sample["skip_or_fallback_reason"] or "none" for sample in samples
        ),
        "prompt_tool_schema_chars": _number_summary(
            samples, "prompt_tool_schema_chars"
        ),
        "prompt_tool_schema_token_proxy": _number_summary(
            samples, "prompt_tool_schema_token_proxy"
        ),
        "extra_model_calls": _number_summary(samples, "extra_model_calls"),
        "ttft_ms": _number_summary(samples, "ttft_ms"),
        "wall_time_ms": _number_summary(samples, "wall_time_ms"),
    }


def _delta_summary(disabled: dict[str, Any], enabled: dict[str, Any]) -> dict[str, Any]:
    return {
        "prompt_tool_schema_chars_median_delta": round(
            enabled["prompt_tool_schema_chars"]["median"]
            - disabled["prompt_tool_schema_chars"]["median"],
            3,
        ),
        "prompt_tool_schema_token_proxy_median_delta": round(
            enabled["prompt_tool_schema_token_proxy"]["median"]
            - disabled["prompt_tool_schema_token_proxy"]["median"],
            3,
        ),
        "extra_model_calls_median_delta": round(
            enabled["extra_model_calls"]["median"]
            - disabled["extra_model_calls"]["median"],
            3,
        ),
        "wall_time_ms_median_delta": round(
            enabled["wall_time_ms"]["median"] - disabled["wall_time_ms"]["median"],
            3,
        ),
    }


def _recommend(pairs: Sequence[dict[str, Any]]) -> dict[str, Any]:
    summaries = _summarize_pairs(pairs)
    enabled_samples = [pair["enabled"] for pair in pairs]
    all_correct = all(sample["correct"] for sample in enabled_samples)
    median_token_delta = statistics.median(
        summary["delta"]["prompt_tool_schema_token_proxy_median_delta"]
        for summary in summaries.values()
    )
    recommendation = "route_to_ptss"
    rationale = (
        "Deterministic samples show token-proxy reduction with correct selected tools, "
        "but enabled mode adds a shortlist model call. PTSS must decide profile/default "
        "policy with provider evidence before any default change."
    )
    if not all_correct:
        recommendation = "defer"
        rationale = "At least one enabled sample hid an expected tool; do not enable by default."
    return {
        "decision": recommendation,
        "all_enabled_samples_correct": all_correct,
        "median_prompt_tool_schema_token_proxy_delta": median_token_delta,
        "ptss_routing": "PTSS-02 must consume this artifact before PTSS-03 policy changes.",
        "rationale": rationale,
    }


def _number_summary(samples: Sequence[dict[str, Any]], key: str) -> dict[str, Any]:
    values = [float(sample[key]) for sample in samples]
    mean = statistics.fmean(values) if values else 0.0
    stdev = statistics.pstdev(values) if len(values) > 1 else 0.0
    cv = stdev / mean if mean else 0.0
    return {
        "min": round(min(values), 3) if values else 0.0,
        "median": round(statistics.median(values), 3) if values else 0.0,
        "p90": round(_percentile(values, 90), 3) if values else 0.0,
        "max": round(max(values), 3) if values else 0.0,
        "coefficient_of_variation": round(cv, 4),
        "needs_10_sample_escalation": cv > 0.15,
    }


def _counts(values: Sequence[str] | Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value)
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * (percentile / 100.0)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _tool_schema_json(tool_specs: Sequence[ToolSpec]) -> str:
    payload = [
        {
            "name": str(spec.name),
            "description": str(spec.description),
            "input_schema": spec.input_schema,
        }
        for spec in tool_specs
    ]
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _messages_text(messages: Sequence[Message]) -> str:
    return "\n".join(str(message.content or "") for message in messages)


def _token_proxy(text: str) -> int:
    value = str(text or "").strip()
    return len(value.split()) if value else 0


def _elapsed_ms(started_ns: int) -> float:
    return (time.perf_counter_ns() - started_ns) / 1_000_000


def _default_artifact_path() -> Path:
    workspace_root = Path(__file__).resolve().parents[3]
    return (
        workspace_root
        / "workspace-tmp"
        / LANE_DIR
        / "tool-schema-shortlisting-measurement.json"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Measure deterministic tool-schema shortlisting enabled/disabled samples."
    )
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--latency-ms", type=float, default=0.0)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    artifact = run_measurement(samples=args.samples, latency_ms=args.latency_ms)
    output = write_artifact(artifact, output=args.output)
    recommendation = artifact["recommendation"]
    print(f"wrote: {output}")
    print(f"recommendation: {recommendation['decision']}")
    print(
        f"all_enabled_samples_correct: {recommendation['all_enabled_samples_correct']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
