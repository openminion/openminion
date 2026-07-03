"""Measure local OpenMinion performance baseline scenarios."""

from __future__ import annotations

import argparse
import cProfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
import io
import json
import math
import os
from pathlib import Path
import platform
import pstats
import statistics
import subprocess
import sys
import tempfile
import time
import tracemalloc
from typing import Any
from uuid import uuid4

from openminion.base.types import Message
from openminion.services.context.budget import (
    ContextBudgetConfig,
    assemble_budgeted_context,
)

ARTIFACT_SCHEMA_VERSION = "pomv2.performance.v2"
LANE_ARTIFACT_DIR = "openminion-performance-observability-and-measurement-v2-2026-07-02"
DEFAULT_SCENARIOS = (
    "cold_focus_startup",
    "warm_focus_startup",
    "simple_turn",
    "local_status_tool_turn",
    "context_heavy_turn",
    "coding_turn",
    "research_turn",
    "repeated_local_turns",
)
LOCAL_VARIANCE = "local_deterministic"
REPLAY_VARIANCE = "replay_fixture"
WARN_ONLY_VARIANCE = "provider_warn_only"
DEFAULT_THRESHOLD_MODE = "warn"
PROFILE_TOP_LIMIT = 20
IMPORTTIME_TOP_LIMIT = 20
TRACEMALLOC_TOP_LIMIT = 10


@dataclass(frozen=True)
class ScenarioRun:
    scenario_id: str
    command: str
    provider_profile: str
    provider_variance_class: str
    metrics: dict[str, Any]
    notes: list[str]
    ok: bool = True
    error: str | None = None


@dataclass(frozen=True)
class RunOptions:
    workspace_root: Path
    output_root: Path
    python: Path
    runs: int
    timeout_seconds: int
    include_importtime: bool
    profile: bool
    warmup_runs: int = 0
    compare_baseline: Path | None = None
    threshold_mode: str = DEFAULT_THRESHOLD_MODE


def _workspace_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _default_output_root(workspace_root: Path) -> Path:
    return workspace_root / "workspace-tmp" / LANE_ARTIFACT_DIR


def _utc_timestamp() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def _current_rss_bytes() -> int:
    try:
        import psutil  # type: ignore[import-not-found]

        return int(psutil.Process(os.getpid()).memory_info().rss)
    except Exception:
        try:
            import resource

            rss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
            if sys.platform == "darwin":
                return rss
            return rss * 1024
        except Exception:
            return 0


def _dirty_worktree_summary(workspace_root: Path) -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["git", "-C", str(workspace_root / "openminion"), "status", "--short"],
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )
    except Exception:
        return {"available": False, "has_changes": None, "change_count": None}
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    return {
        "available": result.returncode == 0,
        "has_changes": bool(lines),
        "change_count": len(lines),
        "sample": lines[:20],
    }


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def _elapsed_ns(started: int) -> int:
    return max(0, time.perf_counter_ns() - started)


def _ns_to_ms(elapsed_ns: int) -> int:
    return max(0, int(elapsed_ns / 1_000_000))


def _estimate_tokens(text: str, chars_per_token: float = 4.0) -> int:
    return max(0, int(len(text) / max(0.1, chars_per_token)))


def _base_metrics() -> dict[str, Any]:
    return {
        "wall_time_ms": None,
        "wall_time_ns": None,
        "time_to_first_visible_text_ms": None,
        "phase_timings_ms": {},
        "phase_timings_ns": {},
        "measurement_resolution": "perf_counter_ns",
        "provider_round_trip_ms": None,
        "context_assembly_ms": None,
        "prompt_tokens_estimated": None,
        "prompt_bytes": None,
        "tool_schema_bytes": None,
        "tool_call_count": 0,
        "duplicate_call_count": 0,
        "rss_start_bytes": _current_rss_bytes(),
        "rss_end_bytes": None,
        "rss_delta_bytes": None,
        "tracemalloc_current_bytes": None,
        "tracemalloc_peak_bytes": None,
        "import_self_us": None,
        "import_cumulative_us": None,
        "importtime_artifact": None,
        "importtime_summary_artifact": None,
        "importtime_top_modules": [],
        "importtime_module_families": [],
        "tracemalloc_snapshot_diff": [],
    }


def _finish_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    rss_end = _current_rss_bytes()
    metrics["rss_end_bytes"] = rss_end
    start = metrics.get("rss_start_bytes")
    metrics["rss_delta_bytes"] = (
        rss_end - int(start) if isinstance(start, int) else None
    )
    if tracemalloc.is_tracing():
        current, peak = tracemalloc.get_traced_memory()
        metrics["tracemalloc_current_bytes"] = int(current)
        metrics["tracemalloc_peak_bytes"] = int(peak)
    return metrics


