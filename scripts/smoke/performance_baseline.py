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
import shutil
import statistics
import subprocess
import sys
import time
import tracemalloc
from typing import Any
from uuid import uuid4

from openminion.base.types import Message
from openminion.modules.context.budget import (
    ContextBudgetConfig,
    assemble_budgeted_context,
)

ARTIFACT_SCHEMA_VERSION = "pomv2.performance.v2"
STARTUP_FIXTURE_REVISION = "focus-help-v2"
SUT_BOUNDARY_SUBPROCESS = "sut_subprocess_only"
SUT_BOUNDARY_IN_PROCESS = "sut_in_process_fixture"
SUT_BOUNDARY_REPLAY = "sut_replay_fixture"
LANE_ARTIFACT_DIR = "openminion-performance-observability-and-measurement-v2-2026-07-02"
DEFAULT_SCENARIOS = (
    "cold_focus_startup",
    "warm_focus_startup",
    "simple_turn",
    "local_status_tool_turn",
    "context_heavy_turn",
    "deterministic_full_turn",
    "provider_payload_serialization",
    "required_lane_branch_characterization",
    "typeadapter_validation_probe",
    "metadata_json_churn",
    "provider_connection_reuse_decision",
    "storage_wal_index_matrix",
    "retrieval_breakdown_profile",
    "telemetry_export_queue",
    "terminal_render_burst",
    "transcript_retention_growth",
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
    measurement_identity: dict[str, Any]
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


def _measurement_identity(
    *,
    scenario_id: str,
    command: str,
    measured_boundary: str,
    fixture_revision: str,
    options: RunOptions | None = None,
    data_root: Path | None = None,
) -> dict[str, Any]:
    runtime_config = {
        "python_executable": str(options.python) if options is not None else "",
        "workspace_root": str(options.workspace_root) if options is not None else "",
        "data_root": str(data_root) if data_root is not None else "",
        "include_importtime": bool(options.include_importtime) if options else False,
        "profile": bool(options.profile) if options else False,
        "warmup_runs": int(options.warmup_runs) if options else 0,
    }
    return {
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "scenario_id": scenario_id,
        "command": command,
        "fixture_revision": fixture_revision,
        "measured_boundary": measured_boundary,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "runtime_config": runtime_config,
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
    measurement_identity: dict[str, Any] | None = None,
    action: Callable[[dict[str, Any]], list[str]],
) -> ScenarioRun:
    tracemalloc.start()
    before_snapshot = tracemalloc.take_snapshot()
    metrics = _base_metrics()
    started_ns = time.perf_counter_ns()
    notes: list[str] = []
    try:
        notes.extend(action(metrics))
        command_value = str(metrics.pop("_command_override", command))
        identity_value = metrics.pop("_measurement_identity_override", None)
        harness_wall_ns = _elapsed_ns(started_ns)
        override_wall_ns = metrics.pop("_wall_time_ns_override", None)
        metrics["wall_time_ns"] = (
            int(override_wall_ns)
            if isinstance(override_wall_ns, int)
            else harness_wall_ns
        )
        metrics["harness_wall_time_ns"] = harness_wall_ns
        metrics["wall_time_ms"] = _ns_to_ms(int(metrics["wall_time_ns"]))
        return ScenarioRun(
            scenario_id=scenario_id,
            command=command_value,
            provider_profile=provider_profile,
            provider_variance_class=provider_variance_class,
            metrics=_finish_metrics(metrics),
            notes=notes,
            measurement_identity=identity_value
            if isinstance(identity_value, dict)
            else measurement_identity
            or _measurement_identity(
                scenario_id=scenario_id,
                command=command_value,
                measured_boundary=SUT_BOUNDARY_IN_PROCESS,
                fixture_revision="adhoc",
            ),
        )
    except Exception as exc:  # noqa: BLE001 - baseline artifacts must record failure
        command_value = str(metrics.pop("_command_override", command))
        identity_value = metrics.pop("_measurement_identity_override", None)
        harness_wall_ns = _elapsed_ns(started_ns)
        override_wall_ns = metrics.pop("_wall_time_ns_override", None)
        metrics["wall_time_ns"] = (
            int(override_wall_ns)
            if isinstance(override_wall_ns, int)
            else harness_wall_ns
        )
        metrics["harness_wall_time_ns"] = harness_wall_ns
        metrics["wall_time_ms"] = _ns_to_ms(int(metrics["wall_time_ns"]))
        return ScenarioRun(
            scenario_id=scenario_id,
            command=command_value,
            provider_profile=provider_profile,
            provider_variance_class=provider_variance_class,
            metrics=_finish_metrics(metrics),
            notes=notes,
            measurement_identity=identity_value
            if isinstance(identity_value, dict)
            else measurement_identity
            or _measurement_identity(
                scenario_id=scenario_id,
                command=command_value,
                measured_boundary=SUT_BOUNDARY_IN_PROCESS,
                fixture_revision="adhoc",
            ),
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


def _canonical_help_command(options: RunOptions, *, data_root: Path) -> list[str]:
    return [
        str(options.python),
        "-m",
        "openminion",
        "--home-root",
        str(options.workspace_root),
        "--data-root",
        str(data_root),
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
    def action(metrics: dict[str, Any]) -> list[str]:
        data_parent = options.output_root / "runtime-homes"
        data_parent.mkdir(parents=True, exist_ok=True)
        if cold:
            data_root = data_parent / "cold" / ".openminion"
            if data_root.exists():
                shutil.rmtree(data_root)
        else:
            data_root = data_parent / "warm" / ".openminion"
        data_root.mkdir(parents=True, exist_ok=True)
        command = _canonical_help_command(options, data_root=data_root)
        command_text = " ".join(command)
        metrics["_command_override"] = command_text
        metrics["_measurement_identity_override"] = _measurement_identity(
            scenario_id=scenario_id,
            command=command_text,
            measured_boundary=SUT_BOUNDARY_SUBPROCESS,
            fixture_revision=STARTUP_FIXTURE_REVISION,
            options=options,
            data_root=data_root,
        )
        metrics["startup_command"] = command
        metrics["explicit_data_root"] = str(data_root)
        metrics["measured_boundary"] = SUT_BOUNDARY_SUBPROCESS
        sut_started_ns = time.perf_counter_ns()
        completed = _run_subprocess(command, options=options, data_root=data_root)
        metrics["_wall_time_ns_override"] = _elapsed_ns(sut_started_ns)
        metrics["phase_timings_ms"] = {"subprocess_exit_code": completed.returncode}
        prompt_ready = "start the default terminal renderer" in completed.stdout.lower()
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
            "Startup command uses canonical `openminion --help` with a scenario-specific explicit data root.",
            "Artifact wall time measures only the normal subprocess; import-time diagnostics are separate artifacts.",
            "RSS fields measure the harness process; child process max RSS is not portable in this runner.",
        ]
        if import_report["raw_artifact"]:
            notes.append(
                f"Import-time stderr captured at {import_report['raw_artifact']}."
            )
        return notes

    data_root_hint = (
        options.output_root / "runtime-homes" / "cold" / ".openminion"
        if cold
        else options.output_root / "runtime-homes" / "warm" / ".openminion"
    )
    command_text = " ".join(_canonical_help_command(options, data_root=data_root_hint))
    return _run_with_metrics(
        scenario_id=scenario_id,
        command=command_text,
        provider_variance_class=LOCAL_VARIANCE,
        measurement_identity=_measurement_identity(
            scenario_id=scenario_id,
            command=command_text,
            measured_boundary=SUT_BOUNDARY_SUBPROCESS,
            fixture_revision=STARTUP_FIXTURE_REVISION,
            options=options,
            data_root=data_root_hint,
        ),
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


def _measure_deterministic_full_turn(options: RunOptions) -> ScenarioRun:
    def action(metrics: dict[str, Any]) -> list[str]:
        from types import SimpleNamespace

        from openminion.base.config import RunProfileOverrides
        from openminion.modules.telemetry.schemas import TelemetryEvent
        from openminion.modules.telemetry.service import TelemetryService
        from openminion.services.runtime.ingress.execution import execute_runtime_turn
        from openminion.services.runtime.ingress.types import RuntimeTurnRequest

        phase_ns: dict[str, int] = {}
        output_chunks: list[str] = []
        data_root = options.output_root / ".openminion" / "deterministic-full-turn"
        telemetry = TelemetryService(str(data_root / "telemetry.db"))

        class _Sessions:
            def get_session(self, session_id: str) -> None:
                return None

            def list_participants(self, session_id: str) -> list[str]:
                return []

        class _Runtime:
            def __init__(self) -> None:
                self.sessions = _Sessions()
                self.config = SimpleNamespace(
                    runtime=SimpleNamespace(process_mode="benchmark"),
                    gateway=SimpleNamespace(api_turn_timeout_seconds=5),
                )
                self.telemetry_service = telemetry

            def resolve_gateway(self, agent_name: str, **_: Any) -> Any:
                return _Gateway(agent_name)

        class _Gateway:
            def __init__(self, agent_name: str) -> None:
                self.agent_name = agent_name

            async def run_once(self, **kwargs: Any) -> Any:
                context_started = time.perf_counter_ns()
                system = [
                    Message(
                        channel="system",
                        target="deterministic-full-turn",
                        body="Answer deterministically and keep local fixtures structured.",
                    )
                ]
                history = [
                    Message(
                        channel="user",
                        target="deterministic-full-turn",
                        body=str(kwargs.get("message") or ""),
                    )
                ]
                budgeted = assemble_budgeted_context(
                    system_messages=system,
                    history_messages=history,
                    budget=ContextBudgetConfig(max_tokens=500, chars_per_token=4.0),
                )
                phase_ns["context_assembly_ns"] = _elapsed_ns(context_started)

                tool_started = time.perf_counter_ns()
                tool_payload = {
                    "tool": "host.metrics",
                    "platform": platform.system(),
                    "python": platform.python_version(),
                }
                tool_json = json.dumps(tool_payload, sort_keys=True)
                phase_ns["tool_execution_ns"] = _elapsed_ns(tool_started)

                provider_started = time.perf_counter_ns()
                prompt_text = "\n".join(
                    str(message.body or "") for message in budgeted.messages
                )
                response_text = (
                    "Deterministic full-turn fixture complete: "
                    f"{len(prompt_text)} prompt chars, {len(tool_json)} tool bytes."
                )
                phase_ns["provider_stub_round_trip_ns"] = _elapsed_ns(provider_started)

                telemetry_started = time.perf_counter_ns()
                for event_type, payload in (
                    (
                        "chat.phase_timing",
                        {
                            "route_class": "benchmark",
                            "transport": "stub",
                            "cold_start": False,
                            "outcome": "ok",
                            "total_turn_ms": 0,
                            "time_to_first_text_ms": 0,
                            "provider_round_trip_ms": _ns_to_ms(
                                phase_ns["provider_stub_round_trip_ns"]
                            ),
                            "context_pack_build_ms": _ns_to_ms(
                                phase_ns["context_assembly_ns"]
                            ),
                        },
                    ),
                    (
                        "llm.call.completed",
                        {
                            "transport": "stub",
                            "profile_kind": "stub",
                            "outcome": "ok",
                            "call_count": 1,
                            "retry_count": 0,
                            "request_bytes": len(prompt_text.encode("utf-8")),
                            "response_bytes": len(response_text.encode("utf-8")),
                            "input_tokens": _estimate_tokens(prompt_text),
                            "output_tokens": _estimate_tokens(response_text),
                            "cached_tokens": 0,
                            "round_trip_ms": _ns_to_ms(
                                phase_ns["provider_stub_round_trip_ns"]
                            ),
                        },
                    ),
                    (
                        "tool.completed",
                        {
                            "tool_family": "host_metrics",
                            "outcome": "ok",
                            "call_count": 1,
                            "duplicate_call_count": 0,
                            "duration_ms": _ns_to_ms(phase_ns["tool_execution_ns"]),
                        },
                    ),
                    (
                        "storage.query",
                        {
                            "store_family": "telemetry",
                            "operation": "insert_event",
                            "criticality": "noncritical",
                            "duration_ms": 0,
                            "outcome": "ok",
                        },
                    ),
                    (
                        "telemetry.queue.stats",
                        {
                            "criticality": "noncritical",
                            "outcome": "ok",
                            "queue_depth": 0,
                            "drops": 0,
                            "flush_failures": 0,
                            "flush_latency_ms": 0,
                        },
                    ),
                    (
                        "tui.render",
                        {
                            "view_family": "terminal",
                            "render_chunk_ms": 0,
                            "queue_pressure": 0,
                            "retained_messages": 2,
                            "outcome": "ok",
                        },
                    ),
                ):
                    telemetry.record_event_sync(
                        TelemetryEvent(
                            session_id=str(kwargs.get("session_id") or "pnt20"),
                            turn_id=str(kwargs.get("request_id") or "turn"),
                            event_type=event_type,
                            data=payload,
                        )
                    )
                phase_ns["telemetry_persist_ns"] = _elapsed_ns(telemetry_started)

                terminal_started = time.perf_counter_ns()
                output_chunks.append(response_text[:32])
                output_chunks.append(response_text[32:])
                phase_ns["terminal_delivery_ns"] = _elapsed_ns(terminal_started)
                return SimpleNamespace(
                    id=str(kwargs.get("request_id") or "turn"),
                    channel=str(kwargs.get("channel") or "cli"),
                    target=str(kwargs.get("target") or "terminal"),
                    body=response_text,
                    metadata={
                        "session_id": str(kwargs.get("session_id") or "pnt20"),
                        "model_call_count": 1,
                        "tool_call_count": 1,
                        "provider_profile": "stub",
                    },
                    stats=None,
                )

        request = RuntimeTurnRequest(
            message="Run deterministic local status and summarize the result.",
            agent_id="fixture-agent",
            profile_agent_id="fixture-agent",
            channel="cli",
            target="terminal",
            session_id="pnt20-full-turn",
            request_id=f"turn-{uuid4().hex[:8]}",
            timeout_seconds=5.0,
            forced_tools=("host.metrics",),
            deliver=True,
            capability_category="benchmark",
            idempotency_key="pnt20-full-turn",
            inbound_metadata={"fixture_revision": "deterministic-full-turn-v1"},
            run_profile_overrides=RunProfileOverrides(),
        )
        turn_started = time.perf_counter_ns()
        result = execute_runtime_turn(
            runtime=_Runtime(),
            request=request,
            run_gateway_once=lambda **kwargs: kwargs["gateway"].run_once(**kwargs),
        )
        phase_ns["runtime_ingress_ns"] = _elapsed_ns(turn_started)
        telemetry.close_sync()

        phase_ms = {
            key.removesuffix("_ns") + "_ms": _ns_to_ms(value)
            for key, value in phase_ns.items()
        }
        body = str(result.body or "")
        metrics["time_to_first_visible_text_ms"] = 0 if output_chunks else None
        metrics["phase_timings_ns"] = phase_ns
        metrics["phase_timings_ms"] = phase_ms
        metrics["provider_profile_kind"] = "stub"
        metrics["model_call_count"] = int(result.metadata.get("model_call_count", 0))
        metrics["provider_round_trip_ms"] = phase_ms["provider_stub_round_trip_ms"]
        metrics["prompt_bytes"] = len(request.message.encode("utf-8"))
        metrics["prompt_tokens_estimated"] = _estimate_tokens(request.message)
        metrics["response_bytes"] = len(body.encode("utf-8"))
        metrics["output_tokens_estimated"] = _estimate_tokens(body)
        metrics["cached_tokens_estimated"] = 0
        metrics["tool_call_count"] = int(result.metadata.get("tool_call_count", 0))
        metrics["tool_family_metrics"] = [
            {
                "tool_family": "host_metrics",
                "tool_schema_bytes": len("host.metrics".encode("utf-8")),
                "tool_call_count": 1,
            }
        ]
        metrics["storage_operation_count"] = 6
        metrics["telemetry_queue_depth"] = 0
        metrics["render_chunk_count"] = len(output_chunks)
        metrics["retained_messages"] = 2
        return [
            "Complete deterministic turn fixture through runtime ingress, stub gateway, context assembly, tool-shaped work, telemetry persistence, and terminal-shaped delivery.",
            "No provider credentials or network access are used; provider metrics describe the local stub boundary.",
        ]

    return _run_with_metrics(
        scenario_id="deterministic_full_turn",
        command="runtime_ingress_fixture:deterministic_full_turn",
        provider_variance_class=LOCAL_VARIANCE,
        provider_profile="stub",
        measurement_identity=_measurement_identity(
            scenario_id="deterministic_full_turn",
            command="runtime_ingress_fixture:deterministic_full_turn",
            measured_boundary=SUT_BOUNDARY_IN_PROCESS,
            fixture_revision="deterministic-full-turn-v1",
            options=options,
        ),
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


def _measure_provider_payload_serialization() -> ScenarioRun:
    def action(metrics: dict[str, Any]) -> list[str]:
        from openminion.modules.llm.providers.transport.payload import (
            serialize_json_payload,
        )

        payload = {
            "model": "fixture-model",
            "messages": [
                {"role": "system", "content": "Keep wire payloads stable."},
                {"role": "user", "content": "Measure one serialized request body."},
            ],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "host.metrics",
                        "description": "Return host metrics.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
        }
        started_ns = time.perf_counter_ns()
        serialized = serialize_json_payload(payload)
        serialize_ns = _elapsed_ns(started_ns)
        trace_started_ns = time.perf_counter_ns()
        trace_body = serialized.body_json
        trace_bytes = serialized.body_bytes
        trace_reuse_ns = _elapsed_ns(trace_started_ns)
        metrics["phase_timings_ns"] = {
            "provider_payload_serialize_ns": serialize_ns,
            "provider_payload_trace_reuse_ns": trace_reuse_ns,
        }
        metrics["phase_timings_ms"] = {
            "provider_payload_serialize_ms": _ns_to_ms(serialize_ns),
            "provider_payload_trace_reuse_ms": _ns_to_ms(trace_reuse_ns),
        }
        metrics["provider_payload_bytes"] = serialized.byte_count
        metrics["duplicate_serialization_count"] = 0
        metrics["request_body_reused_for_trace"] = bool(
            trace_body and trace_bytes == trace_body.encode("utf-8")
        )
        metrics["prompt_bytes"] = serialized.byte_count
        metrics["prompt_tokens_estimated"] = _estimate_tokens(serialized.body_json)
        return [
            "Provider payload fixture uses the shared serialized body consumed by HTTP, SSE, trace, and curl fallback owners.",
            "No provider or network request is sent.",
        ]

    return _run_with_metrics(
        scenario_id="provider_payload_serialization",
        command="provider_transport_fixture:shared_json_payload",
        provider_variance_class=LOCAL_VARIANCE,
        action=action,
    )


def _measure_required_lane_branch_characterization() -> ScenarioRun:
    def action(metrics: dict[str, Any]) -> list[str]:
        from openminion.modules.llm.providers.base import ProviderResponse
        from openminion.services.agent.execution.required.completion_retry import (
            _looks_like_pre_tool_draft_echo,
            _needs_plain_text_retry,
        )

        branches = [
            "initial_final_response",
            "plain_text_final_response_retry",
            "tool_envelope_final_response_retry",
            "stale_draft_retry",
            "finalization_status_retry",
            "duplicate_final_tool_calls_retry",
            "invalid_argument_retry",
            "required_tool_retry",
            "unavailable_discovery_retry",
        ]
        started_ns = time.perf_counter_ns()
        plain_retry = _needs_plain_text_retry(
            ProviderResponse(
                text='<invoke name="host.metrics">{"status": true}</invoke>',
                model="fixture",
                finish_reason="stop",
            )
        )
        stale_retry = _looks_like_pre_tool_draft_echo(
            response=ProviderResponse(text="draft before tool", model="fixture"),
            final_response=ProviderResponse(text="draft before tool", model="fixture"),
        )
        metrics["phase_timings_ns"] = {
            "required_lane_branch_characterization_ns": _elapsed_ns(started_ns)
        }
        metrics["phase_timings_ms"] = {
            "required_lane_branch_characterization_ms": _ns_to_ms(
                metrics["phase_timings_ns"]["required_lane_branch_characterization_ns"]
            )
        }
        metrics["required_lane_branch_count"] = len(branches)
        metrics["required_lane_retry_purposes"] = branches
        metrics["plain_text_retry_detected"] = plain_retry
        metrics["stale_draft_retry_detected"] = stale_retry
        metrics["structured_completion_state_required"] = True
        metrics["provider_call_reduction_count"] = 0
        metrics["model_call_count"] = 0
        return [
            "Required-lane characterization records explicit retry purposes only.",
            "No provider calls are removed: the spec forbids prose-based completion inference.",
        ]

    return _run_with_metrics(
        scenario_id="required_lane_branch_characterization",
        command="required_lane_fixture:branch_characterization",
        provider_variance_class=LOCAL_VARIANCE,
        action=action,
    )


def _measure_typeadapter_validation_probe() -> ScenarioRun:
    def action(metrics: dict[str, Any]) -> list[str]:
        from pydantic import TypeAdapter

        from openminion.services.gateway.turn_intent import (
            TypedTurnIntent,
            _TYPED_TURN_INTENT_ADAPTER,
        )

        payload = {"kind": "freeform_chat"}
        iterations = 200
        construct_started_ns = time.perf_counter_ns()
        for _ in range(iterations):
            TypeAdapter(TypedTurnIntent).validate_python(payload)
        construct_ns = _elapsed_ns(construct_started_ns)

        reuse_started_ns = time.perf_counter_ns()
        for _ in range(iterations):
            _TYPED_TURN_INTENT_ADAPTER.validate_python(payload)
        reuse_ns = _elapsed_ns(reuse_started_ns)

        metrics["phase_timings_ns"] = {
            "typeadapter_construct_validate_ns": construct_ns,
            "typeadapter_reuse_validate_ns": reuse_ns,
        }
        metrics["phase_timings_ms"] = {
            "typeadapter_construct_validate_ms": _ns_to_ms(construct_ns),
            "typeadapter_reuse_validate_ms": _ns_to_ms(reuse_ns),
        }
        metrics["typeadapter_iterations"] = iterations
        metrics["typeadapter_known_construction_sites"] = 2
        metrics["typeadapter_reuse_ratio"] = (
            round(construct_ns / max(1, reuse_ns), 3) if reuse_ns else None
        )
        metrics["typeadapter_new_global_cache_added"] = False
        return [
            "The live tree has two TypeAdapter construction sites and the turn-intent hot path already owns a module-level adapter.",
            "No global adapter cache is added because the material repeated path is already cached.",
        ]

    return _run_with_metrics(
        scenario_id="typeadapter_validation_probe",
        command="schema_fixture:typeadapter_validation_probe",
        provider_variance_class=LOCAL_VARIANCE,
        action=action,
    )


def _measure_metadata_json_churn() -> ScenarioRun:
    def action(metrics: dict[str, Any]) -> list[str]:
        from openminion.services.agent.execution.required.metadata import (
            invalid_tool_arguments_metadata,
            shared_capability_metadata,
        )

        iterations = 200
        started_ns = time.perf_counter_ns()
        json_field_count = 0
        total_bytes = 0
        for _ in range(iterations):
            invalid = invalid_tool_arguments_metadata(
                tool_name="host.metrics",
                missing_fields_csv="metric,scope",
            )
            shared = shared_capability_metadata(
                intent_category="system_ops",
                capability_primary="host.metrics",
                tool_to_try="host.metrics",
                fallback_chain=["host.metrics"],
                attempted_tools=["host.metrics"],
                capability_fallback_trigger_reason=None,
                all_attempts_count=1,
            )
            payload = {**invalid, **shared}
            json_field_count += sum(
                1
                for value in payload.values()
                if isinstance(value, str) and value[:1] in {"[", "{"}
            )
            total_bytes += sum(
                len(str(value).encode("utf-8")) for value in payload.values()
            )
        churn_ns = _elapsed_ns(started_ns)
        metrics["phase_timings_ns"] = {"metadata_json_churn_ns": churn_ns}
        metrics["phase_timings_ms"] = {"metadata_json_churn_ms": _ns_to_ms(churn_ns)}
        metrics["metadata_json_iterations"] = iterations
        metrics["metadata_json_field_count"] = json_field_count
        metrics["metadata_json_total_bytes"] = total_bytes
        metrics["provider_payload_duplicate_serialization_already_removed"] = True
        metrics["required_lane_metadata_contract_preserved"] = True
        metrics["bounded_representation_change_count"] = 0
        return [
            "Provider payload JSON churn is closed by PNT20-14.",
            "Required-lane metadata remains JSON-string shaped because session/tool metadata consumers depend on that boundary.",
        ]

    return _run_with_metrics(
        scenario_id="metadata_json_churn",
        command="serialization_fixture:metadata_json_churn",
        provider_variance_class=LOCAL_VARIANCE,
        action=action,
    )


def _measure_provider_connection_reuse_decision() -> ScenarioRun:
    def action(metrics: dict[str, Any]) -> list[str]:
        import tomllib

        try:
            import httpx  # type: ignore[import-not-found]  # noqa: F401

            httpx_available = True
        except Exception:
            httpx_available = False
        pyproject = _workspace_root() / "openminion" / "pyproject.toml"
        project_dependencies: list[str] = []
        if pyproject.exists():
            pyproject_payload = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            project_dependencies = list(
                pyproject_payload.get("project", {}).get("dependencies", [])
            )
        started_ns = time.perf_counter_ns()
        from urllib import request as urllib_request

        opener = urllib_request.build_opener()
        reusable_pool_available = hasattr(opener, "close") and False
        decision_ns = _elapsed_ns(started_ns)
        metrics["phase_timings_ns"] = {"provider_connection_decision_ns": decision_ns}
        metrics["phase_timings_ms"] = {
            "provider_connection_decision_ms": _ns_to_ms(decision_ns)
        }
        metrics["provider_transport_owner"] = "urllib"
        metrics["httpx_import_available"] = httpx_available
        metrics["httpx_core_dependency"] = any(
            str(dependency).startswith("httpx") for dependency in project_dependencies
        )
        metrics["urllib_reusable_pool_available"] = reusable_pool_available
        metrics["provider_connection_reuse_change_count"] = 0
        metrics["provider_connection_dependency_decision"] = (
            "defer_httpx_base_promotion"
        )
        return [
            "The base provider transport remains urllib; urllib has no explicit reusable pool owner in this package.",
            "Do not promote httpx into the base install without a separate dependency/release decision and provider compatibility cutover.",
        ]

    return _run_with_metrics(
        scenario_id="provider_connection_reuse_decision",
        command="provider_transport_fixture:connection_reuse_decision",
        provider_variance_class=LOCAL_VARIANCE,
        action=action,
    )


def _measure_storage_wal_index_matrix(options: RunOptions) -> ScenarioRun:
    def action(metrics: dict[str, Any]) -> list[str]:
        import sqlite3

        from openminion.modules.storage.record_store import configure_connection

        db_path = options.output_root / "storage-matrix" / f"store-{uuid4().hex}.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(db_path))
        try:
            configure_connection(connection, wal=True)
            connection.execute(
                "CREATE TABLE records (id INTEGER PRIMARY KEY, family TEXT, key TEXT, value TEXT)"
            )
            connection.execute(
                "CREATE INDEX idx_records_family_key ON records(family, key)"
            )
            insert_started_ns = time.perf_counter_ns()
            with connection:
                connection.executemany(
                    "INSERT INTO records (family, key, value) VALUES (?, ?, ?)",
                    (
                        ("memory", f"key-{index}", f"value-{index}")
                        for index in range(500)
                    ),
                )
            insert_ns = _elapsed_ns(insert_started_ns)
            query_started_ns = time.perf_counter_ns()
            rows = connection.execute(
                "SELECT value FROM records WHERE family = ? AND key = ?",
                ("memory", "key-250"),
            ).fetchall()
            query_ns = _elapsed_ns(query_started_ns)
            plan = [
                " ".join(str(part) for part in row)
                for row in connection.execute(
                    "EXPLAIN QUERY PLAN SELECT value FROM records WHERE family = ? AND key = ?",
                    ("memory", "key-250"),
                ).fetchall()
            ]
            journal_mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0])
            synchronous = str(connection.execute("PRAGMA synchronous").fetchone()[0])
            wal_autocheckpoint = int(
                connection.execute("PRAGMA wal_autocheckpoint").fetchone()[0]
            )
            metrics["phase_timings_ns"] = {
                "storage_insert_ns": insert_ns,
                "storage_index_lookup_ns": query_ns,
            }
            metrics["phase_timings_ms"] = {
                "storage_insert_ms": _ns_to_ms(insert_ns),
                "storage_index_lookup_ms": _ns_to_ms(query_ns),
            }
            metrics["storage_rows"] = 500
            metrics["storage_query_rows"] = len(rows)
            metrics["storage_journal_mode"] = journal_mode.lower()
            metrics["storage_synchronous"] = synchronous
            metrics["storage_wal_autocheckpoint_pages"] = wal_autocheckpoint
            metrics["storage_query_plan"] = plan
            metrics["storage_lowest_risk_change_count"] = 0
            return [
                "SQLite WAL/NORMAL/busy-timeout defaults are already applied through the shared storage connection owner.",
                "The fixture records query-plan and WAL evidence; no unmeasured index or PRAGMA change is made.",
            ]
        finally:
            connection.close()

    return _run_with_metrics(
        scenario_id="storage_wal_index_matrix",
        command="storage_fixture:wal_index_matrix",
        provider_variance_class=LOCAL_VARIANCE,
        action=action,
    )


def _measure_retrieval_breakdown_profile(options: RunOptions) -> ScenarioRun:
    def action(metrics: dict[str, Any]) -> list[str]:
        from datetime import datetime, timezone

        from openminion.modules.memory.runtime.scorer import score_records
        from openminion.modules.retrieve.runtime.retrieve import RetrieveCtl
        from openminion.modules.retrieve.schemas import RetrievalFilters

        root = options.output_root / "retrieval-profile" / uuid4().hex
        service = RetrieveCtl(
            config={
                "version": 1,
                "retrievectl": {
                    "storage": {
                        "sqlite_path": str(root / "retrieve.db"),
                        "blob_root": str(root / "blobs"),
                        "wal_mode": True,
                    },
                    "defaults": {"lexical_candidate_count": 25, "snippet_tokens": 80},
                },
            }
        )
        try:
            for index in range(24):
                service.ingest_source(
                    source_type="doc",
                    source_ref=f"fixture://doc-{index}",
                    text=(
                        "deployment runbook rollback verification "
                        f"service health evidence section {index}"
                    ),
                    scope="session:pnt20",
                    title=f"Deployment runbook {index}",
                    tags=["pnt20", "fixture"],
                )
            query = "deployment rollback health evidence"
            filters = RetrievalFilters()
            scope = {"session": "pnt20"}
            strategy = service._resolve_strategy(
                query=query,
                purpose="verify",
                strategy="auto",
                scope=scope,
                filters=filters,
            )
            candidate_started_ns = time.perf_counter_ns()
            candidates = service._generate_candidates(
                query=query,
                scope=scope,
                filters=filters,
                limit=25,
            )
            candidate_ns = _elapsed_ns(candidate_started_ns)
            rank_started_ns = time.perf_counter_ns()
            ranked = score_records(
                candidates,
                ranking_config=service._ranking_config,
                now=datetime.now(timezone.utc),
            )
            rank_ns = _elapsed_ns(rank_started_ns)
            select_started_ns = time.perf_counter_ns()
            selected = service._select_candidates(
                candidates=ranked,
                strategy=strategy,
                k=5,
            )
            select_ns = _elapsed_ns(select_started_ns)
            blob_started_ns = time.perf_counter_ns()
            items = [
                service._to_retrieved_item(candidate=item, strategy=strategy)
                for item in selected
            ]
            blob_ns = _elapsed_ns(blob_started_ns)
            metrics["phase_timings_ns"] = {
                "retrieval_candidate_query_ns": candidate_ns,
                "retrieval_ranking_ns": rank_ns,
                "retrieval_top_k_ns": select_ns,
                "retrieval_blob_read_ns": blob_ns,
            }
            metrics["phase_timings_ms"] = {
                key.removesuffix("_ns") + "_ms": _ns_to_ms(value)
                for key, value in metrics["phase_timings_ns"].items()
            }
            metrics["retrieval_candidate_count"] = len(candidates)
            metrics["retrieval_selected_count"] = len(selected)
            metrics["retrieval_item_count"] = len(items)
            metrics["retrieval_source_grounding_ok"] = all(
                str(item.ref_id).startswith("fixture://doc-") for item in items
            )
            metrics["retrieval_ranking_drift_count"] = 0
            metrics["retrieval_connection_pressure"] = "single_sqlite_connection"
            metrics["retrieval_measured_change_count"] = 0
            return [
                "Retrieval fixture separates candidate query, ranking, top-k selection, and blob-read phases.",
                "No retrieval tuning is applied because this row first needed source-grounded phase evidence.",
            ]
        finally:
            service.close()

    return _run_with_metrics(
        scenario_id="retrieval_breakdown_profile",
        command="retrieval_fixture:breakdown_profile",
        provider_variance_class=LOCAL_VARIANCE,
        action=action,
    )


def _measure_terminal_render_burst() -> ScenarioRun:
    def action(metrics: dict[str, Any]) -> list[str]:
        from rich.console import Console

        from openminion.cli.interactive.terminal.streaming import TerminalTurnHandle

        refresh_count = 0
        first_refresh_after_chars: int | None = None
        handle = TerminalTurnHandle(Console(file=io.StringIO(), force_terminal=False))

        def _refresh() -> None:
            nonlocal first_refresh_after_chars, refresh_count
            refresh_count += 1
            if first_refresh_after_chars is None:
                first_refresh_after_chars = len(handle._buffer)  # type: ignore[attr-defined]

        handle._refresh_live = _refresh  # type: ignore[method-assign]
        chunks = ["a"] * 120
        started_ns = time.perf_counter_ns()
        for chunk in chunks:
            handle.append_token(chunk)
        render_ns = _elapsed_ns(started_ns)
        metrics["time_to_first_visible_text_ms"] = (
            0 if first_refresh_after_chars == 1 else None
        )
        metrics["phase_timings_ns"] = {"terminal_render_burst_ns": render_ns}
        metrics["phase_timings_ms"] = {"terminal_render_burst_ms": _ns_to_ms(render_ns)}
        metrics["render_chunk_count"] = len(chunks)
        metrics["render_refresh_count"] = refresh_count
        metrics["coalesced_refresh_count"] = max(0, len(chunks) - refresh_count)
        metrics["first_refresh_after_chars"] = first_refresh_after_chars
        metrics["prompt_bytes"] = len("".join(chunks).encode("utf-8"))
        metrics["prompt_tokens_estimated"] = _estimate_tokens("".join(chunks))
        return [
            "Render fixture appends a token burst through TerminalTurnHandle.",
            "First token still forces an immediate refresh; later burst refreshes are coalesced by the handle.",
        ]

    return _run_with_metrics(
        scenario_id="terminal_render_burst",
        command="terminal_fixture:render_burst",
        provider_variance_class=LOCAL_VARIANCE,
        action=action,
    )


def _measure_telemetry_export_queue() -> ScenarioRun:
    def action(metrics: dict[str, Any]) -> list[str]:
        from openminion.base.config import OTELExporterConfig
        from openminion.modules.telemetry.export.otel import (
            OpenTelemetryTraceExporter,
            RecordingOTELTraceSink,
        )
        from openminion.modules.telemetry.schemas import TelemetryEvent

        event_count = 100
        sink = RecordingOTELTraceSink()
        exporter = OpenTelemetryTraceExporter(
            OTELExporterConfig(
                enabled=True,
                endpoint="http://collector.local:4318",
                noncritical_queue_capacity=event_count,
                queue_flush_timeout_seconds=2.0,
            ),
            sink=sink,
        )
        enqueue_started_ns = time.perf_counter_ns()
        accepted = 0
        for index in range(event_count):
            if exporter.export(
                TelemetryEvent(
                    session_id="pnt20",
                    turn_id="queue",
                    event_type="policy.applied",
                    data={
                        "trace_id": f"queue-{index}",
                        "criticality": "noncritical",
                        "value": index,
                    },
                )
            ):
                accepted += 1
        enqueue_ns = _elapsed_ns(enqueue_started_ns)
        flush_started_ns = time.perf_counter_ns()
        exporter.close()
        flush_ns = _elapsed_ns(flush_started_ns)
        stats = exporter.queue_stats()
        metrics["phase_timings_ns"] = {
            "telemetry_queue_enqueue_ns": enqueue_ns,
            "telemetry_queue_flush_ns": flush_ns,
        }
        metrics["phase_timings_ms"] = {
            "telemetry_queue_enqueue_ms": _ns_to_ms(enqueue_ns),
            "telemetry_queue_flush_ms": _ns_to_ms(flush_ns),
        }
        metrics["telemetry_events_enqueued"] = accepted
        metrics["telemetry_events_exported"] = len(sink.records)
        metrics["telemetry_queue_depth"] = stats["queue_depth"]
        metrics["telemetry_queue_drops"] = stats["drops"]
        metrics["telemetry_queue_flush_failures"] = stats["flush_failures"]
        return [
            "OTel export queue fixture enqueues noncritical events, flushes on close, and records drops/failures.",
            "Telemetry storage durability is not changed by this fixture.",
        ]

    return _run_with_metrics(
        scenario_id="telemetry_export_queue",
        command="telemetry_fixture:noncritical_export_queue",
        provider_variance_class=LOCAL_VARIANCE,
        action=action,
    )


def _measure_transcript_retention_growth() -> ScenarioRun:
    def action(metrics: dict[str, Any]) -> list[str]:
        from rich.console import Console

        from openminion.cli.interactive.terminal.transcript import TerminalTranscript
        from openminion.cli.presentation.models import ChatMessage, MessageKind

        message_count = 1500
        retention_limit = 200
        transcript = TerminalTranscript(
            Console(file=io.StringIO(), force_terminal=False),
            max_retained_messages=retention_limit,
        )
        start_rss = _current_rss_bytes()
        started_ns = time.perf_counter_ns()
        for index in range(message_count):
            transcript.push_message(
                ChatMessage(
                    kind=MessageKind.AGENT,
                    sender="agent",
                    body=f"retained message {index}",
                ),
                render=False,
            )
        retention_ns = _elapsed_ns(started_ns)
        end_rss = _current_rss_bytes()
        metrics["phase_timings_ns"] = {"transcript_retention_ns": retention_ns}
        metrics["phase_timings_ms"] = {
            "transcript_retention_ms": _ns_to_ms(retention_ns)
        }
        metrics["transcript_messages_seen"] = message_count
        metrics["retained_messages"] = len(transcript._messages)
        metrics["retention_limit"] = retention_limit
        metrics["copy_last_ok"] = transcript.copy_last_copyable_message() == (
            f"retained message {message_count - 1}"
        )
        metrics["rss_growth_bytes"] = end_rss - start_rss
        metrics["rss_growth_per_message_bytes"] = int(
            (end_rss - start_rss) / message_count
        )
        metrics["prompt_bytes"] = message_count * len("retained message 0000")
        metrics["prompt_tokens_estimated"] = _estimate_tokens(
            "retained message" * message_count
        )
        return [
            "Transcript fixture pushes a long local session into the terminal transcript with an in-memory retention cap.",
            "Durable session history is outside this fixture; it verifies the terminal working set and copy-last behavior only.",
        ]

    return _run_with_metrics(
        scenario_id="transcript_retention_growth",
        command="terminal_fixture:transcript_retention",
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
    if scenario_id == "deterministic_full_turn":
        return _measure_deterministic_full_turn(options)
    if scenario_id == "provider_payload_serialization":
        return _measure_provider_payload_serialization()
    if scenario_id == "required_lane_branch_characterization":
        return _measure_required_lane_branch_characterization()
    if scenario_id == "typeadapter_validation_probe":
        return _measure_typeadapter_validation_probe()
    if scenario_id == "metadata_json_churn":
        return _measure_metadata_json_churn()
    if scenario_id == "provider_connection_reuse_decision":
        return _measure_provider_connection_reuse_decision()
    if scenario_id == "storage_wal_index_matrix":
        return _measure_storage_wal_index_matrix(options)
    if scenario_id == "retrieval_breakdown_profile":
        return _measure_retrieval_breakdown_profile(options)
    if scenario_id == "telemetry_export_queue":
        return _measure_telemetry_export_queue()
    if scenario_id == "terminal_render_burst":
        return _measure_terminal_render_burst()
    if scenario_id == "transcript_retention_growth":
        return _measure_transcript_retention_growth()
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
    identity_errors = _comparison_identity_errors(
        current.get("measurement_identity"),
        baseline_scenario.get("measurement_identity"),
    )
    if identity_errors:
        return {
            "mode": threshold_mode,
            "status": "ineligible",
            "reason": "measurement identity mismatch",
            "identity_errors": identity_errors,
        }
    current_count = int(current.get("count", 0) or 0)
    current_ok = int(current.get("ok_count", 0) or 0)
    if current_ok < current_count:
        return {
            "mode": threshold_mode,
            "status": "fail",
            "reason": "quality fixture failure",
            "sample_count": current_count,
            "ok_count": current_ok,
        }
    if current_count < 5:
        return {
            "mode": threshold_mode,
            "status": "ineligible",
            "reason": "fewer than five comparable samples",
            "sample_count": current_count,
        }
    baseline_wall = dict(baseline_scenario.get("wall_time_ms") or {})
    current_wall = dict(current.get("wall_time_ms") or {})
    baseline_cv = baseline_wall.get("coefficient_of_variation")
    current_cv = current_wall.get("coefficient_of_variation")
    if any(
        isinstance(value, int | float) and float(value) > 0.20
        for value in (baseline_cv, current_cv)
    ):
        return {
            "mode": threshold_mode,
            "status": "ineligible",
            "reason": "timing variance exceeds 0.20 CV",
            "baseline_cv": baseline_cv,
            "current_cv": current_cv,
        }
    baseline_p95 = baseline_wall.get("p95")
    current_p95 = current_wall.get("p95")
    if not isinstance(baseline_p95, int) or not isinstance(current_p95, int):
        return {
            "mode": threshold_mode,
            "status": "not_applicable",
            "reason": "missing comparable wall p95",
        }
    ratio = round(current_p95 / float(max(1, baseline_p95)), 4)
    if ratio <= 1.10:
        status = "pass"
    else:
        status = (
            "warn" if current.get("warn_only") or threshold_mode != "hard" else "fail"
        )
    return {
        "mode": threshold_mode,
        "status": status,
        "baseline_wall_p95_ms": baseline_p95,
        "current_wall_p95_ms": current_p95,
        "ratio": ratio,
        "regression_ratio": 1.10,
    }


def _comparison_identity_errors(
    current_identity: Any,
    baseline_identity: Any,
) -> list[str]:
    if not isinstance(current_identity, dict) or not isinstance(
        baseline_identity, dict
    ):
        return ["missing measurement identity"]
    errors: list[str] = []
    for key in (
        "artifact_schema_version",
        "scenario_id",
        "command",
        "fixture_revision",
        "measured_boundary",
        "python_version",
        "platform",
    ):
        if current_identity.get(key) != baseline_identity.get(key):
            errors.append(key)
    current_config = current_identity.get("runtime_config")
    baseline_config = baseline_identity.get("runtime_config")
    if not isinstance(current_config, dict) or not isinstance(baseline_config, dict):
        errors.append("runtime_config")
    else:
        for key in (
            "python_executable",
            "workspace_root",
            "data_root",
            "include_importtime",
            "profile",
            "warmup_runs",
        ):
            if current_config.get(key) != baseline_config.get(key):
                errors.append(f"runtime_config.{key}")
    return errors


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
        sample_artifacts = [
            str(run.get("artifact_path") or "")
            for run in scenario_runs
            if str(run.get("artifact_path") or "").strip()
        ]
        scenarios[scenario_id] = {
            "count": len(scenario_runs),
            "ok_count": sum(1 for run in scenario_runs if run.get("ok")),
            "measurement_identity": dict(
                scenario_runs[0].get("measurement_identity") or {}
            ),
            "sample_artifacts": sample_artifacts,
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
        "measurement_identity": run.measurement_identity,
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
    artifact["artifact_path"] = str(path)
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


def _hard_gate_failures(summary: dict[str, Any]) -> list[str]:
    if str(summary.get("threshold_mode", "") or "") != "hard":
        return []
    failures: list[str] = []
    scenarios = summary.get("scenarios")
    if not isinstance(scenarios, dict):
        return failures
    for scenario_id, payload in scenarios.items():
        if not isinstance(payload, dict):
            continue
        result = payload.get("threshold_result")
        if isinstance(result, dict) and result.get("status") == "fail":
            failures.append(str(scenario_id))
    return failures


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
    hard_failures = _hard_gate_failures(summary)
    if hard_failures:
        print(
            "[performance-baseline] hard gate failed: " + ", ".join(hard_failures),
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - script entrypoint
    raise SystemExit(main())
