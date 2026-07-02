"""Measure local OpenMinion performance baseline scenarios."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterable
from dataclasses import dataclass
import json
import os
from pathlib import Path
import platform
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

ARTIFACT_SCHEMA_VERSION = "pbhg.baseline.v1"
LANE_ARTIFACT_DIR = "openminion-performance-baseline-harness-and-gates-2026-07-01"
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


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def _estimate_tokens(text: str, chars_per_token: float = 4.0) -> int:
    return max(0, int(len(text) / max(0.1, chars_per_token)))


def _base_metrics() -> dict[str, Any]:
    return {
        "wall_time_ms": None,
        "time_to_first_visible_text_ms": None,
        "phase_timings_ms": {},
        "provider_round_trip_ms": None,
        "context_assembly_ms": None,
        "prompt_tokens_estimated": None,
        "prompt_bytes": None,
        "tool_call_count": 0,
        "duplicate_call_count": 0,
        "rss_start_bytes": _current_rss_bytes(),
        "rss_end_bytes": None,
        "rss_delta_bytes": None,
        "tracemalloc_current_bytes": None,
        "tracemalloc_peak_bytes": None,
        "import_self_us": None,
        "import_cumulative_us": None,
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


def _run_with_metrics(
    *,
    scenario_id: str,
    command: str,
    provider_variance_class: str,
    provider_profile: str = "none",
    action: Callable[[dict[str, Any]], list[str]],
) -> ScenarioRun:
    tracemalloc.start()
    metrics = _base_metrics()
    started = time.perf_counter()
    notes: list[str] = []
    try:
        notes.extend(action(metrics))
        metrics["wall_time_ms"] = _elapsed_ms(started)
        return ScenarioRun(
            scenario_id=scenario_id,
            command=command,
            provider_profile=provider_profile,
            provider_variance_class=provider_variance_class,
            metrics=_finish_metrics(metrics),
            notes=notes,
        )
    except Exception as exc:  # noqa: BLE001 - baseline artifacts must record failure
        metrics["wall_time_ms"] = _elapsed_ms(started)
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


def _command_env(options: RunOptions, *, data_root: Path | None = None) -> dict[str, str]:
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


def _run_subprocess(command: list[str], *, options: RunOptions, data_root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=options.workspace_root / "openminion",
        env=_command_env(options, data_root=data_root),
        text=True,
        capture_output=True,
        timeout=options.timeout_seconds,
        check=False,
    )


def _parse_importtime(stderr: str) -> tuple[int | None, int | None]:
    self_values: list[int] = []
    cumulative_values: list[int] = []
    for line in stderr.splitlines():
        if not line.startswith("import time:"):
            continue
        parts = [part.strip() for part in line.removeprefix("import time:").split("|")]
        if len(parts) < 2:
            continue
        try:
            self_values.append(int(parts[0]))
            cumulative_values.append(int(parts[1]))
        except ValueError:
            continue
    return (
        max(self_values) if self_values else None,
        max(cumulative_values) if cumulative_values else None,
    )


def _capture_importtime(
    *,
    scenario_id: str,
    command: list[str],
    options: RunOptions,
    data_root: Path,
) -> tuple[int | None, int | None, str | None]:
    if not options.include_importtime:
        return None, None, None
    import_command = [str(options.python), "-X", "importtime", *command[1:]]
    completed = _run_subprocess(import_command, options=options, data_root=data_root)
    self_us, cumulative_us = _parse_importtime(completed.stderr)
    out_dir = options.output_root / "importtime"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{_utc_timestamp()}-{scenario_id}.txt"
    path.write_text(completed.stderr, encoding="utf-8")
    return self_us, cumulative_us, str(path)


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
            metrics["phase_timings_ms"] = {
                "subprocess_exit_code": completed.returncode
            }
            prompt_ready = "focused single-agent shell" in completed.stdout.lower()
            metrics["prompt_ready_marker"] = prompt_ready
            if not prompt_ready or completed.returncode != 0:
                metrics["stderr_tail"] = completed.stderr[-500:]
            self_us, cumulative_us, import_path = _capture_importtime(
                scenario_id=scenario_id,
                command=command,
                options=options,
                data_root=data_root,
            )
            metrics["import_self_us"] = self_us
            metrics["import_cumulative_us"] = cumulative_us
            notes = [
                "Startup command uses `openminion focus --help` as a non-interactive prompt-readiness proxy.",
                "RSS fields measure the harness process; child process max RSS is not portable in this runner.",
            ]
            if import_path:
                notes.append(f"Import-time stderr captured at {import_path}.")
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
        metrics["prompt_tokens_estimated"] = _estimate_tokens(prompt)
        metrics["prompt_bytes"] = len(prompt.encode("utf-8"))
        metrics["transcript_bytes"] = len(payload.encode("utf-8"))
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
        facts = {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "cwd": str(Path.cwd()),
            "time_ns": time.time_ns(),
        }
        serialized = json.dumps(facts, sort_keys=True)
        metrics["phase_timings_ms"] = {"local_status_collect_ms": 0}
        metrics["prompt_tokens_estimated"] = _estimate_tokens(serialized)
        metrics["prompt_bytes"] = len(serialized.encode("utf-8"))
        metrics["tool_call_count"] = 1
        return ["Local deterministic status/tool-style fixture; no provider or network work."]

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
                    "and tool observations that must be packed predictably. "
                    * 8
                ),
            )
            for index in range(24)
        ]
        started = time.perf_counter()
        budgeted = assemble_budgeted_context(
            system_messages=system,
            history_messages=history,
            budget=ContextBudgetConfig(max_tokens=900, chars_per_token=4.0),
        )
        context_ms = _elapsed_ms(started)
        telemetry = budgeted.telemetry.to_dict()
        metrics["context_assembly_ms"] = context_ms
        metrics["phase_timings_ms"] = {"context_assembly_ms": context_ms}
        metrics["prompt_tokens_estimated"] = telemetry["estimated_tokens_total"]
        metrics["prompt_bytes"] = sum(
            len(str(message.body or "").encode("utf-8"))
            for message in budgeted.messages
        )
        metrics["segment_count"] = len(history)
        metrics["messages_after_trim"] = telemetry["messages_after_trim"]
        metrics["trimmed_count"] = telemetry["trimmed_count"]
        return ["Uses the existing context budget owner to create a replayable context-heavy measurement."]

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
            iteration_started = time.perf_counter()
            payload = {
                "index": index,
                "platform": platform.system(),
                "python": platform.python_version(),
                "monotonic_ns": time.monotonic_ns(),
            }
            _ = json.dumps(payload, sort_keys=True)
            current, peak = tracemalloc.get_traced_memory()
            iteration_metrics.append(
                {
                    "iteration": index,
                    "wall_time_ms": _elapsed_ms(iteration_started),
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
        return _measure_focus_startup(scenario_id=scenario_id, options=options, cold=True)
    if scenario_id == "warm_focus_startup":
        return _measure_focus_startup(scenario_id=scenario_id, options=options, cold=False)
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
    if len(values) == 1:
        return values[0]
    return int(statistics.quantiles(values, n=100)[pct - 1])


def _metric_summary(values: Iterable[Any]) -> dict[str, int | None]:
    ints = sorted(int(value) for value in values if isinstance(value, int))
    if not ints:
        return {"min": None, "median": None, "p95": None, "max": None}
    return {
        "min": ints[0],
        "median": int(statistics.median(ints)),
        "p95": _percentile(ints, 95) if len(ints) >= 5 else None,
        "max": ints[-1],
    }


def summarize_runs(runs: list[dict[str, Any]]) -> dict[str, Any]:
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
            "rss_delta_bytes": _metric_summary(
                metric.get("rss_delta_bytes") for metric in metrics
            ),
            "tracemalloc_peak_bytes": _metric_summary(
                metric.get("tracemalloc_peak_bytes") for metric in metrics
            ),
            "prompt_tokens_estimated": _metric_summary(
                metric.get("prompt_tokens_estimated") for metric in metrics
            ),
            "warn_only": scenario_runs[0].get("provider_variance_class")
            == WARN_ONLY_VARIANCE,
        }
    return {
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "generated_at_utc": _utc_timestamp(),
        "scenario_count": len(scenarios),
        "run_count": len(runs),
        "scenarios": scenarios,
    }


def _run_to_artifact(run: ScenarioRun, *, run_index: int, options: RunOptions) -> dict[str, Any]:
    return {
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "scenario_id": run.scenario_id,
        "run_id": f"{_utc_timestamp()}-{run.scenario_id}-{run_index}-{uuid4().hex[:8]}",
        "timestamp_utc": _utc_timestamp(),
        "git_head": _git_head(options.workspace_root),
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "command": run.command,
        "provider_profile": run.provider_profile,
        "provider_variance_class": run.provider_variance_class,
        "metrics": run.metrics,
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
        "| Scenario | Runs | Variance | Wall median ms | Wall max ms | Peak alloc max bytes | Warn only |",
        "| --- | ---: | --- | ---: | ---: | ---: | --- |",
    ]
    for scenario_id, data in summary["scenarios"].items():
        wall = data["wall_time_ms"]
        peak = data["tracemalloc_peak_bytes"]
        lines.append(
            "| "
            f"`{scenario_id}` | {data['count']} | `{data['provider_variance_class']}` | "
            f"{wall['median']} | {wall['max']} | {peak['max']} | {data['warn_only']} |"
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
        per_scenario_runs = 1 if scenario_id == "repeated_local_turns" else options.runs
        for run_index in range(per_scenario_runs):
            print(f"[performance-baseline] {scenario_id} run {run_index + 1}/{per_scenario_runs}")
            run = run_scenario(scenario_id, options)
            artifact = _run_to_artifact(run, run_index=run_index, options=options)
            _write_run_artifact(artifact, options.output_root)
            artifacts.append(artifact)
            if not run.ok:
                print(f"  error: {run.error}")

    summary = summarize_runs(artifacts)
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
        help="Reserved for explicit cProfile diagnostic captures.",
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
        )
        scenarios = _scenario_list(str(args.scenarios))
        summary = run_baseline(options, scenarios)
    except Exception as exc:  # noqa: BLE001 - script should return operator-friendly error
        print(f"performance baseline failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(f"[performance-baseline] wrote {options.output_root / 'summary.json'}")
    print(f"[performance-baseline] scenarios={summary['scenario_count']} runs={summary['run_count']}")
    return 0


if __name__ == "__main__":  # pragma: no cover - script entrypoint
    raise SystemExit(main())