def _tracemalloc_diff_summary(
    before: tracemalloc.Snapshot,
    after: tracemalloc.Snapshot,
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for stat in after.compare_to(before, "lineno")[:TRACEMALLOC_TOP_LIMIT]:
        frame = stat.traceback[0] if stat.traceback else None
        entries.append(
            {
                "filename": str(frame.filename) if frame else "",
                "lineno": int(frame.lineno) if frame else 0,
                "size_diff_bytes": int(stat.size_diff),
                "count_diff": int(stat.count_diff),
                "size_bytes": int(stat.size),
                "count": int(stat.count),
            }
        )
    return entries


def _run_with_metrics(
    *,
    scenario_id: str,
    command: str,
    provider_variance_class: str,
    provider_profile: str = "none",
    action: Callable[[dict[str, Any]], list[str]],
) -> ScenarioRun:
    tracemalloc.start()
    before_snapshot = tracemalloc.take_snapshot()
    metrics = _base_metrics()
    started_ns = time.perf_counter_ns()
    notes: list[str] = []
    try:
        notes.extend(action(metrics))
        metrics["wall_time_ns"] = _elapsed_ns(started_ns)
        metrics["wall_time_ms"] = _ns_to_ms(int(metrics["wall_time_ns"]))
        return ScenarioRun(
            scenario_id=scenario_id,
            command=command,
            provider_profile=provider_profile,
            provider_variance_class=provider_variance_class,
            metrics=_finish_metrics(metrics),
            notes=notes,
        )
    except Exception as exc:  # noqa: BLE001 - baseline artifacts must record failure
        metrics["wall_time_ns"] = _elapsed_ns(started_ns)
        metrics["wall_time_ms"] = _ns_to_ms(int(metrics["wall_time_ns"]))
        return ScenarioRun(
            scenario_id=scenario_id,
            command=command,
            provider_profile=provider_profile,
            provider_variance_class=provider_variance_class,
            metrics=_finish_metrics(metrics),
            notes=notes,
            ok=False,
            error=f"{type(exc).__name__}: {exc}",
        )
    finally:
        if tracemalloc.is_tracing():
            try:
                metrics["tracemalloc_snapshot_diff"] = _tracemalloc_diff_summary(
                    before_snapshot,
                    tracemalloc.take_snapshot(),
                )
            except Exception:
                metrics["tracemalloc_snapshot_diff"] = []
        tracemalloc.stop()


def _focus_help_command(options: RunOptions) -> list[str]:
    return [
        str(options.python),
        "-m",
        "openminion",
        "--home-root",
        str(options.workspace_root),
        "--data-root",
        str(options.workspace_root / ".openminion"),
        "focus",
        "--help",
    ]


def _command_env(
    options: RunOptions, *, data_root: Path | None = None
) -> dict[str, str]:
    env = os.environ.copy()
    src_root = options.workspace_root / "openminion" / "src"
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        str(src_root)
        if not existing_pythonpath
        else f"{src_root}{os.pathsep}{existing_pythonpath}"
    )
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["OPENMINION_HOME"] = str(options.workspace_root)
    if data_root is not None:
        env["OPENMINION_DATA_ROOT"] = str(data_root)
    return env


def _run_subprocess(
    command: list[str], *, options: RunOptions, data_root: Path
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=options.workspace_root / "openminion",
        env=_command_env(options, data_root=data_root),
        text=True,
        capture_output=True,
        timeout=options.timeout_seconds,
        check=False,
    )


def _module_family(module_name: str) -> str:
    normalized = str(module_name or "").strip()
    if not normalized:
        return "unknown"
    parts = normalized.split(".")
    if parts[0] == "openminion" and len(parts) >= 2:
        return ".".join(parts[:2])
    return parts[0]


def _parse_importtime_report(stderr: str) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for line in stderr.splitlines():
        if not line.startswith("import time:"):
            continue
        parts = [part.strip() for part in line.removeprefix("import time:").split("|")]
        if len(parts) < 3:
            continue
        try:
            self_us = int(parts[0])
            cumulative_us = int(parts[1])
        except ValueError:
            continue
        module_name = parts[2].strip()
        entries.append(
            {
                "module": module_name,
                "module_family": _module_family(module_name),
                "self_us": self_us,
                "cumulative_us": cumulative_us,
            }
        )
    families: dict[str, dict[str, Any]] = {}
    for entry in entries:
        family = str(entry["module_family"])
        bucket = families.setdefault(
            family,
            {
                "module_family": family,
                "self_us": 0,
                "cumulative_us": 0,
                "module_count": 0,
            },
        )
        bucket["self_us"] = int(bucket["self_us"]) + int(entry["self_us"])
        bucket["cumulative_us"] = max(
            int(bucket["cumulative_us"]),
            int(entry["cumulative_us"]),
        )
        bucket["module_count"] = int(bucket["module_count"]) + 1
    return {
        "max_self_us": max((int(entry["self_us"]) for entry in entries), default=None),
        "max_cumulative_us": max(
            (int(entry["cumulative_us"]) for entry in entries),
            default=None,
        ),
        "top_self": sorted(
            entries,
            key=lambda item: int(item["self_us"]),
            reverse=True,
        )[:IMPORTTIME_TOP_LIMIT],
        "top_cumulative": sorted(
            entries,
            key=lambda item: int(item["cumulative_us"]),
            reverse=True,
        )[:IMPORTTIME_TOP_LIMIT],
        "module_families": sorted(
            families.values(),
            key=lambda item: int(item["cumulative_us"]),
            reverse=True,
        )[:IMPORTTIME_TOP_LIMIT],
    }


def _capture_importtime(
    *,
    scenario_id: str,
    command: list[str],
    options: RunOptions,
    data_root: Path,
) -> dict[str, Any]:
    if not options.include_importtime:
        return {
            "max_self_us": None,
            "max_cumulative_us": None,
            "raw_artifact": None,
            "summary_artifact": None,
            "top_self": [],
            "top_cumulative": [],
            "module_families": [],
        }
    import_command = [str(options.python), "-X", "importtime", *command[1:]]
    completed = _run_subprocess(import_command, options=options, data_root=data_root)
    report = _parse_importtime_report(completed.stderr)
    out_dir = options.output_root / "importtime"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{_utc_timestamp()}-{scenario_id}.txt"
    path.write_text(completed.stderr, encoding="utf-8")
    summary_path = out_dir / f"{_utc_timestamp()}-{scenario_id}.json"
    summary = {
        "scenario_id": scenario_id,
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        **report,
    }
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return {
        **report,
        "raw_artifact": str(path),
        "summary_artifact": str(summary_path),
    }


def _measure_focus_startup(
    *, scenario_id: str, options: RunOptions, cold: bool
) -> ScenarioRun:
    command = _focus_help_command(options)

    def action(metrics: dict[str, Any]) -> list[str]:
        data_parent = options.output_root / "runtime-homes"
        data_parent.mkdir(parents=True, exist_ok=True)
        if cold:
            temp_context = tempfile.TemporaryDirectory(dir=data_parent)
            data_root = Path(temp_context.name) / ".openminion"
        else:
            temp_context = None
            data_root = data_parent / "warm" / ".openminion"
        data_root.mkdir(parents=True, exist_ok=True)
        try:
            completed = _run_subprocess(command, options=options, data_root=data_root)
            metrics["phase_timings_ms"] = {"subprocess_exit_code": completed.returncode}
            prompt_ready = "focused single-agent shell" in completed.stdout.lower()
            metrics["prompt_ready_marker"] = prompt_ready
            if not prompt_ready or completed.returncode != 0:
                metrics["stderr_tail"] = completed.stderr[-500:]
            import_report = _capture_importtime(
                scenario_id=scenario_id,
                command=command,
                options=options,
                data_root=data_root,
            )
            metrics["import_self_us"] = import_report["max_self_us"]
            metrics["import_cumulative_us"] = import_report["max_cumulative_us"]
            metrics["importtime_artifact"] = import_report["raw_artifact"]
            metrics["importtime_summary_artifact"] = import_report["summary_artifact"]
            metrics["importtime_top_modules"] = import_report["top_cumulative"]
            metrics["importtime_module_families"] = import_report["module_families"]
            notes = [
                "Startup command uses `openminion focus --help` as a non-interactive prompt-readiness proxy.",
                "RSS fields measure the harness process; child process max RSS is not portable in this runner.",
            ]
            if import_report["raw_artifact"]:
                notes.append(
                    f"Import-time stderr captured at {import_report['raw_artifact']}."
                )
            return notes
        finally:
            if temp_context is not None:
                temp_context.cleanup()

    return _run_with_metrics(
        scenario_id=scenario_id,
        command=" ".join(command),
        provider_variance_class=LOCAL_VARIANCE,
        action=action,
    )


def _measure_replay_turn(scenario_id: str, prompt: str, answer: str) -> ScenarioRun:
    def action(metrics: dict[str, Any]) -> list[str]:
        transcript = [
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": answer},
        ]
        payload = json.dumps(transcript, sort_keys=True)
        metrics["time_to_first_visible_text_ms"] = 0
        metrics["phase_timings_ms"] = {
            "replay_payload_build_ms": 0,
            "transcript_persistence_ms": 0,
        }
        metrics["phase_timings_ns"] = {
            "replay_payload_build_ns": 0,
            "transcript_persistence_ns": 0,
        }
        metrics["prompt_tokens_estimated"] = _estimate_tokens(prompt)
        metrics["prompt_bytes"] = len(prompt.encode("utf-8"))
        metrics["transcript_bytes"] = len(payload.encode("utf-8"))
        metrics["segment_family_metrics"] = [
            {
                "segment_family": "replay_user",
                "prompt_bytes": len(prompt.encode("utf-8")),
                "prompt_tokens_estimated": _estimate_tokens(prompt),
            },
            {
                "segment_family": "replay_assistant",
                "prompt_bytes": len(answer.encode("utf-8")),
                "prompt_tokens_estimated": _estimate_tokens(answer),
            },
        ]
        metrics["tool_call_count"] = 0
        return [
            "Replay fixture path: measures harness/payload shape without provider latency.",
            "Provider-backed timing remains warn_only until credentials and variance are characterized.",
        ]

    return _run_with_metrics(
        scenario_id=scenario_id,
        command=f"replay_fixture:{scenario_id}",
        provider_variance_class=REPLAY_VARIANCE,
        action=action,
    )


def _measure_local_status_tool_turn() -> ScenarioRun:
    def action(metrics: dict[str, Any]) -> list[str]:
        collect_started_ns = time.perf_counter_ns()
        facts = {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "cwd": str(Path.cwd()),
            "time_ns": time.time_ns(),
        }
        serialized = json.dumps(facts, sort_keys=True)
        collect_ns = _elapsed_ns(collect_started_ns)
        metrics["phase_timings_ms"] = {"local_status_collect_ms": _ns_to_ms(collect_ns)}
        metrics["phase_timings_ns"] = {"local_status_collect_ns": collect_ns}
        metrics["prompt_tokens_estimated"] = _estimate_tokens(serialized)
        metrics["prompt_bytes"] = len(serialized.encode("utf-8"))
        metrics["tool_schema_bytes"] = len(
            json.dumps(
                {
                    "name": "local.status",
                    "description": "Collect local deterministic status facts.",
                    "input_schema": {"type": "object", "properties": {}},
                },
                sort_keys=True,
            ).encode("utf-8")
        )
        metrics["tool_family_metrics"] = [
            {
                "tool_family": "local_status",
                "tool_schema_bytes": metrics["tool_schema_bytes"],
                "tool_call_count": 1,
            }
        ]
        metrics["tool_call_count"] = 1
        return [
            "Local deterministic status/tool-style fixture; no provider or network work."
        ]

    return _run_with_metrics(
        scenario_id="local_status_tool_turn",
        command="local_status_fixture",
        provider_variance_class=LOCAL_VARIANCE,
        action=action,
    )


def _measure_context_heavy_turn() -> ScenarioRun:
    def action(metrics: dict[str, Any]) -> list[str]:
        system = [
            Message(
                channel="system",
                target="context",
                body="Follow project instructions, preserve policy, and cite evidence.",
            )
        ]
        history = [
            Message(
                channel="user" if index % 2 == 0 else "assistant",
                target="context",
                body=(
                    f"Context fixture message {index}. "
                    "This message represents project files, docs, memories, "
                    "and tool observations that must be packed predictably. " * 8
                ),
            )
            for index in range(24)
        ]
        started_ns = time.perf_counter_ns()
        budgeted = assemble_budgeted_context(
            system_messages=system,
            history_messages=history,
            budget=ContextBudgetConfig(max_tokens=900, chars_per_token=4.0),
        )
        context_ns = _elapsed_ns(started_ns)
        context_ms = _ns_to_ms(context_ns)
        telemetry = budgeted.telemetry.to_dict()
        metrics["context_assembly_ms"] = context_ms
        metrics["phase_timings_ms"] = {"context_assembly_ms": context_ms}
        metrics["phase_timings_ns"] = {"context_assembly_ns": context_ns}
        metrics["prompt_tokens_estimated"] = telemetry["estimated_tokens_total"]
        metrics["prompt_bytes"] = sum(
            len(str(message.body or "").encode("utf-8"))
            for message in budgeted.messages
        )
        metrics["segment_count"] = len(history)
        metrics["messages_after_trim"] = telemetry["messages_after_trim"]
        metrics["trimmed_count"] = telemetry["trimmed_count"]
        metrics["segment_family_metrics"] = [
            {
                "segment_family": "system",
                "prompt_bytes": sum(
                    len(str(message.body or "").encode("utf-8")) for message in system
                ),
                "prompt_tokens_estimated": sum(
                    _estimate_tokens(str(message.body or "")) for message in system
                ),
            },
            {
                "segment_family": "history",
                "prompt_bytes": sum(
                    len(str(message.body or "").encode("utf-8")) for message in history
                ),
                "prompt_tokens_estimated": sum(
                    _estimate_tokens(str(message.body or "")) for message in history
                ),
            },
        ]
        return [
            "Uses the existing context budget owner to create a replayable context-heavy measurement."
        ]

    return _run_with_metrics(
        scenario_id="context_heavy_turn",
        command="context_budget_fixture",
        provider_variance_class=REPLAY_VARIANCE,
        action=action,
    )


def _measure_repeated_local_turns(options: RunOptions) -> ScenarioRun:
    def action(metrics: dict[str, Any]) -> list[str]:
        iteration_metrics: list[dict[str, int]] = []
        start_rss = _current_rss_bytes()
        for index in range(max(1, options.runs)):
            iteration_started_ns = time.perf_counter_ns()
            payload = {
                "index": index,
                "platform": platform.system(),
                "python": platform.python_version(),
                "monotonic_ns": time.monotonic_ns(),
            }
            _ = json.dumps(payload, sort_keys=True)
            current, peak = tracemalloc.get_traced_memory()
            iteration_wall_ns = _elapsed_ns(iteration_started_ns)
            iteration_metrics.append(
                {
                    "iteration": index,
                    "wall_time_ns": iteration_wall_ns,
                    "wall_time_ms": _ns_to_ms(iteration_wall_ns),
                    "rss_bytes": _current_rss_bytes(),
                    "tracemalloc_current_bytes": int(current),
                    "tracemalloc_peak_bytes": int(peak),
                }
            )
        end_rss = _current_rss_bytes()
        metrics["phase_timings_ms"] = {"iterations": max(1, options.runs)}
        metrics["tool_call_count"] = max(1, options.runs)
        metrics["iterations"] = iteration_metrics
        metrics["rss_growth_bytes"] = end_rss - start_rss
        metrics["rss_growth_per_iteration_bytes"] = int(
            (end_rss - start_rss) / max(1, options.runs)
        )
        if iteration_metrics:
            first_peak = iteration_metrics[0]["tracemalloc_peak_bytes"]
            last_peak = iteration_metrics[-1]["tracemalloc_peak_bytes"]
            metrics["tracemalloc_peak_growth_bytes"] = last_peak - first_peak
            metrics["tracemalloc_peak_growth_per_iteration_bytes"] = int(
                (last_peak - first_peak) / max(1, options.runs)
            )
        return ["Leak-growth report is informational and warn-only in PBHG."]

    return _run_with_metrics(
        scenario_id="repeated_local_turns",
        command=f"repeated_local_fixture:runs={options.runs}",
        provider_variance_class=LOCAL_VARIANCE,
        action=action,
    )


def run_scenario(scenario_id: str, options: RunOptions) -> ScenarioRun:
    if scenario_id == "cold_focus_startup":
        return _measure_focus_startup(
            scenario_id=scenario_id, options=options, cold=True
        )
    if scenario_id == "warm_focus_startup":
        return _measure_focus_startup(
            scenario_id=scenario_id, options=options, cold=False
        )
    if scenario_id == "simple_turn":
        return _measure_replay_turn(
            scenario_id,
            prompt="Give a one sentence acknowledgement.",
            answer="Acknowledged.",
        )
    if scenario_id == "local_status_tool_turn":
        return _measure_local_status_tool_turn()
    if scenario_id == "context_heavy_turn":
        return _measure_context_heavy_turn()
    if scenario_id == "coding_turn":
        return _measure_replay_turn(
            scenario_id,
            prompt="Inspect a small Python file, make a safe edit, and run a focused test.",
            answer="Replay fixture records coding-shape prompt and transcript overhead.",
        )
    if scenario_id == "research_turn":
        return _measure_replay_turn(
            scenario_id,
            prompt="Research a technical topic, collect sources, and summarize supported claims.",
            answer="Replay fixture records research-shape prompt and source-table overhead.",
        )
    if scenario_id == "repeated_local_turns":
        return _measure_repeated_local_turns(options)
    raise ValueError(f"unknown scenario: {scenario_id}")


def _percentile(values: list[int], pct: int) -> int | None:
    if not values:
        return None
    index = max(0, min(len(values) - 1, math.ceil((pct / 100.0) * len(values)) - 1))
    return values[index]


def _metric_summary(values: Iterable[Any]) -> dict[str, int | None]:
    ints = sorted(int(value) for value in values if isinstance(value, int))
    if not ints:
        return {
            "count": 0,
            "min": None,
            "median": None,
            "p90": None,
            "p95": None,
            "max": None,
            "mean": None,
            "stddev": None,
            "coefficient_of_variation": None,
        }
    mean = statistics.mean(ints)
    stddev = statistics.pstdev(ints) if len(ints) > 1 else 0.0
    return {
        "count": len(ints),
        "min": ints[0],
        "median": int(statistics.median(ints)),
        "p90": _percentile(ints, 90),
        "p95": _percentile(ints, 95),
        "max": ints[-1],
        "mean": round(mean, 2),
        "stddev": round(stddev, 2),
        "coefficient_of_variation": (round(stddev / mean, 4) if mean > 0 else None),
    }


def _family_metric_summary(
    metrics: list[dict[str, Any]],
    *,
    field_name: str,
    family_key: str,
) -> list[dict[str, Any]]:
    families: dict[str, dict[str, Any]] = {}
    for metric in metrics:
        for item in metric.get(field_name) or []:
            if not isinstance(item, dict):
                continue
            family = str(item.get(family_key, "") or "").strip()
            if not family:
                continue
            bucket = families.setdefault(
                family,
                {
                    family_key: family,
                    "sample_count": 0,
                    "prompt_bytes": 0,
                    "prompt_tokens_estimated": 0,
                    "tool_schema_bytes": 0,
                    "tool_call_count": 0,
                },
            )
            bucket["sample_count"] = int(bucket["sample_count"]) + 1
            for key in (
                "prompt_bytes",
                "prompt_tokens_estimated",
                "tool_schema_bytes",
                "tool_call_count",
            ):
                value = item.get(key)
                if isinstance(value, int):
                    bucket[key] = int(bucket[key]) + value
    return sorted(families.values(), key=lambda item: str(item[family_key]))


def _load_comparison_baseline(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _threshold_result(
    *,
    current: dict[str, Any],
    baseline: dict[str, Any] | None,
    scenario_id: str,
    threshold_mode: str,
) -> dict[str, Any]:
    if threshold_mode == "off" or not baseline:
        return {
            "mode": threshold_mode,
            "status": "not_applicable",
            "reason": "no comparison baseline",
        }
    baseline_scenario = dict((baseline.get("scenarios") or {}).get(scenario_id) or {})
    baseline_wall = dict(baseline_scenario.get("wall_time_ms") or {})
    baseline_median = baseline_wall.get("median")
    current_median = dict(current.get("wall_time_ms") or {}).get("median")
    if not isinstance(baseline_median, int) or not isinstance(current_median, int):
        return {
            "mode": threshold_mode,
            "status": "not_applicable",
            "reason": "missing comparable wall median",
        }
    ratio = round(current_median / float(max(1, baseline_median)), 4)
    if ratio <= 1.20:
        status = "pass"
    else:
        status = "fail" if threshold_mode == "hard" else "warn"
    return {
        "mode": threshold_mode,
        "status": status,
        "baseline_wall_median_ms": baseline_median,
        "current_wall_median_ms": current_median,
        "ratio": ratio,
        "warn_ratio": 1.20,
    }


def summarize_runs(
    runs: list[dict[str, Any]],
    *,
    comparison_baseline: dict[str, Any] | None = None,
    threshold_mode: str = DEFAULT_THRESHOLD_MODE,
) -> dict[str, Any]:
    by_scenario: dict[str, list[dict[str, Any]]] = {}
    for run in runs:
        by_scenario.setdefault(str(run["scenario_id"]), []).append(run)

    scenarios: dict[str, Any] = {}
    for scenario_id, scenario_runs in sorted(by_scenario.items()):
        metrics = [dict(run.get("metrics") or {}) for run in scenario_runs]
        scenarios[scenario_id] = {
            "count": len(scenario_runs),
            "ok_count": sum(1 for run in scenario_runs if run.get("ok")),
            "provider_variance_class": scenario_runs[0].get(
                "provider_variance_class", ""
            ),
            "wall_time_ms": _metric_summary(
                metric.get("wall_time_ms") for metric in metrics
            ),
            "wall_time_ns": _metric_summary(
                metric.get("wall_time_ns") for metric in metrics
            ),
            "rss_delta_bytes": _metric_summary(
                metric.get("rss_delta_bytes") for metric in metrics
            ),
            "tracemalloc_peak_bytes": _metric_summary(
                metric.get("tracemalloc_peak_bytes") for metric in metrics
            ),
            "prompt_tokens_estimated": _metric_summary(
                metric.get("prompt_tokens_estimated") for metric in metrics
            ),
            "segment_family_metrics": _family_metric_summary(
                metrics,
                field_name="segment_family_metrics",
                family_key="segment_family",
            ),
            "tool_family_metrics": _family_metric_summary(
                metrics,
                field_name="tool_family_metrics",
                family_key="tool_family",
            ),
            "warn_only": scenario_runs[0].get("provider_variance_class")
            == WARN_ONLY_VARIANCE,
        }
        scenarios[scenario_id]["threshold_result"] = _threshold_result(
            current=scenarios[scenario_id],
            baseline=comparison_baseline,
            scenario_id=scenario_id,
            threshold_mode=threshold_mode,
        )
    return {
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "generated_at_utc": _utc_timestamp(),
        "scenario_count": len(scenarios),
        "run_count": len(runs),
        "threshold_mode": threshold_mode,
        "comparison_baseline_artifact": (
            str(comparison_baseline.get("artifact_path", ""))
            if isinstance(comparison_baseline, dict)
            else ""
        ),
        "scenarios": scenarios,
    }


def _run_to_artifact(
    run: ScenarioRun,
    *,
    run_index: int,
    options: RunOptions,
    profile_artifact: str | None = None,
    profile_pstats_artifact: str | None = None,
) -> dict[str, Any]:
    metrics = dict(run.metrics)
    return {
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "run_started_at": _utc_timestamp(),
        "scenario_id": run.scenario_id,
        "run_id": f"{_utc_timestamp()}-{run.scenario_id}-{run_index}-{uuid4().hex[:8]}",
        "timestamp_utc": _utc_timestamp(),
        "git_head": _git_head(options.workspace_root),
        "dirty_worktree_summary": _dirty_worktree_summary(options.workspace_root),
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "runs_requested": int(options.runs),
        "runs_completed": 1,
        "warmup_runs": int(options.warmup_runs),
        "sample_index": int(run_index),
        "command": run.command,
        "provider_profile": run.provider_profile,
        "provider_variance_class": run.provider_variance_class,
        "wall_ms": metrics.get("wall_time_ms"),
        "wall_ns": metrics.get("wall_time_ns"),
        "time_to_first_visible_text_ms": metrics.get("time_to_first_visible_text_ms"),
        "phase_timings_ms": metrics.get("phase_timings_ms", {}),
        "phase_timings_ns": metrics.get("phase_timings_ns", {}),
        "provider_round_trip_ms": metrics.get("provider_round_trip_ms"),
        "context_assembly_ms": metrics.get("context_assembly_ms"),
        "prompt_bytes": metrics.get("prompt_bytes"),
        "prompt_tokens_estimated": metrics.get("prompt_tokens_estimated"),
        "tool_schema_bytes": metrics.get("tool_schema_bytes"),
        "tool_call_count": metrics.get("tool_call_count"),
        "process_rss_bytes": metrics.get("rss_end_bytes"),
        "tracemalloc_current_bytes": metrics.get("tracemalloc_current_bytes"),
        "tracemalloc_peak_bytes": metrics.get("tracemalloc_peak_bytes"),
        "tracemalloc_snapshot_diff": metrics.get("tracemalloc_snapshot_diff", []),
        "importtime_artifact": metrics.get("importtime_artifact"),
        "profile_artifact": profile_artifact,
        "profile_pstats_artifact": profile_pstats_artifact,
        "comparison_baseline_artifact": (
            str(options.compare_baseline) if options.compare_baseline else None
        ),
        "threshold_mode": options.threshold_mode,
        "threshold_result": "not_applicable",
        "metrics": metrics,
        "notes": run.notes,
        "ok": run.ok,
        "error": run.error,
    }


def _git_head(workspace_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(workspace_root / "openminion"), "rev-parse", "HEAD"],
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


def _write_run_artifact(artifact: dict[str, Any], output_root: Path) -> Path:
    runs_dir = output_root / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    path = runs_dir / f"{artifact['run_id']}.json"
    path.write_text(json.dumps(artifact, indent=2, sort_keys=True), encoding="utf-8")
    return path


def _write_summary_markdown(summary: dict[str, Any], output_root: Path) -> None:
    lines = [
        "# OpenMinion Performance Baseline Summary",
        "",
        f"Generated: `{summary['generated_at_utc']}`",
        f"Runs: `{summary['run_count']}`",
        f"Scenarios: `{summary['scenario_count']}`",
        "",
        "| Scenario | Runs | Variance | Wall median ms | Wall p95 ms | Wall max ms | CV | Gate | Warn only |",
        "| --- | ---: | --- | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    for scenario_id, data in summary["scenarios"].items():
        wall = data["wall_time_ms"]
        gate = data.get("threshold_result", {})
        lines.append(
            "| "
            f"`{scenario_id}` | {data['count']} | `{data['provider_variance_class']}` | "
            f"{wall['median']} | {wall['p95']} | {wall['max']} | "
            f"{wall['coefficient_of_variation']} | `{gate.get('status', 'not_applicable')}` | {data['warn_only']} |"
        )
    lines.extend(
        [
            "",
            "## Gate Proposal",
            "",
            "1. Keep provider-backed and network-backed timing warn-only.",
            "2. Do not hard-fail on timing until repeated local samples establish variance.",
            "3. Treat leak-growth metrics as warning evidence until a leak owner approves thresholds.",
            "4. Candidate local gates after repeated samples: startup wall time, local status wall time, context assembly wall time, and repeated-turn RSS/peak-allocation slope.",
            "",
            "## Notes",
            "",
            "- PBHG artifacts are measurement-only and do not claim runtime speedups.",
            "- Replay fixtures keep the full scenario matrix runnable without provider credentials.",
        ]
    )
    (output_root / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _copy_baseline_plan(options: RunOptions) -> None:
    plan_path = (
        options.workspace_root
        / "docs"
        / "discussions"
        / "openminion-performance-baseline-harness-plan-2026-07-02.md"
    )
    if plan_path.exists():
        (options.output_root / "baseline-plan.md").write_text(
            plan_path.read_text(encoding="utf-8"),
            encoding="utf-8",
        )


def _run_scenario_with_optional_profile(
    scenario_id: str,
    *,
    run_index: int,
    options: RunOptions,
) -> tuple[ScenarioRun, str | None, str | None]:
    if not options.profile:
        return run_scenario(scenario_id, options), None, None
    profiler = cProfile.Profile()
    run = profiler.runcall(run_scenario, scenario_id, options)
    profiles_dir = options.output_root / "profiles"
    profiles_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{_utc_timestamp()}-{scenario_id}-{run_index}"
    pstats_path = profiles_dir / f"{stem}.pstats"
    summary_path = profiles_dir / f"{stem}.txt"
    profiler.dump_stats(str(pstats_path))
    stream = io.StringIO()
    stream.write("## Top cumulative time\n\n")
    pstats.Stats(profiler, stream=stream).strip_dirs().sort_stats(
        "cumulative"
    ).print_stats(PROFILE_TOP_LIMIT)
    stream.write("\n## Top internal time\n\n")
    pstats.Stats(profiler, stream=stream).strip_dirs().sort_stats(
        "tottime"
    ).print_stats(PROFILE_TOP_LIMIT)
    summary_path.write_text(stream.getvalue(), encoding="utf-8")
    return run, str(summary_path), str(pstats_path)


def _scenario_list(raw: str) -> list[str]:
    if raw.strip() == "all":
        return list(DEFAULT_SCENARIOS)
    scenarios = [item.strip() for item in raw.split(",") if item.strip()]
    unknown = sorted(set(scenarios) - set(DEFAULT_SCENARIOS))
    if unknown:
        raise ValueError(f"unknown scenario(s): {', '.join(unknown)}")
    return scenarios


def run_baseline(options: RunOptions, scenarios: list[str]) -> dict[str, Any]:
    options.output_root.mkdir(parents=True, exist_ok=True)
    (options.output_root / "profiles").mkdir(exist_ok=True)
    _copy_baseline_plan(options)

    artifacts: list[dict[str, Any]] = []
    for scenario_id in scenarios:
        for warmup_index in range(options.warmup_runs):
            print(
                f"[performance-baseline] {scenario_id} warmup {warmup_index + 1}/{options.warmup_runs}"
            )
            run_scenario(scenario_id, options)
        per_scenario_runs = 1 if scenario_id == "repeated_local_turns" else options.runs
        for run_index in range(per_scenario_runs):
            print(
                f"[performance-baseline] {scenario_id} run {run_index + 1}/{per_scenario_runs}"
            )
            run, profile_artifact, profile_pstats_artifact = (
                _run_scenario_with_optional_profile(
                    scenario_id,
                    run_index=run_index,
                    options=options,
                )
            )
            artifact = _run_to_artifact(
                run,
                run_index=run_index,
                options=options,
                profile_artifact=profile_artifact,
                profile_pstats_artifact=profile_pstats_artifact,
            )
            _write_run_artifact(artifact, options.output_root)
            artifacts.append(artifact)
            if not run.ok:
                print(f"  error: {run.error}")

    comparison_baseline = _load_comparison_baseline(options.compare_baseline)
    if comparison_baseline is not None and options.compare_baseline is not None:
        comparison_baseline["artifact_path"] = str(options.compare_baseline)
    summary = summarize_runs(
        artifacts,
        comparison_baseline=comparison_baseline,
        threshold_mode=options.threshold_mode,
    )
    (options.output_root / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    _write_summary_markdown(summary, options.output_root)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenarios",
        default="all",
        help="Comma-separated scenario ids or 'all'.",
    )
    parser.add_argument("--runs", type=int, default=3, help="Runs per scenario.")
    parser.add_argument(
        "--warmup-runs",
        type=int,
        default=0,
        help="Warmup runs per scenario; artifacts are not written for warmups.",
    )
    parser.add_argument(
        "--output-root",
        default=None,
        help="Artifact root. Defaults to workspace-tmp/PBHG lane root.",
    )
    parser.add_argument(
        "--workspace-root",
        default=None,
        help="Workspace root containing openminion/ and docs/.",
    )
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="Python executable for subprocess startup measurements.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=30,
        help="Timeout for subprocess startup measurements.",
    )
    parser.add_argument(
        "--no-importtime",
        action="store_true",
        help="Skip -X importtime startup captures.",
    )
    parser.add_argument(
        "--profile",
        action="store_true",
        help="Write cProfile .pstats files and top cumulative/internal summaries.",
    )
    parser.add_argument(
        "--compare-baseline",
        default=None,
        help="Existing summary.json to compare against.",
    )
    parser.add_argument(
        "--threshold-mode",
        choices=("warn", "hard", "off"),
        default=DEFAULT_THRESHOLD_MODE,
        help="Threshold behavior for comparison results.",
    )
    parser.add_argument("--list-scenarios", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.list_scenarios:
        for scenario_id in DEFAULT_SCENARIOS:
            print(scenario_id)
        return 0
    try:
        workspace_root = (
            Path(args.workspace_root).expanduser().resolve()
            if args.workspace_root
            else _workspace_root()
        )
        output_root = (
            Path(args.output_root).expanduser().resolve()
            if args.output_root
            else _default_output_root(workspace_root)
        )
        options = RunOptions(
            workspace_root=workspace_root,
            output_root=output_root,
            python=Path(args.python).expanduser().resolve(),
            runs=max(1, int(args.runs)),
            timeout_seconds=max(1, int(args.timeout_seconds)),
            include_importtime=not bool(args.no_importtime),
            profile=bool(args.profile),
            warmup_runs=max(0, int(args.warmup_runs)),
            compare_baseline=(
                Path(args.compare_baseline).expanduser().resolve()
                if args.compare_baseline
                else None
            ),
            threshold_mode=str(args.threshold_mode),
        )
        scenarios = _scenario_list(str(args.scenarios))
        summary = run_baseline(options, scenarios)
    except Exception as exc:  # noqa: BLE001 - script should return operator-friendly error
        print(
            f"performance baseline failed: {type(exc).__name__}: {exc}", file=sys.stderr
        )
        return 2
    print(f"[performance-baseline] wrote {options.output_root / 'summary.json'}")
    print(
        f"[performance-baseline] scenarios={summary['scenario_count']} runs={summary['run_count']}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - script entrypoint
    raise SystemExit(main())
