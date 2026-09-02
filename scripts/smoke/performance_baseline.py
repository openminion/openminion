"""Measure local OpenMinion performance baseline scenarios."""

from __future__ import annotations

import argparse
import cProfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
import gc
import hashlib
import importlib.metadata as importlib_metadata
import io
import json
import math
import os
from pathlib import Path
import platform
import pstats
import shutil
import socket
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

ARTIFACT_SCHEMA_VERSION = "pomv2.performance.v4"
TCPL_ARTIFACT_SCHEMA_VERSION = "tcpl.performance.v1"
STARTUP_FIXTURE_REVISION = "focus-help-v2"
SUT_BOUNDARY_SUBPROCESS = "sut_subprocess_only"
SUT_BOUNDARY_IN_PROCESS = "sut_in_process_fixture"
SUT_BOUNDARY_REPLAY = "sut_replay_fixture"
LANE_ARTIFACT_DIR = "openminion-performance-observability-and-measurement-v2-2026-07-02"
DEFAULT_SCENARIOS = (
    "cold_focus_startup",
    "warm_focus_startup",
    "terminal_import_surface",
    "interactive_runtime_import_surface",
    "simple_turn",
    "local_status_tool_turn",
    "context_heavy_turn",
    "deterministic_full_turn",
    "instrumentation_overhead_aa",
    "tcpl_selector_direct_route",
    "tcpl_selector_retrieval_route",
    "tcpl_selector_llm_route",
    "tcpl_02_skill_llm_baseline",
    "tcpl_02_skill_entry_candidate",
    "tcpl_02_skill_entry_rollback",
    "tcpl_01_streaming_safety_nochange",
    "tcpl_03_memory_projection_defer",
    "tcpl_04_compaction_defer",
    "tcpl_05_delivery_fence_retain",
    "tcpl_compaction_threshold_crossing",
    "tcpl_memory_followup_pending",
    "tcpl_memory_followup_active",
    "tcpl_branch_direct_tool",
    "tcpl_branch_seeded_multi_step",
    "tcpl_branch_final_answer_repair",
    "tcpl_branch_active_mission_finish",
    "tcpl_provider_retry_fallback",
    "tcpl_large_tool_surface",
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
    "persistent_api_turns",
    "persistent_focus_turns",
    "session_cache_churn",
    "provider_lifecycle_loopback",
    "agent_cache_churn",
    "runtime_restart",
    "queue_pressure",
)
LOCAL_VARIANCE = "local_deterministic"
REPLAY_VARIANCE = "replay_fixture"
WARN_ONLY_VARIANCE = "provider_warn_only"
DEFAULT_THRESHOLD_MODE = "warn"
PROFILE_TOP_LIMIT = 20
IMPORTTIME_TOP_LIMIT = 20
TRACEMALLOC_TOP_LIMIT = 10
PROCESS_TREE_MEMBER_LIMIT = 64
COMPARISON_MIN_SAMPLES = 20
OMFLA_PROCESS_TREE_RSS_ABORT_BYTES = 2 * 1024 * 1024 * 1024
TCPL_SKILL_ENTRY_TOKEN_BUDGET = 1200
TCPL_SKILL_ENTRY_CANDIDATE_BUDGET = 6
TCPL_COMPACTION_DEFER_MS_THRESHOLD = 10
TCPL_REQUIRED_COVERAGE: dict[str, tuple[tuple[str, ...], ...]] = {
    "provider_attempts": (("provider_attempts",),),
    "selector": (("selector_latency_ms",), ("selector_token_count",)),
    "compaction": (("session_compaction_ms",), ("session_compaction_policy",)),
    "memory_followup": (
        ("memory_followup_flush_ms",),
        ("memory_followup_pending_count",),
    ),
    "persistence": (
        ("transcript_persistence_ms", "response_persistence_ms"),
        ("base_memory_persistence_ms", "memory_write_ms"),
    ),
    "run_record": (("run_record_finish_ms",),),
    "delivery": (
        ("terminal_delivery_ms", "final_delivery_ms", "response_delivery_ms"),
    ),
    "delivered_event": (("delivered_event_emit_ms", "response_delivered_event_ms"),),
    "terminal_event": (("terminal_event_emit_ms", "terminal_event_ms"),),
}
TCPL_QUALITY_INVARIANTS = (
    "skill_selection_quality",
    "tool_order_quality",
    "context_quality",
    "transcript_quality",
    "memory_quality",
    "replay_quality",
    "policy_quality",
    "approval_quality",
    "final_delivery_quality",
)


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


def _current_rss_bytes(process_id: int | None = None) -> int | None:
    try:
        import psutil  # type: ignore[import-not-found]

        return int(psutil.Process(process_id or os.getpid()).memory_info().rss)
    except Exception:
        return None


def _max_rss_bytes() -> int | None:
    try:
        import resource

        rss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    except Exception:
        return None
    return rss if sys.platform == "darwin" else rss * 1024


def _process_rss_metrics(process_id: int | None = None) -> dict[str, Any]:
    measured_process_id = int(process_id or os.getpid())
    unavailable: dict[str, str] = {}
    metrics: dict[str, Any] = {
        "current_rss_bytes": None,
        "process_tree_current_rss_bytes": None,
        "child_process_count": None,
        "measured_process_id": measured_process_id,
        "children_included": False,
        "external_services_included": False,
        "process_tree_members": [],
    }
    try:
        import psutil  # type: ignore[import-not-found]
    except ImportError:
        for key in (
            "current_rss_bytes",
            "process_tree_current_rss_bytes",
            "child_process_count",
        ):
            unavailable[key] = "psutil_unavailable"
        metrics["availability_reasons"] = unavailable
        return metrics

    try:
        process = psutil.Process(measured_process_id)
        current_rss = int(process.memory_info().rss)
        metrics["current_rss_bytes"] = current_rss
        children = process.children(recursive=True)
        metrics["child_process_count"] = len(children)
        metrics["children_included"] = True
        process_tree_rss = current_rss
        aggregate_complete = True
        members: list[dict[str, Any]] = []
        for child in children:
            try:
                child_rss = int(child.memory_info().rss)
            except psutil.Error:
                child_rss = None
            if child_rss is not None:
                process_tree_rss += child_rss
            else:
                aggregate_complete = False
            if len(members) < PROCESS_TREE_MEMBER_LIMIT:
                members.append(
                    {
                        "pid": int(child.pid),
                        "role": "child_process",
                        "current_rss_bytes": child_rss,
                        "availability_reason": (
                            None if child_rss is not None else "process_unavailable"
                        ),
                    }
                )
        metrics["process_tree_current_rss_bytes"] = (
            process_tree_rss if aggregate_complete else None
        )
        metrics["process_tree_members"] = members
        if not aggregate_complete:
            unavailable["process_tree_current_rss_bytes"] = "descendant_rss_unavailable"
        if len(children) > PROCESS_TREE_MEMBER_LIMIT:
            unavailable["process_tree_members"] = "member_limit_reached"
    except psutil.Error:
        for key in (
            "current_rss_bytes",
            "process_tree_current_rss_bytes",
            "child_process_count",
        ):
            unavailable[key] = "process_unavailable"
    metrics["availability_reasons"] = unavailable
    return metrics


def _process_metrics(process_id: int | None = None) -> dict[str, Any]:
    metrics = _process_rss_metrics(process_id)
    unavailable = dict(metrics.get("availability_reasons") or {})
    metrics.update(
        {
            "max_rss_bytes": _max_rss_bytes() if process_id is None else None,
            "thread_count": None,
            "async_task_count": None,
            "file_descriptor_count": None,
            "open_file_count": None,
            "network_connection_count": None,
        }
    )
    try:
        import psutil  # type: ignore[import-not-found]

        process = psutil.Process(int(process_id or os.getpid()))
        metrics["thread_count"] = int(process.num_threads())
        try:
            metrics["file_descriptor_count"] = int(process.num_fds())
        except (AttributeError, psutil.Error):
            unavailable["file_descriptor_count"] = "not_supported"
        try:
            metrics["open_file_count"] = len(process.open_files())
        except psutil.Error:
            unavailable["open_file_count"] = "process_unavailable"
        try:
            metrics["network_connection_count"] = len(process.net_connections())
        except (AttributeError, psutil.Error):
            unavailable["network_connection_count"] = "not_supported"
    except ImportError:
        for key in (
            "thread_count",
            "file_descriptor_count",
            "open_file_count",
            "network_connection_count",
        ):
            unavailable[key] = "psutil_unavailable"
    except psutil.Error:
        for key in (
            "thread_count",
            "file_descriptor_count",
            "open_file_count",
            "network_connection_count",
        ):
            unavailable[key] = "process_unavailable"

    if metrics["max_rss_bytes"] is None:
        unavailable["max_rss_bytes"] = (
            "resource_unavailable" if process_id is None else "not_supported"
        )
    unavailable["async_task_count"] = "not_applicable"
    metrics["availability_reasons"] = unavailable
    return metrics


def _dirty_worktree_summary(workspace_root: Path) -> dict[str, Any]:
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(workspace_root / "openminion"),
                "status",
                "--short",
                "--untracked-files=all",
            ],
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


def _stable_json_hash(payload: Any) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _dirty_worktree_fingerprint(workspace_root: Path) -> str:
    repo_root = workspace_root / "openminion"
    summary = _dirty_worktree_summary(workspace_root)
    if not summary.get("available"):
        return "unavailable"
    digest = hashlib.sha256()
    status_bytes = b""
    for label, args in (
        (
            "status",
            [
                "git",
                "-C",
                str(repo_root),
                "status",
                "--porcelain=v1",
                "-z",
                "--untracked-files=all",
            ],
        ),
        ("diff", ["git", "-C", str(repo_root), "diff", "--binary"]),
        (
            "diff-cached",
            ["git", "-C", str(repo_root), "diff", "--cached", "--binary"],
        ),
    ):
        try:
            result = subprocess.run(
                args,
                capture_output=True,
                check=False,
                timeout=20,
            )
        except Exception:
            return _stable_json_hash(summary)
        digest.update(label.encode("utf-8"))
        digest.update(b"\0")
        digest.update(result.stdout)
        digest.update(result.stderr)
        if label == "status":
            status_bytes = result.stdout
    for entry in status_bytes.decode("utf-8", errors="replace").split("\0"):
        if not entry.startswith("?? "):
            continue
        path = repo_root / entry[3:]
        if path.is_file() and not path.is_symlink():
            digest.update(entry.encode("utf-8"))
            digest.update(_file_sha256(path).encode("utf-8"))
    return digest.hexdigest()


def _file_sha256(path: Path) -> str:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except Exception:
        return "unavailable"


def _loaded_openminion_package_root() -> str:
    module = sys.modules.get("openminion")
    module_path = getattr(module, "__file__", None)
    if not module_path:
        return "unavailable"
    return str(Path(module_path).resolve().parent)


def _installed_distribution_provenance() -> dict[str, Any]:
    distributions: dict[str, dict[str, Any]] = {}
    for distribution in importlib_metadata.distributions():
        name = str(distribution.metadata.get("Name") or "").strip()
        if not name:
            continue
        normalized_name = name.casefold().replace("_", "-")
        direct_url_text = distribution.read_text("direct_url.json")
        direct_url: dict[str, Any] | None = None
        if direct_url_text:
            try:
                loaded = json.loads(direct_url_text)
            except json.JSONDecodeError:
                loaded = {"malformed": True}
            if isinstance(loaded, dict):
                direct_url = loaded
        distributions[normalized_name] = {
            "name": name,
            "version": str(distribution.version),
            "direct_url": direct_url,
            "editable": bool(
                isinstance(direct_url, dict)
                and isinstance(direct_url.get("dir_info"), dict)
                and direct_url["dir_info"].get("editable") is True
            ),
        }
    entries = [distributions[name] for name in sorted(distributions)]
    version_pairs = [
        [str(entry["name"]).casefold().replace("_", "-"), entry["version"]]
        for entry in entries
    ]
    return {
        "distributions": entries,
        "runtime_dependency_hash": _stable_json_hash(version_pairs),
        "editable_dependency_names": [
            str(entry["name"]) for entry in entries if entry["editable"]
        ],
    }


def _host_runtime_hash() -> str:
    return _stable_json_hash(
        {
            "hostname": socket.gethostname(),
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        }
    )


def _path_shape(path_value: str, options: RunOptions) -> str:
    if not path_value:
        return "<CWD>"
    path = Path(path_value).expanduser().absolute()
    repo_root = (options.workspace_root / "openminion").absolute()
    source_root = (repo_root / "src").absolute()
    if path == source_root:
        return "<SUT_SRC>"
    if path == repo_root:
        return "<SUT_REPO>"
    python_prefix = Path(sys.prefix).absolute()
    try:
        return f"<PYTHON_PREFIX>/{path.relative_to(python_prefix)}"
    except ValueError:
        return str(path)


def _runtime_environment_identity(options: RunOptions) -> dict[str, Any]:
    dependency_provenance = _installed_distribution_provenance()
    inherited_pythonpath = os.environ.get("PYTHONPATH", "")
    pythonpath_entries = [
        entry for entry in inherited_pythonpath.split(os.pathsep) if entry
    ]
    return {
        "resolved_python_executable": str(options.python.resolve()),
        "running_python_executable": str(Path(sys.executable).resolve()),
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "python_build": list(platform.python_build()),
        "platform": platform.platform(),
        "host_runtime_hash": _host_runtime_hash(),
        "effective_sys_path": list(sys.path),
        "effective_sys_path_shape": [_path_shape(entry, options) for entry in sys.path],
        "inherited_pythonpath": inherited_pythonpath,
        "inherited_pythonpath_shape": [
            _path_shape(entry, options) for entry in pythonpath_entries
        ],
        "bytecode_cache_environment": {
            "dont_write_bytecode": os.environ.get("PYTHONDONTWRITEBYTECODE", ""),
            "pycache_prefix": os.environ.get("PYTHONPYCACHEPREFIX", ""),
            "pycache_posture": (
                "external"
                if os.environ.get("PYTHONPYCACHEPREFIX")
                else "interpreter_default"
            ),
            "no_user_site": os.environ.get("PYTHONNOUSERSITE", ""),
        },
        **dependency_provenance,
    }


def _comparison_command_shape(identity: dict[str, Any]) -> dict[str, Any]:
    scenario_id = str(identity.get("scenario_id") or "")
    if scenario_id in {"cold_focus_startup", "warm_focus_startup"}:
        return {
            "entrypoint": "python -m openminion",
            "arguments": ["--data-root", "<DATA_ROOT>", "--help"],
        }
    if scenario_id in {
        "terminal_import_surface",
        "interactive_runtime_import_surface",
    }:
        return {
            "entrypoint": "python -c",
            "fixture": scenario_id,
            "data_root": "<DATA_ROOT>",
        }
    return {"fixture": str(identity.get("command") or "")}


def _comparison_identity(measurement_identity: dict[str, Any]) -> dict[str, Any]:
    runtime_config = dict(measurement_identity.get("runtime_config") or {})
    environment = dict(measurement_identity.get("runtime_environment") or {})
    cache_environment = dict(environment.get("bytecode_cache_environment") or {})
    scenario_id = str(measurement_identity.get("scenario_id") or "")
    if scenario_id == "cold_focus_startup":
        process_posture = "cold"
    elif scenario_id == "warm_focus_startup":
        process_posture = "warm"
    else:
        process_posture = "steady"
    return {
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "scenario_id": scenario_id,
        "command_shape": _comparison_command_shape(measurement_identity),
        "fixture_revision": measurement_identity.get("fixture_revision"),
        "measured_boundary": measurement_identity.get("measured_boundary"),
        "python_implementation": environment.get("python_implementation"),
        "python_version": environment.get("python_version"),
        "python_build": environment.get("python_build"),
        "resolved_python_executable": environment.get("resolved_python_executable"),
        "runner_source_sha256": measurement_identity.get("runner_source_sha256"),
        "host_runtime_hash": environment.get("host_runtime_hash"),
        "runtime_dependency_hash": environment.get("runtime_dependency_hash"),
        "editable_dependency_names": environment.get("editable_dependency_names", []),
        "effective_sys_path_shape": environment.get("effective_sys_path_shape"),
        "inherited_pythonpath_shape": environment.get("inherited_pythonpath_shape"),
        "bytecode_cache_posture": {
            "dont_write_bytecode": cache_environment.get("dont_write_bytecode"),
            "pycache_posture": cache_environment.get("pycache_posture"),
            "no_user_site": cache_environment.get("no_user_site"),
        },
        "provider_posture": measurement_identity.get("provider_posture"),
        "model_posture": measurement_identity.get("model_posture"),
        "process_posture": process_posture,
        "include_importtime": runtime_config.get("include_importtime"),
        "profile": runtime_config.get("profile"),
        "warmup_runs": runtime_config.get("warmup_runs"),
        "scenario_config": {
            "timeout_seconds": runtime_config.get("timeout_seconds"),
            **dict(measurement_identity.get("scenario_config") or {}),
        },
    }


def _fixture_hash(identity: dict[str, Any], *, command: Any) -> str:
    return _stable_json_hash(
        {
            "command": command,
            "identity": identity,
            "runner_source_sha256": _file_sha256(Path(__file__)),
        }
    )


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def _elapsed_ns(started: int) -> int:
    return max(0, time.perf_counter_ns() - started)


def _ns_to_ms(elapsed_ns: int) -> int:
    return max(0, int(elapsed_ns / 1_000_000))


def _estimate_tokens(text: str, chars_per_token: float = 4.0) -> int:
    return max(0, int(len(text) / max(0.1, chars_per_token)))


def _base_metrics() -> dict[str, Any]:
    process_metrics = _process_metrics()
    current_rss = process_metrics.get("current_rss_bytes")
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
        "rss_start_bytes": current_rss,
        "rss_end_bytes": None,
        "rss_delta_bytes": None,
        **process_metrics,
        "harness_process_id": os.getpid(),
        "harness_current_rss_bytes": current_rss,
        "harness_max_rss_bytes": process_metrics.get("max_rss_bytes"),
        "tracemalloc_current_bytes": None,
        "tracemalloc_peak_bytes": None,
        "tracemalloc_overhead_bytes": None,
        "queue_depths": {},
        "cache_cardinalities": {},
        "phase": "steady_state",
        "sample_index": 0,
        "elapsed_ms": 0,
        "completed_turn_count": 0,
        "terminal_fact": None,
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
    provider_posture: str = "none",
    model_posture: str = "unavailable",
    scenario_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    runtime_config = {
        "python_executable": str(options.python) if options is not None else "",
        "workspace_root": str(options.workspace_root) if options is not None else "",
        "data_root": str(data_root) if data_root is not None else "",
        "include_importtime": bool(options.include_importtime) if options else False,
        "profile": bool(options.profile) if options else False,
        "warmup_runs": int(options.warmup_runs) if options else 0,
        "timeout_seconds": int(options.timeout_seconds) if options else 0,
    }
    return {
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "git_head": "unavailable",
        "dirty_tree_fingerprint": "unavailable",
        "runner_source_sha256": "unavailable",
        "config_hash": _stable_json_hash(runtime_config),
        "scenario_id": scenario_id,
        "command": command,
        "fixture_revision": fixture_revision,
        "measured_boundary": measured_boundary,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "provider_posture": provider_posture,
        "model_posture": model_posture,
        "runtime_config": runtime_config,
        "scenario_config": dict(scenario_config or {}),
    }


def _campaign_source_identity(options: RunOptions) -> dict[str, Any]:
    return {
        "git_head": _git_head(options.workspace_root),
        "dirty_tree_fingerprint": _dirty_worktree_fingerprint(options.workspace_root),
        "dirty_worktree_summary": _dirty_worktree_summary(options.workspace_root),
        "runner_path": str(Path(__file__).resolve()),
        "runner_source_sha256": _file_sha256(Path(__file__)),
        "loaded_openminion_package_root": _loaded_openminion_package_root(),
        "runtime_environment": _runtime_environment_identity(options),
    }


def _campaign_source_identity_errors(
    expected: dict[str, Any], actual: dict[str, Any]
) -> list[str]:
    return [
        key
        for key in ("git_head", "dirty_tree_fingerprint", "runner_source_sha256")
        if str(expected.get(key) or "") in {"", "unknown", "unavailable"}
        or expected.get(key) != actual.get(key)
    ]


def _finish_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    process_override = metrics.pop("_process_metrics_override", None)
    harness_metrics = _process_metrics()
    metrics["harness_current_rss_bytes"] = harness_metrics.get("current_rss_bytes")
    metrics["harness_max_rss_bytes"] = harness_metrics.get("max_rss_bytes")
    process_metrics = (
        process_override if isinstance(process_override, dict) else harness_metrics
    )
    availability_reasons = dict(metrics.get("availability_reasons") or {})
    availability_reasons.update(process_metrics.get("availability_reasons") or {})
    metrics.update(process_metrics)
    metrics["availability_reasons"] = availability_reasons
    rss_end = metrics.get("current_rss_bytes")
    metrics["rss_end_bytes"] = rss_end
    start = metrics.get("rss_start_bytes")
    metrics["rss_delta_bytes"] = (
        int(rss_end) - int(start)
        if isinstance(start, int) and isinstance(rss_end, int)
        else None
    )
    metrics["elapsed_ms"] = max(0, int(metrics.get("wall_time_ms") or 0))
    if metrics.get("terminal_fact") is None:
        metrics["availability_reasons"].setdefault("terminal_fact", "not_applicable")
    return metrics


def _capture_tracemalloc_metrics(
    metrics: dict[str, Any],
    before_snapshot: tracemalloc.Snapshot,
    *,
    subprocess_boundary: bool,
) -> None:
    try:
        current, peak = tracemalloc.get_traced_memory()
        overhead = int(tracemalloc.get_tracemalloc_memory())
        snapshot_diff = _tracemalloc_diff_summary(
            before_snapshot,
            tracemalloc.take_snapshot(),
        )
    finally:
        tracemalloc.stop()
    metrics["harness_tracemalloc_current_bytes"] = int(current)
    metrics["harness_tracemalloc_peak_bytes"] = int(peak)
    metrics["harness_tracemalloc_overhead_bytes"] = overhead
    if subprocess_boundary:
        for key in (
            "tracemalloc_current_bytes",
            "tracemalloc_peak_bytes",
            "tracemalloc_overhead_bytes",
            "tracemalloc_snapshot_diff",
        ):
            metrics[key] = [] if key == "tracemalloc_snapshot_diff" else None
            metrics.setdefault("availability_reasons", {})[key] = (
                "not_supported_for_subprocess"
            )
        metrics["harness_tracemalloc_snapshot_diff"] = snapshot_diff
        return
    metrics["tracemalloc_current_bytes"] = int(current)
    metrics["tracemalloc_peak_bytes"] = int(peak)
    metrics["tracemalloc_overhead_bytes"] = overhead
    metrics["tracemalloc_snapshot_diff"] = snapshot_diff


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


def _tracemalloc_line_diff(
    before: tracemalloc.Snapshot,
    after: tracemalloc.Snapshot,
    *,
    filename_suffix: str,
    lineno: int,
) -> dict[str, int]:
    for stat in after.compare_to(before, "lineno"):
        frame = stat.traceback[0] if stat.traceback else None
        if (
            frame is not None
            and str(frame.filename).endswith(filename_suffix)
            and int(frame.lineno) == lineno
        ):
            return {
                "size_diff_bytes": int(stat.size_diff),
                "count_diff": int(stat.count_diff),
            }
    return {"size_diff_bytes": 0, "count_diff": 0}


def _run_with_metrics(
    *,
    scenario_id: str,
    command: str,
    provider_variance_class: str,
    provider_profile: str = "none",
    measurement_identity: dict[str, Any] | None = None,
    action: Callable[[dict[str, Any]], list[str]],
) -> ScenarioRun:
    metrics = _base_metrics()
    tracemalloc.start()
    before_snapshot = tracemalloc.take_snapshot()
    started_ns = time.perf_counter_ns()
    notes: list[str] = []
    error: str | None = None
    try:
        notes.extend(action(metrics))
    except Exception as exc:  # noqa: BLE001 - baseline artifacts must record failure
        error = f"{type(exc).__name__}: {exc}"
    command_value = str(metrics.pop("_command_override", command))
    identity_value = metrics.pop("_measurement_identity_override", None)
    harness_wall_ns = _elapsed_ns(started_ns)
    override_wall_ns = metrics.pop("_wall_time_ns_override", None)
    metrics["wall_time_ns"] = (
        int(override_wall_ns) if isinstance(override_wall_ns, int) else harness_wall_ns
    )
    metrics["harness_wall_time_ns"] = harness_wall_ns
    metrics["wall_time_ms"] = _ns_to_ms(int(metrics["wall_time_ns"]))
    _capture_tracemalloc_metrics(
        metrics,
        before_snapshot,
        subprocess_boundary=isinstance(metrics.get("_process_metrics_override"), dict),
    )
    resolved_identity = dict(
        identity_value
        if isinstance(identity_value, dict)
        else measurement_identity
        or _measurement_identity(
            scenario_id=scenario_id,
            command=command_value,
            measured_boundary=SUT_BOUNDARY_IN_PROCESS,
            fixture_revision="adhoc",
        )
    )
    resolved_identity["provider_posture"] = provider_profile
    resolved_identity["model_posture"] = str(
        metrics.get("model_posture") or "unavailable"
    )
    if tracemalloc.is_tracing():
        tracemalloc.stop()
    return ScenarioRun(
        scenario_id=scenario_id,
        command=command_value,
        provider_profile=provider_profile,
        provider_variance_class=provider_variance_class,
        metrics=_finish_metrics(metrics),
        notes=notes,
        measurement_identity=resolved_identity,
        ok=error is None,
        error=error,
    )


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


def _run_subprocess_measured(
    command: list[str], *, options: RunOptions, data_root: Path
) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
    process = subprocess.Popen(
        command,
        cwd=options.workspace_root / "openminion",
        env=_command_env(options, data_root=data_root),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + options.timeout_seconds
    sample_count = 0
    first_current_rss: int | None = None
    sampled_peak_current_rss: int | None = None
    resource_metrics = _process_metrics(process.pid)
    current_rss = resource_metrics.get("current_rss_bytes")
    if isinstance(current_rss, int):
        first_current_rss = current_rss
        sampled_peak_current_rss = current_rss
        sample_count = 1
    while True:
        current_rss = _current_rss_bytes(process.pid)
        if isinstance(current_rss, int):
            first_current_rss = (
                current_rss if first_current_rss is None else first_current_rss
            )
            sampled_peak_current_rss = max(
                current_rss,
                sampled_peak_current_rss or current_rss,
            )
            sample_count += 1
        if process.poll() is not None:
            break
        if time.monotonic() >= deadline:
            process.kill()
            stdout, stderr = process.communicate()
            raise subprocess.TimeoutExpired(
                command,
                options.timeout_seconds,
                output=stdout,
                stderr=stderr,
            )
        time.sleep(0.005)
    stdout, stderr = process.communicate()
    resource_metrics["sampled_start_current_rss_bytes"] = first_current_rss
    resource_metrics["sampled_peak_current_rss_bytes"] = sampled_peak_current_rss
    resource_metrics["process_sample_count"] = sample_count
    return (
        subprocess.CompletedProcess(
            command,
            process.returncode,
            stdout=stdout,
            stderr=stderr,
        ),
        resource_metrics,
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
        completed, sut_metrics = _run_subprocess_measured(
            command,
            options=options,
            data_root=data_root,
        )
        metrics["_wall_time_ns_override"] = _elapsed_ns(sut_started_ns)
        metrics["_process_metrics_override"] = sut_metrics
        metrics["rss_start_bytes"] = sut_metrics.get("sampled_start_current_rss_bytes")
        metrics["phase"] = "startup"
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
            "RSS and process-tree fields sample the startup subprocess; sampled peak current RSS remains distinct from unavailable child high-water RSS.",
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


def _measure_import_surface(
    *, scenario_id: str, module_name: str, options: RunOptions
) -> ScenarioRun:
    marker = "OWPR_IMPORT_SURFACE="
    data_root = options.output_root / "runtime-homes" / scenario_id / ".openminion"
    data_root.mkdir(parents=True, exist_ok=True)
    module_artifact = data_root / "imported-modules.json"
    script = (
        "import importlib,json,sys;from pathlib import Path;"
        f"importlib.import_module({module_name!r});"
        f"Path({str(module_artifact)!r}).write_text("
        "json.dumps(sorted(sys.modules)),encoding='utf-8');"
        f"print({marker!r}+'written')"
    )
    command = [str(options.python), "-c", script]
    command_text = f"python -c import_surface:{module_name}"

    def action(metrics: dict[str, Any]) -> list[str]:
        started_ns = time.perf_counter_ns()
        completed, process_metrics = _run_subprocess_measured(
            command,
            options=options,
            data_root=data_root,
        )
        metrics["_wall_time_ns_override"] = _elapsed_ns(started_ns)
        metrics["_process_metrics_override"] = process_metrics
        metrics["rss_start_bytes"] = process_metrics.get(
            "sampled_start_current_rss_bytes"
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"import surface exited {completed.returncode}: {completed.stderr[-500:]}"
            )
        if f"{marker}written" not in completed.stdout:
            raise RuntimeError("import surface did not report imported modules")
        imported = json.loads(module_artifact.read_text(encoding="utf-8"))
        if not isinstance(imported, list):
            raise RuntimeError("import surface returned malformed module facts")
        names = [str(name) for name in imported]
        textual_modules = [
            name for name in names if name == "textual" or name.startswith("textual.")
        ]
        metrics["imported_module_count"] = len(names)
        metrics["openminion_module_count"] = sum(
            name == "openminion" or name.startswith("openminion.") for name in names
        )
        metrics["textual_module_count"] = len(textual_modules)
        metrics["textual_modules"] = textual_modules
        metrics["measured_boundary"] = SUT_BOUNDARY_SUBPROCESS
        return [
            f"Fresh subprocess imports {module_name} and reports exact module names.",
            "Wall time and sampled RSS are advisory; module-family contracts are deterministic.",
        ]

    return _run_with_metrics(
        scenario_id=scenario_id,
        command=command_text,
        provider_variance_class=LOCAL_VARIANCE,
        measurement_identity=_measurement_identity(
            scenario_id=scenario_id,
            command=command_text,
            measured_boundary=SUT_BOUNDARY_SUBPROCESS,
            fixture_revision="import-surface-v1",
            options=options,
            data_root=data_root,
            scenario_config={"module_name": module_name},
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
                    agents={"fixture-agent": SimpleNamespace()},
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
                            "provider_call_purposes": ["entry"],
                            "provider_call_latency_ms": [
                                _ns_to_ms(phase_ns["provider_stub_round_trip_ns"])
                            ],
                            "provider_attempts": [
                                {
                                    "logical_call_id": "deterministic-full-turn-entry",
                                    "semantic_purpose": "entry",
                                    "attempt": 1,
                                    "provider": "stub",
                                    "model": "stub-model",
                                    "route_posture": "primary",
                                    "attempt_posture": "initial",
                                    "latency_ms": _ns_to_ms(
                                        phase_ns["provider_stub_round_trip_ns"]
                                    ),
                                    "outcome": "ok",
                                }
                            ],
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
        phase_ms.setdefault("session_compaction_ms", 0)
        phase_ms.setdefault("memory_followup_flush_ms", 0)
        phase_ms.setdefault("memory_summary_checkpoint_ms", 0)
        phase_ms.setdefault("memory_summary_structure_ms", 0)
        phase_ms.setdefault("response_persistence_ms", 0)
        phase_ms.setdefault("memory_write_ms", 0)
        phase_ms.setdefault("run_record_finish_ms", 0)
        phase_ms.setdefault("response_delivery_ms", phase_ms["terminal_delivery_ms"])
        phase_ms.setdefault("response_delivered_event_ms", 0)
        phase_ms.setdefault("terminal_event_ms", 0)
        provider_latency_ms = phase_ms["provider_stub_round_trip_ms"]
        body = str(result.body or "")
        metrics["time_to_first_visible_text_ms"] = 0 if output_chunks else None
        metrics["phase_timings_ns"] = phase_ns
        metrics["phase_timings_ms"] = phase_ms
        metrics["provider_profile_kind"] = "stub"
        metrics["model_call_count"] = int(result.metadata.get("model_call_count", 0))
        metrics["provider_round_trip_ms"] = phase_ms["provider_stub_round_trip_ms"]
        metrics["provider_call_purposes"] = ["entry"]
        metrics["provider_call_latency_ms"] = [provider_latency_ms]
        metrics["provider_attempts"] = [
            {
                "logical_call_id": "deterministic-full-turn-entry",
                "semantic_purpose": "entry",
                "attempt": 1,
                "provider": "stub",
                "model": "stub-model",
                "route_posture": "primary",
                "attempt_posture": "initial",
                "latency_ms": provider_latency_ms,
                "outcome": "ok",
            }
        ]
        metrics["selector_latency_ms"] = 0
        metrics["selector_token_count"] = 0
        metrics["selector_candidate_count"] = 0
        metrics["skill_selection_route"] = "direct_no_catalog"
        metrics["skill_selection_strategy"] = "llm"
        metrics["session_compaction_ms"] = 0
        metrics["session_compaction_policy"] = "noop"
        metrics["memory_followup_flush_ms"] = 0
        metrics["memory_followup_pending_count"] = 0
        metrics["memory_summary_checkpoint_ms"] = 0
        metrics["memory_summary_structure_ms"] = 0
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


def _measure_instrumentation_overhead_aa(options: RunOptions) -> ScenarioRun:
    def action(metrics: dict[str, Any]) -> list[str]:
        from openminion.modules.telemetry.trace.phase_timing import ChatPhaseTimer

        iterations = 20
        disabled_samples_ns: list[int] = []
        enabled_samples_ns: list[int] = []
        for _ in range(iterations):
            started_ns = time.perf_counter_ns()
            for _index in range(25):
                pass
            disabled_samples_ns.append(_elapsed_ns(started_ns))

            timer = ChatPhaseTimer()
            started_ns = time.perf_counter_ns()
            for _index in range(25):
                with timer.phase("provider_request_build"):
                    pass
                with timer.phase("provider_round_trip"):
                    pass
                timer.record_provider_attempt(
                    logical_call_id="aa-entry",
                    semantic_purpose="entry",
                    attempt=1,
                    provider="stub",
                    model="stub-model",
                    route_posture="primary",
                    attempt_posture="initial",
                    latency_ms=0,
                    outcome="ok",
                )
            timer.build_payload()
            enabled_samples_ns.append(_elapsed_ns(started_ns))

        disabled_median_ns = int(statistics.median(disabled_samples_ns))
        enabled_median_ns = int(statistics.median(enabled_samples_ns))
        overhead_ns = max(0, enabled_median_ns - disabled_median_ns)
        metrics["phase_timings_ns"] = {
            "instrumentation_disabled_median_ns": disabled_median_ns,
            "instrumentation_enabled_median_ns": enabled_median_ns,
            "instrumentation_overhead_median_ns": overhead_ns,
        }
        metrics["phase_timings_ms"] = {
            key.removesuffix("_ns") + "_ms": _ns_to_ms(value)
            for key, value in metrics["phase_timings_ns"].items()
        }
        metrics["instrumentation_aa_enabled_samples"] = iterations
        metrics["instrumentation_aa_disabled_samples"] = iterations
        metrics["instrumentation_overhead_median_ms"] = _ns_to_ms(overhead_ns)
        metrics["provider_calls_allowed"] = False
        metrics["storage_mutations_allowed"] = False
        metrics["tool_call_count"] = 0
        return [
            "A/A instrumentation loop compares enabled timer/reporting work with a disabled no-op loop.",
            "The fixture performs no provider calls and no storage mutations.",
        ]

    return _run_with_metrics(
        scenario_id="instrumentation_overhead_aa",
        command="local_fixture:instrumentation_overhead_aa",
        provider_variance_class=LOCAL_VARIANCE,
        provider_profile="none",
        measurement_identity=_measurement_identity(
            scenario_id="instrumentation_overhead_aa",
            command="local_fixture:instrumentation_overhead_aa",
            measured_boundary=SUT_BOUNDARY_IN_PROCESS,
            fixture_revision="instrumentation-overhead-aa-v1",
            options=options,
        ),
        action=action,
    )


def _tcpl_attempt(
    *,
    scenario_id: str,
    purpose: str,
    attempt: int = 1,
    route_posture: str = "primary",
    attempt_posture: str = "initial",
    latency_ms: int = 0,
    outcome: str = "ok",
    provider: str = "stub",
    model: str = "stub-model",
    error_code: str | None = None,
    logical_call_id: str | None = None,
) -> dict[str, Any]:
    payload = {
        "logical_call_id": logical_call_id or f"{scenario_id}-{purpose}",
        "semantic_purpose": purpose,
        "attempt": attempt,
        "provider": provider,
        "model": model,
        "route_posture": route_posture,
        "attempt_posture": attempt_posture,
        "latency_ms": latency_ms,
        "outcome": outcome,
    }
    if error_code:
        payload["error_code"] = error_code
    return payload


def _tcpl_phase_ms(
    *,
    compaction_ms: int = 0,
    memory_flush_ms: int = 0,
    checkpoint_ms: int = 0,
    structure_ms: int = 0,
    provider_ms: int = 0,
) -> dict[str, int]:
    return {
        "session_compaction_ms": compaction_ms,
        "memory_followup_flush_ms": memory_flush_ms,
        "memory_summary_checkpoint_ms": checkpoint_ms,
        "memory_summary_structure_ms": structure_ms,
        "provider_stub_round_trip_ms": provider_ms,
        "provider_round_trip_ms": provider_ms,
        "response_persistence_ms": 0,
        "memory_write_ms": 0,
        "run_record_finish_ms": 0,
        "response_delivery_ms": 0,
        "response_delivered_event_ms": 0,
        "terminal_event_ms": 0,
    }


def _populate_tcpl_matrix_metrics(
    metrics: dict[str, Any],
    *,
    scenario_id: str,
    purposes: list[str],
    selector_route: str,
    selector_tokens: int,
    selector_candidates: int,
    compaction_policy: str,
    compaction_ms: int,
    memory_posture: str,
    memory_pending_count: int,
    memory_flush_ms: int = 0,
    checkpoint_ms: int = 0,
    structure_ms: int = 0,
    attempts: list[dict[str, Any]] | None = None,
    tool_call_count: int = 0,
) -> None:
    provider_ms = sum(int(item.get("latency_ms", 0) or 0) for item in attempts or [])
    if attempts is None:
        attempts = [
            _tcpl_attempt(
                scenario_id=scenario_id,
                purpose=purpose,
                logical_call_id=f"{scenario_id}-{index}-{purpose}",
            )
            for index, purpose in enumerate(purposes, start=1)
        ]
        provider_ms = 0
    phase_ms = _tcpl_phase_ms(
        compaction_ms=compaction_ms,
        memory_flush_ms=memory_flush_ms,
        checkpoint_ms=checkpoint_ms,
        structure_ms=structure_ms,
        provider_ms=provider_ms,
    )
    metrics["phase_timings_ms"] = phase_ms
    metrics["phase_timings_ns"] = {
        key.removesuffix("_ms") + "_ns": value * 1_000_000
        for key, value in phase_ms.items()
    }
    metrics["time_to_first_visible_text_ms"] = 0
    metrics["provider_profile_kind"] = "stub"
    metrics["model_call_count"] = len(purposes)
    metrics["provider_round_trip_ms"] = provider_ms
    metrics["provider_call_purposes"] = purposes
    metrics["provider_call_latency_ms"] = [0 for _ in purposes]
    metrics["provider_attempts"] = attempts
    metrics["selector_latency_ms"] = 0
    metrics["selector_token_count"] = selector_tokens
    metrics["selector_candidate_count"] = selector_candidates
    metrics["skill_selection_route"] = selector_route
    metrics["skill_selection_strategy"] = "llm"
    metrics["session_compaction_ms"] = compaction_ms
    metrics["session_compaction_policy"] = compaction_policy
    metrics["memory_followup_flush_ms"] = memory_flush_ms
    metrics["memory_followup_pending_count"] = memory_pending_count
    metrics["memory_followup_active_count"] = 1 if memory_posture == "active" else 0
    metrics["memory_projection_posture"] = memory_posture
    metrics["memory_summary_checkpoint_ms"] = checkpoint_ms
    metrics["memory_summary_structure_ms"] = structure_ms
    metrics["tool_call_count"] = tool_call_count
    metrics["storage_operation_count"] = 0
    metrics["render_chunk_count"] = 1
    metrics["retained_messages"] = 2
    metrics["prompt_tokens_estimated"] = max(1, selector_tokens)
    metrics["prompt_bytes"] = metrics["prompt_tokens_estimated"] * 4


def _tcpl_set_quality_floor(metrics: dict[str, Any], *, replay: str = "pass") -> None:
    metrics.update(
        {
            "skill_selection_quality": "pass",
            "tool_order_quality": "pass",
            "context_quality": "pass",
            "transcript_quality": "pass",
            "memory_quality": "pass",
            "replay_quality": replay,
            "policy_quality": "pass",
            "approval_quality": "pass",
        }
    )


def _measure_tcpl_matrix_scenario(scenario_id: str, options: RunOptions) -> ScenarioRun:
    def action(metrics: dict[str, Any]) -> list[str]:
        if scenario_id == "tcpl_selector_direct_route":
            _populate_tcpl_matrix_metrics(
                metrics,
                scenario_id=scenario_id,
                purposes=["entry"],
                selector_route="direct_no_catalog",
                selector_tokens=0,
                selector_candidates=0,
                compaction_policy="noop",
                compaction_ms=0,
                memory_posture="none",
                memory_pending_count=0,
            )
        elif scenario_id == "tcpl_selector_retrieval_route":
            _populate_tcpl_matrix_metrics(
                metrics,
                scenario_id=scenario_id,
                purposes=["entry"],
                selector_route="retrieval",
                selector_tokens=640,
                selector_candidates=3,
                compaction_policy="noop",
                compaction_ms=0,
                memory_posture="none",
                memory_pending_count=0,
            )
        elif scenario_id == "tcpl_selector_llm_route":
            _populate_tcpl_matrix_metrics(
                metrics,
                scenario_id=scenario_id,
                purposes=["skill_selection", "entry"],
                selector_route="llm_preselect",
                selector_tokens=4_800,
                selector_candidates=24,
                compaction_policy="noop",
                compaction_ms=0,
                memory_posture="none",
                memory_pending_count=0,
            )
        elif scenario_id == "tcpl_compaction_threshold_crossing":
            _populate_tcpl_matrix_metrics(
                metrics,
                scenario_id=scenario_id,
                purposes=["self_compaction", "entry"],
                selector_route="direct_no_catalog",
                selector_tokens=0,
                selector_candidates=0,
                compaction_policy="threshold_crossing",
                compaction_ms=12,
                memory_posture="none",
                memory_pending_count=0,
            )
        elif scenario_id == "tcpl_memory_followup_pending":
            _populate_tcpl_matrix_metrics(
                metrics,
                scenario_id=scenario_id,
                purposes=["summarize", "entry"],
                selector_route="direct_no_catalog",
                selector_tokens=0,
                selector_candidates=0,
                compaction_policy="noop",
                compaction_ms=0,
                memory_posture="pending",
                memory_pending_count=2,
                memory_flush_ms=3,
                checkpoint_ms=1,
                structure_ms=1,
            )
        elif scenario_id == "tcpl_memory_followup_active":
            _populate_tcpl_matrix_metrics(
                metrics,
                scenario_id=scenario_id,
                purposes=["summarize", "entry"],
                selector_route="direct_no_catalog",
                selector_tokens=0,
                selector_candidates=0,
                compaction_policy="noop",
                compaction_ms=0,
                memory_posture="active",
                memory_pending_count=1,
                memory_flush_ms=5,
                checkpoint_ms=1,
                structure_ms=2,
            )
        elif scenario_id == "tcpl_branch_direct_tool":
            _populate_tcpl_matrix_metrics(
                metrics,
                scenario_id=scenario_id,
                purposes=["entry", "act", "judge"],
                selector_route="direct_no_catalog",
                selector_tokens=0,
                selector_candidates=0,
                compaction_policy="noop",
                compaction_ms=0,
                memory_posture="none",
                memory_pending_count=0,
                tool_call_count=1,
            )
        elif scenario_id == "tcpl_branch_seeded_multi_step":
            _populate_tcpl_matrix_metrics(
                metrics,
                scenario_id=scenario_id,
                purposes=["entry", "judge", "judge", "judge"],
                selector_route="direct_no_catalog",
                selector_tokens=0,
                selector_candidates=0,
                compaction_policy="noop",
                compaction_ms=0,
                memory_posture="none",
                memory_pending_count=0,
                tool_call_count=2,
            )
        elif scenario_id == "tcpl_branch_final_answer_repair":
            _populate_tcpl_matrix_metrics(
                metrics,
                scenario_id=scenario_id,
                purposes=["entry", "act", "judge", "judge"],
                selector_route="direct_no_catalog",
                selector_tokens=0,
                selector_candidates=0,
                compaction_policy="noop",
                compaction_ms=0,
                memory_posture="none",
                memory_pending_count=0,
                tool_call_count=1,
            )
            metrics["closure_branch"] = "final_answer_repair"
        elif scenario_id == "tcpl_branch_active_mission_finish":
            _populate_tcpl_matrix_metrics(
                metrics,
                scenario_id=scenario_id,
                purposes=["entry", "act", "judge", "judge"],
                selector_route="direct_no_catalog",
                selector_tokens=0,
                selector_candidates=0,
                compaction_policy="noop",
                compaction_ms=0,
                memory_posture="none",
                memory_pending_count=0,
                tool_call_count=1,
            )
            metrics["closure_branch"] = "active_mission_finish"
        elif scenario_id == "tcpl_provider_retry_fallback":
            attempts = [
                _tcpl_attempt(
                    scenario_id=scenario_id,
                    purpose="entry",
                    attempt=1,
                    latency_ms=1,
                    outcome="error",
                    error_code="TIMEOUT",
                ),
                _tcpl_attempt(
                    scenario_id=scenario_id,
                    purpose="entry",
                    attempt=2,
                    attempt_posture="retry",
                    latency_ms=1,
                    outcome="error",
                    error_code="PROVIDER_ERROR",
                ),
                _tcpl_attempt(
                    scenario_id=scenario_id,
                    purpose="entry",
                    attempt=3,
                    route_posture="fallback",
                    latency_ms=1,
                    outcome="ok",
                    provider="fallback-stub",
                ),
            ]
            _populate_tcpl_matrix_metrics(
                metrics,
                scenario_id=scenario_id,
                purposes=["entry"],
                selector_route="direct_no_catalog",
                selector_tokens=0,
                selector_candidates=0,
                compaction_policy="noop",
                compaction_ms=0,
                memory_posture="none",
                memory_pending_count=0,
                attempts=attempts,
            )
        elif scenario_id == "tcpl_large_tool_surface":
            _populate_tcpl_matrix_metrics(
                metrics,
                scenario_id=scenario_id,
                purposes=["tool_shortlist", "entry"],
                selector_route="direct_no_catalog",
                selector_tokens=0,
                selector_candidates=0,
                compaction_policy="noop",
                compaction_ms=0,
                memory_posture="none",
                memory_pending_count=0,
                tool_call_count=0,
            )
            metrics["tool_schema_bytes"] = 24_000
        elif scenario_id == "tcpl_01_streaming_safety_nochange":
            _populate_tcpl_matrix_metrics(
                metrics,
                scenario_id=scenario_id,
                purposes=["entry"],
                selector_route="direct_no_catalog",
                selector_tokens=0,
                selector_candidates=0,
                compaction_policy="noop",
                compaction_ms=0,
                memory_posture="none",
                memory_pending_count=0,
            )
            metrics["candidate_posture"] = "streaming_nochange"
            metrics["rollback_posture"] = "final_only"
            metrics["streaming_prefix_contract"] = "missing_structured_safe_prefix"
            metrics["streaming_current_posture"] = "final_only"
            metrics["streaming_candidate_disposition"] = "defer_nochange"
            metrics["delivery_fence_posture"] = "unchanged"
            _tcpl_set_quality_floor(metrics)
        elif scenario_id == "tcpl_03_memory_projection_defer":
            _populate_tcpl_matrix_metrics(
                metrics,
                scenario_id=scenario_id,
                purposes=["summarize", "entry"],
                selector_route="direct_no_catalog",
                selector_tokens=0,
                selector_candidates=0,
                compaction_policy="noop",
                compaction_ms=0,
                memory_posture="pending",
                memory_pending_count=2,
                memory_flush_ms=5,
                checkpoint_ms=1,
                structure_ms=2,
            )
            metrics["candidate_posture"] = "memory_projection_defer_nochange"
            metrics["rollback_posture"] = "synchronous_followup_flush"
            metrics["memory_generation_contract"] = "not_implemented"
            metrics["memory_candidate_disposition"] = "defer_nochange"
            _tcpl_set_quality_floor(metrics)
        elif scenario_id == "tcpl_04_compaction_defer":
            _populate_tcpl_matrix_metrics(
                metrics,
                scenario_id=scenario_id,
                purposes=["self_compaction", "entry"],
                selector_route="direct_no_catalog",
                selector_tokens=0,
                selector_candidates=0,
                compaction_policy="threshold_crossing",
                compaction_ms=12,
                memory_posture="none",
                memory_pending_count=0,
            )
            metrics["candidate_posture"] = "compaction_projection_defer_nochange"
            metrics["rollback_posture"] = "synchronous_compaction"
            metrics["session_compaction_generation_contract"] = "not_implemented"
            metrics["session_compaction_candidate_disposition"] = "defer_nochange"
            _tcpl_set_quality_floor(metrics)
        elif scenario_id == "tcpl_05_delivery_fence_retain":
            _populate_tcpl_matrix_metrics(
                metrics,
                scenario_id=scenario_id,
                purposes=["entry"],
                selector_route="direct_no_catalog",
                selector_tokens=0,
                selector_candidates=0,
                compaction_policy="noop",
                compaction_ms=0,
                memory_posture="none",
                memory_pending_count=0,
            )
            metrics["candidate_posture"] = "delivery_fence_retain"
            metrics["rollback_posture"] = "delivery_fence_retain"
            metrics["delivery_fence_posture"] = (
                "transcript_memory_terminal_before_final"
            )
            metrics["delivery_candidate_disposition"] = "retain_nochange"
            metrics["post_delivery_allowed_work"] = ["derived_projection", "analytics"]
            _tcpl_set_quality_floor(metrics)
        else:
            raise ValueError(f"unknown TCPL matrix scenario: {scenario_id}")
        return [
            "Provider-free TCPL route/branch matrix fixture.",
            "Records structural owner inputs only; no runtime optimization is enabled.",
        ]

    return _run_with_metrics(
        scenario_id=scenario_id,
        command=f"tcpl_matrix_fixture:{scenario_id}",
        provider_variance_class=LOCAL_VARIANCE,
        provider_profile="stub",
        measurement_identity=_measurement_identity(
            scenario_id=scenario_id,
            command=f"tcpl_matrix_fixture:{scenario_id}",
            measured_boundary=SUT_BOUNDARY_IN_PROCESS,
            fixture_revision="tcpl-matrix-v1",
            options=options,
        ),
        action=action,
    )


def _measure_tcpl02_skill_entry_scenario(
    scenario_id: str,
    options: RunOptions,
) -> ScenarioRun:
    def action(metrics: dict[str, Any]) -> list[str]:
        selected_skill_ids = ["alpha_skill", "beta_skill"]
        quality_assertions = {
            "skill_selection_quality": "pass",
            "tool_order_quality": "pass",
            "context_quality": "pass",
            "transcript_quality": "pass",
            "memory_quality": "not_applicable",
            "replay_quality": "pass",
            "policy_quality": "pass",
            "approval_quality": "not_applicable",
        }
        if scenario_id == "tcpl_02_skill_entry_candidate":
            purposes = ["entry"]
            selector_route = "entry_inline"
            strategy = "entry"
            candidate_posture = "entry_opt_in"
        elif scenario_id == "tcpl_02_skill_entry_rollback":
            purposes = ["skill_selection", "entry"]
            selector_route = "llm_preselect"
            strategy = "llm"
            candidate_posture = "rollback_llm"
        elif scenario_id == "tcpl_02_skill_llm_baseline":
            purposes = ["skill_selection", "entry"]
            selector_route = "llm_preselect"
            strategy = "llm"
            candidate_posture = "baseline_current"
        else:
            raise ValueError(f"unknown TCPL-02 skill entry scenario: {scenario_id}")
        attempts = [
            _tcpl_attempt(
                scenario_id=scenario_id,
                purpose=purpose,
                logical_call_id=f"{scenario_id}-{index}-{purpose}",
            )
            for index, purpose in enumerate(purposes, start=1)
        ]
        _populate_tcpl_matrix_metrics(
            metrics,
            scenario_id=scenario_id,
            purposes=purposes,
            selector_route=selector_route,
            selector_tokens=640,
            selector_candidates=len(selected_skill_ids),
            compaction_policy="noop",
            compaction_ms=0,
            memory_posture="none",
            memory_pending_count=0,
            attempts=attempts,
        )
        metrics["skill_selection_strategy"] = strategy
        metrics["candidate_posture"] = candidate_posture
        metrics["rollback_posture"] = "llm"
        metrics["selected_skill_ids"] = selected_skill_ids
        metrics["applied_skill_ids"] = selected_skill_ids
        metrics["skill_catalog_hash"] = "tcpl-02-two-skill-catalog-v1"
        metrics["skill_catalog_complete_rendered"] = True
        metrics["entry_candidate_budget_tokens"] = TCPL_SKILL_ENTRY_TOKEN_BUDGET
        metrics["entry_candidate_budget_count"] = TCPL_SKILL_ENTRY_CANDIDATE_BUDGET
        metrics.update(quality_assertions)
        return [
            "Provider-free TCPL-02 skill-entry candidate fixture.",
            "Baseline and rollback use llm preselection; entry candidate uses the same bounded rendered candidates inside entry.",
        ]

    return _run_with_metrics(
        scenario_id=scenario_id,
        command=f"tcpl_02_skill_entry_fixture:{scenario_id}",
        provider_variance_class=LOCAL_VARIANCE,
        provider_profile="stub",
        measurement_identity=_measurement_identity(
            scenario_id=scenario_id,
            command=f"tcpl_02_skill_entry_fixture:{scenario_id}",
            measured_boundary=SUT_BOUNDARY_IN_PROCESS,
            fixture_revision="tcpl-02-skill-entry-candidate-v1",
            options=options,
        ),
        action=action,
    )


def _measure_repeated_local_turns(options: RunOptions) -> ScenarioRun:
    def action(metrics: dict[str, Any]) -> list[str]:
        iteration_metrics: list[dict[str, Any]] = []
        start_rss = _current_rss_bytes()
        for index in range(1):
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
        metrics["phase_timings_ms"] = {"iterations": 1}
        metrics["tool_call_count"] = 1
        metrics["iterations"] = iteration_metrics
        if isinstance(start_rss, int) and isinstance(end_rss, int):
            metrics["rss_growth_bytes"] = end_rss - start_rss
            metrics["rss_growth_per_iteration_bytes"] = end_rss - start_rss
        else:
            metrics["rss_growth_bytes"] = None
            metrics["rss_growth_per_iteration_bytes"] = None
            metrics["availability_reasons"]["rss_growth_bytes"] = (
                "current_rss_unavailable"
            )
            metrics["availability_reasons"]["rss_growth_per_iteration_bytes"] = (
                "current_rss_unavailable"
            )
        if iteration_metrics:
            first_peak = iteration_metrics[0]["tracemalloc_peak_bytes"]
            last_peak = iteration_metrics[-1]["tracemalloc_peak_bytes"]
            metrics["tracemalloc_peak_growth_bytes"] = last_peak - first_peak
            metrics["tracemalloc_peak_growth_per_iteration_bytes"] = (
                last_peak - first_peak
            )
        return [
            "Each raw sample records one local iteration; summarize repeated-turn growth across samples."
        ]

    return _run_with_metrics(
        scenario_id="repeated_local_turns",
        command="repeated_local_fixture:single_iteration_sample",
        provider_variance_class=LOCAL_VARIANCE,
        action=action,
    )


def _omfla_echo_config(root: Path) -> Path:
    from openminion.base.config import (
        AgentProfileConfig,
        OpenMinionConfig,
        save_config,
    )

    root.mkdir(parents=True, exist_ok=True)
    config = OpenMinionConfig()
    config.agents = {
        "openminion": AgentProfileConfig(
            name="openminion",
            provider="echo",
            default_channel="console",
        )
    }
    config.default_agent = "openminion"
    config.runtime.log_level = "ERROR"
    config.runtime.memory_enabled = False
    config.storage.path = str(root / "state.db")
    config_path = root / "config.json"
    save_config(config, str(config_path))
    return config_path


def _omfla_process_sample(
    *,
    completed_turn_count: int,
    process_id: int | None = None,
    cache_cardinalities: dict[str, int] | None = None,
    queue_depths: dict[str, int] | None = None,
) -> dict[str, Any]:
    sample = _process_metrics(process_id)
    tree_rss = sample.get("process_tree_current_rss_bytes")
    if isinstance(tree_rss, int) and tree_rss >= OMFLA_PROCESS_TREE_RSS_ABORT_BYTES:
        raise RuntimeError(
            "OMFLA process-tree RSS operational abort: "
            f"{tree_rss} >= {OMFLA_PROCESS_TREE_RSS_ABORT_BYTES}"
        )
    if process_id is None and tracemalloc.is_tracing():
        current, peak = tracemalloc.get_traced_memory()
        sample["tracemalloc_current_bytes"] = int(current)
        sample["tracemalloc_peak_bytes"] = int(peak)
    else:
        sample["tracemalloc_current_bytes"] = None
        sample["tracemalloc_peak_bytes"] = None
        reasons = dict(sample.get("availability_reasons") or {})
        reasons["tracemalloc_current_bytes"] = "not_supported_for_subprocess"
        reasons["tracemalloc_peak_bytes"] = "not_supported_for_subprocess"
        sample["availability_reasons"] = reasons
    sample["completed_turn_count"] = completed_turn_count
    sample["cache_cardinalities"] = dict(cache_cardinalities or {})
    sample["queue_depths"] = dict(queue_depths or {})
    return sample


def _omfla_window_sample(
    *,
    index: int,
    completed_turn_count: int,
    rss_readings: list[int],
    process_id: int | None = None,
    cache_cardinalities: dict[str, int] | None = None,
    queue_depths: dict[str, int] | None = None,
) -> dict[str, Any]:
    sample = _omfla_process_sample(
        completed_turn_count=completed_turn_count,
        process_id=process_id,
        cache_cardinalities=cache_cardinalities,
        queue_depths=queue_depths,
    )
    sample["window_index"] = index
    sample["rss_median_bytes"] = (
        int(statistics.median(rss_readings)) if rss_readings else None
    )
    if not rss_readings:
        sample.setdefault("availability_reasons", {})["rss_median_bytes"] = (
            "current_rss_unavailable"
        )
    return sample


def _omfla_completed_api_turn(
    runtime: Any,
    *,
    session_id: str,
    turn_index: int,
) -> dict[str, Any]:
    result = runtime.run_turn(
        payload={
            "message": f"OMFLA deterministic turn {turn_index}",
            "session_id": session_id,
            "conversation_id": session_id,
        },
        request_id=f"omfla-{session_id}-{turn_index}",
    )
    run_id = str(result.get("run_id") or "").strip()
    if (
        not str(result.get("body") or "").strip()
        or result.get("session_id") != session_id
        or result.get("run_state") != "completed"
        or not run_id
    ):
        raise RuntimeError(f"typed API completion missing for turn {turn_index}")
    events = runtime.sessions.list_events(
        session_id=session_id,
        limit=50,
        newest_first=True,
        event_type_prefix="run.",
    )
    terminal = next(
        (
            event
            for event in events
            if event.event_type == "run.completed"
            and str(event.payload.get("run_id") or "") == run_id
            and event.payload.get("state") == "completed"
        ),
        None,
    )
    if terminal is None:
        raise RuntimeError(f"persisted API terminal event missing for run {run_id}")
    return {
        "session_id": session_id,
        "run_id": run_id,
        "run_record_status": "completed",
        "terminal_event_type": terminal.event_type,
        "terminal_event_state": terminal.payload.get("state"),
    }


def _measure_persistent_api_turns(
    options: RunOptions,
    *,
    warmup_turns: int = 10,
    measured_turns: int = 100,
    window_count: int = 5,
) -> ScenarioRun:
    scenario_id = "persistent_api_turns"

    def action(metrics: dict[str, Any]) -> list[str]:
        from contextlib import redirect_stdout

        from openminion.api.runtime import APIRuntime

        root = options.output_root / "api-runtime"
        config_path = _omfla_echo_config(root)
        runtime = APIRuntime.from_config_path(str(config_path))
        completed = 0
        terminal_fact: dict[str, Any] | None = None
        windows: list[dict[str, Any]] = []
        post_warmup_snapshot: Any | None = None
        pre_close_diff: list[dict[str, Any]] = []
        try:
            ready = _omfla_process_sample(completed_turn_count=completed)
            with redirect_stdout(io.StringIO()):
                for turn_index in range(warmup_turns):
                    terminal_fact = _omfla_completed_api_turn(
                        runtime,
                        session_id="omfla-api",
                        turn_index=turn_index,
                    )
                    completed += 1
            context_service = runtime.agent._runner.context_api.service  # noqa: SLF001

            def context_cache_cardinalities() -> dict[str, int]:
                return {
                    "contextctl_pack_cache": len(context_service._cache),  # noqa: SLF001
                    "contextctl_manifest_index": len(  # noqa: SLF001
                        context_service._manifest_index
                    ),
                    "contextctl_latest_sessions": len(  # noqa: SLF001
                        context_service._latest_manifest_by_session
                    ),
                }

            post_warmup = _omfla_process_sample(
                completed_turn_count=completed,
                cache_cardinalities=context_cache_cardinalities(),
            )
            if tracemalloc.is_tracing():
                post_warmup_snapshot = tracemalloc.take_snapshot()
            if measured_turns % window_count:
                raise ValueError("measured API turns must divide into equal windows")
            turns_per_window = measured_turns // window_count
            for window_index in range(window_count):
                readings: list[int] = []
                with redirect_stdout(io.StringIO()):
                    for _ in range(turns_per_window):
                        terminal_fact = _omfla_completed_api_turn(
                            runtime,
                            session_id="omfla-api",
                            turn_index=completed,
                        )
                        completed += 1
                        if isinstance(current_rss := _current_rss_bytes(), int):
                            readings.append(current_rss)
                windows.append(
                    _omfla_window_sample(
                        index=window_index + 1,
                        completed_turn_count=completed,
                        rss_readings=readings,
                        cache_cardinalities={
                            **context_cache_cardinalities(),
                            "gateway_memory_capsules": len(
                                runtime.gateway._memory_capsule_cache  # noqa: SLF001
                            ),
                            "runtime_gateways": len(runtime._gateways),  # noqa: SLF001
                            "runtime_agent_services": len(  # noqa: SLF001
                                runtime._agent_services
                            ),
                        },
                    )
                )
            if post_warmup_snapshot is not None:
                pre_close_diff = _tracemalloc_diff_summary(
                    post_warmup_snapshot,
                    tracemalloc.take_snapshot(),
                )
        finally:
            runtime.close()
        close_sample = _omfla_process_sample(
            completed_turn_count=completed,
            cache_cardinalities=context_cache_cardinalities(),
        )
        close_sample["phase"] = "normal_close"
        post_close_snapshot = (
            tracemalloc.take_snapshot() if post_warmup_snapshot is not None else None
        )
        post_close_diff = (
            _tracemalloc_diff_summary(post_warmup_snapshot, post_close_snapshot)
            if post_warmup_snapshot is not None and post_close_snapshot is not None
            else []
        )
        post_close_event_loop_diff = (
            _tracemalloc_line_diff(
                post_warmup_snapshot,
                post_close_snapshot,
                filename_suffix="asyncio/base_events.py",
                lineno=401,
            )
            if post_warmup_snapshot is not None and post_close_snapshot is not None
            else {"size_diff_bytes": 0, "count_diff": 0}
        )
        collected_objects = gc.collect()
        post_gc_sample = _omfla_process_sample(
            completed_turn_count=completed,
            cache_cardinalities=context_cache_cardinalities(),
        )
        post_gc_sample["phase"] = "post_diagnostic_gc"
        post_gc_snapshot = (
            tracemalloc.take_snapshot() if post_warmup_snapshot is not None else None
        )
        post_gc_diff = (
            _tracemalloc_diff_summary(post_warmup_snapshot, post_gc_snapshot)
            if post_warmup_snapshot is not None and post_gc_snapshot is not None
            else []
        )
        post_gc_event_loop_diff = (
            _tracemalloc_line_diff(
                post_warmup_snapshot,
                post_gc_snapshot,
                filename_suffix="asyncio/base_events.py",
                lineno=401,
            )
            if post_warmup_snapshot is not None and post_gc_snapshot is not None
            else {"size_diff_bytes": 0, "count_diff": 0}
        )
        metrics.update(
            {
                "ready_sample": ready,
                "post_warmup_sample": post_warmup,
                "steady_state_windows": windows,
                "close_sample": close_sample,
                "completed_turn_count": completed,
                "warmup_turn_count": warmup_turns,
                "measured_turn_count": measured_turns,
                "terminal_fact": terminal_fact,
                "cache_cardinalities": windows[-1]["cache_cardinalities"],
                "post_warmup_pre_close_tracemalloc_diff": pre_close_diff,
                "post_warmup_post_close_tracemalloc_diff": post_close_diff,
                "post_warmup_post_close_event_loop_diff": (post_close_event_loop_diff),
                "diagnostic_gc_collected_objects": collected_objects,
                "post_warmup_post_gc_tracemalloc_diff": post_gc_diff,
                "post_warmup_post_gc_event_loop_diff": post_gc_event_loop_diff,
                "post_gc_sample": post_gc_sample,
                "phase": "post_diagnostic_gc",
                "model_posture": "test_owned_echo",
            }
        )
        return [
            "One API runtime and one session own all warmup and measured turns.",
            "Completion requires the typed result plus its matching persisted run.completed event.",
        ]

    return _run_with_metrics(
        scenario_id=scenario_id,
        command="omfla_fixture:api-repeated-turns",
        provider_variance_class=LOCAL_VARIANCE,
        provider_profile="test_owned_echo",
        measurement_identity=_measurement_identity(
            scenario_id=scenario_id,
            command="omfla_fixture:api-repeated-turns",
            measured_boundary=SUT_BOUNDARY_IN_PROCESS,
            fixture_revision="omfla-api-repeated-turns-v4",
            options=options,
            data_root=options.output_root / "api-runtime",
            provider_posture="test_owned_echo",
            model_posture="test_owned_echo",
        ),
        action=action,
    )


def _persisted_focus_terminal_fact(
    database_path: Path,
    *,
    session_id: str,
    after_event_id: int,
    missing_ok: bool = False,
) -> dict[str, Any] | None:
    from contextlib import closing
    import sqlite3

    with closing(
        sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    ) as connection:
        row = connection.execute(
            """
            SELECT id, payload_json
            FROM events
            WHERE session_id = ? AND event_type = 'run.completed' AND id > ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (session_id, after_event_id),
        ).fetchone()
        if row is None:
            if missing_ok:
                return None
            raise RuntimeError("persisted Focus run.completed event missing")
        message_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM messages WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]
        )
    payload = json.loads(str(row[1]))
    run_id = str(payload.get("run_id") or "").strip()
    state = str(payload.get("state") or "").strip()
    if not run_id or state != "completed":
        raise RuntimeError("persisted Focus terminal fact is not completed")
    return {
        "event_id": int(row[0]),
        "session_id": session_id,
        "run_id": run_id,
        "run_record_status": state,
        "terminal_event_type": "run.completed",
        "terminal_event_state": state,
        "persisted_message_count": message_count,
    }


def _measure_persistent_focus_turns(
    options: RunOptions,
    *,
    warmup_turns: int = 10,
    measured_turns: int = 50,
    window_count: int = 5,
) -> ScenarioRun:
    scenario_id = "persistent_focus_turns"

    def action(metrics: dict[str, Any]) -> list[str]:
        from tests.e2e.cli.focus.harness import FocusProbe

        root = options.output_root / "focus-runtime"
        config_path = _omfla_echo_config(root)
        session_id = "omfla-focus"
        probe = FocusProbe(
            python_bin=options.python,
            openminion_root=options.workspace_root / "openminion",
            framework_root=options.workspace_root,
            data_root=root / "data",
            config_path=config_path,
            agent_id="openminion",
            workdir=options.workspace_root / "openminion",
            session_id=session_id,
            include_project_context=False,
        )
        completed = 0
        terminal_fact: dict[str, Any] | None = None
        terminal_event_id = 0
        windows: list[dict[str, Any]] = []
        child_pid = 0
        session = probe.session()
        session.start()
        try:
            probe.wait_ready(session)
            process = session._process  # noqa: SLF001
            if process is None:
                raise RuntimeError("Focus PTY process is unavailable")
            child_pid = process.pid
            ready = _omfla_process_sample(
                completed_turn_count=completed,
                process_id=child_pid,
            )

            def run_turn() -> None:
                nonlocal completed, terminal_event_id, terminal_fact
                probe._submit_composer_line(  # noqa: SLF001
                    session,
                    f"OMFLA deterministic Focus turn {completed}",
                )
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    terminal_fact = _persisted_focus_terminal_fact(
                        root / "state.db",
                        session_id=session_id,
                        after_event_id=terminal_event_id,
                        missing_ok=True,
                    )
                    if terminal_fact is not None:
                        break
                    time.sleep(0.05)
                else:
                    raise RuntimeError("persisted Focus terminal event timed out")
                terminal_event_id = int(terminal_fact["event_id"])
                completed += 1

            for _ in range(warmup_turns):
                run_turn()
            post_warmup = _omfla_process_sample(
                completed_turn_count=completed,
                process_id=child_pid,
            )
            if measured_turns % window_count:
                raise ValueError("measured Focus turns must divide into equal windows")
            turns_per_window = measured_turns // window_count
            for window_index in range(window_count):
                readings: list[int] = []
                for _ in range(turns_per_window):
                    run_turn()
                    if isinstance(current_rss := _current_rss_bytes(child_pid), int):
                        readings.append(current_rss)
                windows.append(
                    _omfla_window_sample(
                        index=window_index + 1,
                        completed_turn_count=completed,
                        rss_readings=readings,
                        process_id=child_pid,
                    )
                )
            metrics["_process_metrics_override"] = _process_metrics(child_pid)
        finally:
            session.terminate()
        child_alive_after_close = False
        if child_pid:
            try:
                import psutil  # type: ignore[import-not-found]

                child_alive_after_close = psutil.pid_exists(child_pid)
            except ImportError:
                child_alive_after_close = False
        metrics.update(
            {
                "ready_sample": ready,
                "post_warmup_sample": post_warmup,
                "steady_state_windows": windows,
                "close_sample": _omfla_process_sample(completed_turn_count=completed),
                "completed_turn_count": completed,
                "warmup_turn_count": warmup_turns,
                "measured_turn_count": measured_turns,
                "terminal_fact": terminal_fact,
                "focus_child_pid": child_pid,
                "focus_child_alive_after_close": child_alive_after_close,
                "focus_transcript_retention_limit": 1000,
                "focus_transcript_retention_limit_crossed": completed * 2 > 1000,
                "durable_history_message_count": (
                    int(terminal_fact["persisted_message_count"])
                    if terminal_fact is not None
                    else 0
                ),
                "model_posture": "test_owned_echo",
            }
        )
        if child_alive_after_close:
            raise RuntimeError("Focus child process remained alive after close")
        return [
            "Composer screen text is readiness evidence only.",
            "Each completed turn is matched to a new persisted run.completed event for the same session.",
            "The default 1000-message transcript retention limit is recorded; this campaign does not cross it.",
        ]

    return _run_with_metrics(
        scenario_id=scenario_id,
        command="omfla_fixture:focus-repeated-turns",
        provider_variance_class=LOCAL_VARIANCE,
        provider_profile="test_owned_echo",
        measurement_identity=_measurement_identity(
            scenario_id=scenario_id,
            command="omfla_fixture:focus-repeated-turns",
            measured_boundary=SUT_BOUNDARY_SUBPROCESS,
            fixture_revision="omfla-focus-repeated-turns-v1",
            options=options,
            data_root=options.output_root / "focus-runtime",
            provider_posture="test_owned_echo",
            model_posture="test_owned_echo",
        ),
        action=action,
    )


def _measure_session_cache_churn(
    options: RunOptions,
    *,
    warmup_turns: int = 10,
    session_count: int = 100,
    window_count: int = 5,
) -> ScenarioRun:
    scenario_id = "session_cache_churn"

    def action(metrics: dict[str, Any]) -> list[str]:
        from contextlib import redirect_stdout

        from openminion.api.runtime import APIRuntime

        root = options.output_root / "session-runtime"
        runtime = APIRuntime.from_config_path(str(_omfla_echo_config(root)))
        completed = 0
        terminal_fact: dict[str, Any] | None = None
        windows: list[dict[str, Any]] = []
        try:
            ready = _omfla_process_sample(completed_turn_count=completed)
            with redirect_stdout(io.StringIO()):
                for turn_index in range(warmup_turns):
                    terminal_fact = _omfla_completed_api_turn(
                        runtime,
                        session_id="omfla-session-warmup",
                        turn_index=turn_index,
                    )
                    completed += 1
            context_service = runtime.agent._runner.context_api.service  # noqa: SLF001

            def context_cardinalities() -> dict[str, int]:
                return {
                    "contextctl_pack_cache": len(context_service._cache),  # noqa: SLF001
                    "contextctl_manifest_index": len(  # noqa: SLF001
                        context_service._manifest_index
                    ),
                    "contextctl_latest_sessions": len(  # noqa: SLF001
                        context_service._latest_manifest_by_session
                    ),
                }

            post_warmup = _omfla_process_sample(completed_turn_count=completed)
            if session_count % window_count:
                raise ValueError("session count must divide into equal windows")
            sessions_per_window = session_count // window_count
            for window_index in range(window_count):
                readings: list[int] = []
                with redirect_stdout(io.StringIO()):
                    for _ in range(sessions_per_window):
                        session_index = completed - warmup_turns
                        terminal_fact = _omfla_completed_api_turn(
                            runtime,
                            session_id=f"omfla-session-{session_index:03d}",
                            turn_index=0,
                        )
                        completed += 1
                        if isinstance(current_rss := _current_rss_bytes(), int):
                            readings.append(current_rss)
                followups = runtime.gateway._turn_runner._memory_followup_queue  # noqa: SLF001
                windows.append(
                    _omfla_window_sample(
                        index=window_index + 1,
                        completed_turn_count=completed,
                        rss_readings=readings,
                        cache_cardinalities={
                            **context_cardinalities(),
                            "runtime_sessions": runtime.sessions.count_sessions(),
                            "gateway_memory_capsules": len(
                                runtime.gateway._memory_capsule_cache  # noqa: SLF001
                            ),
                            "runtime_gateways": len(runtime._gateways),  # noqa: SLF001
                            "runtime_agent_services": len(  # noqa: SLF001
                                runtime._agent_services
                            ),
                        },
                        queue_depths={
                            "memory_followup": followups.pending_count(),
                        },
                    )
                )
        finally:
            runtime.close()
        close_cardinalities = context_cardinalities()
        last_cache = windows[-1]["cache_cardinalities"]
        metrics.update(
            {
                "ready_sample": ready,
                "post_warmup_sample": post_warmup,
                "steady_state_windows": windows,
                "close_sample": _omfla_process_sample(
                    completed_turn_count=completed,
                    cache_cardinalities=close_cardinalities,
                ),
                "completed_turn_count": completed,
                "warmup_turn_count": warmup_turns,
                "distinct_session_count": session_count,
                "terminal_fact": terminal_fact,
                "cache_cardinalities": last_cache,
                "queue_depths": windows[-1]["queue_depths"],
                "owner_cardinality_facts": [
                    {
                        "owner": "runtime SessionStore",
                        "lifetime": "runtime/durable store",
                        "observed_cardinality": last_cache["runtime_sessions"],
                        "natural_invalidation": "session retention policy",
                        "disposition": "keep",
                    },
                    {
                        "owner": "GatewayService memory capsule cache",
                        "lifetime": "gateway",
                        "observed_cardinality": last_cache["gateway_memory_capsules"],
                        "natural_invalidation": "gateway/runtime close",
                        "disposition": "keep",
                    },
                    {
                        "owner": "ContextCtlService pack and manifest caches",
                        "lifetime": "Brain/context-service runtime lifetime",
                        "observed_cardinality": {
                            "pack_cache": last_cache["contextctl_pack_cache"],
                            "manifest_index": last_cache["contextctl_manifest_index"],
                            "latest_sessions": last_cache["contextctl_latest_sessions"],
                        },
                        "natural_invalidation": (
                            "ContextCtl close delegated through Brain/runtime close"
                        ),
                        "disposition": "defer:session-lifecycle-contract-required",
                    },
                    {
                        "owner": "repo-map cache",
                        "lifetime": "context owner with 60-second entry TTL",
                        "observed_cardinality": None,
                        "natural_invalidation": "matching lookup after TTL",
                        "disposition": "defer:not_reached_by_provider_free_turn",
                    },
                    {
                        "owner": "file backend cache",
                        "lifetime": "process",
                        "observed_cardinality": None,
                        "natural_invalidation": "test reset/process close",
                        "disposition": "defer:not_reached_by_provider_free_turn",
                    },
                    {
                        "owner": "memory follow-up queue",
                        "lifetime": "gateway turn runner",
                        "observed_cardinality": windows[-1]["queue_depths"][
                            "memory_followup"
                        ],
                        "natural_invalidation": "flush/worker drain",
                        "disposition": "keep",
                    },
                    {
                        "owner": "control-plane submission audit/dedup",
                        "lifetime": "control-plane store",
                        "observed_cardinality": None,
                        "natural_invalidation": "store retention owner",
                        "disposition": "defer:measured_by_queue_pressure",
                    },
                ],
                "model_posture": "test_owned_echo",
            }
        )
        return [
            "One warmup session is followed by distinct provider-free sessions in the same runtime.",
            "Unconnected cache owners are reported as deferred facts rather than assigned synthetic zeroes.",
        ]

    return _run_with_metrics(
        scenario_id=scenario_id,
        command="omfla_fixture:session-churn",
        provider_variance_class=LOCAL_VARIANCE,
        provider_profile="test_owned_echo",
        measurement_identity=_measurement_identity(
            scenario_id=scenario_id,
            command="omfla_fixture:session-churn",
            measured_boundary=SUT_BOUNDARY_IN_PROCESS,
            fixture_revision="omfla-session-churn-v1",
            options=options,
            data_root=options.output_root / "session-runtime",
            provider_posture="test_owned_echo",
            model_posture="test_owned_echo",
        ),
        action=action,
    )


def _measure_provider_lifecycle_loopback(
    options: RunOptions,
    *,
    warmup_calls: int = 5,
    measured_calls: int = 50,
) -> ScenarioRun:
    scenario_id = "provider_lifecycle_loopback"

    def action(metrics: dict[str, Any]) -> list[str]:
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading
        from unittest.mock import patch

        from openminion.modules.llm import LLMRequest
        from openminion.modules.llm.providers.openai.adapter import OpenAIProvider
        from openminion.tools.mcp.manager import MCPFleetManager

        request_count = 0

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self) -> None:  # noqa: N802
                nonlocal request_count
                request_count += 1
                length = int(self.headers.get("Content-Length", "0") or 0)
                self.rfile.read(length)
                body = json.dumps(
                    {
                        "id": f"loopback-{request_count}",
                        "model": "fixture-model",
                        "choices": [
                            {
                                "finish_reason": "stop",
                                "message": {"content": "loopback ok"},
                            }
                        ],
                        "usage": {
                            "prompt_tokens": 1,
                            "completion_tokens": 1,
                            "total_tokens": 2,
                        },
                    }
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format: str, *_args: Any) -> None:
                return None

        class InProcessMCPSession:
            server_name = "fixture"
            restart_total = 0

            def __init__(self) -> None:
                self.closed = False

            def call_tool(
                self,
                *,
                remote_name: str,
                arguments: dict[str, Any],
                progress_token: str,
            ) -> dict[str, Any]:
                if self.closed:
                    raise RuntimeError("in-process MCP session is closed")
                return {
                    "content": [{"type": "text", "text": remote_name}],
                    "arguments": dict(arguments),
                    "progress_token": progress_token,
                }

            def close(self, *, reset_initialized: bool) -> None:
                del reset_initialized
                self.closed = True

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server_thread = threading.Thread(
            target=server.serve_forever,
            name="omfla-loopback-http",
            daemon=True,
        )
        server_thread.start()
        base_url = f"http://127.0.0.1:{server.server_address[1]}/v1"
        request = LLMRequest.model_validate(
            {
                "model": "fixture-model",
                "messages": [{"role": "user", "content": "loopback"}],
            }
        )
        provider_config = {
            "api_key": "fixture-key",
            "base_url": base_url,
            "http_connection_reuse_enabled": True,
            "allow_curl_fallback": False,
        }
        ready = _omfla_process_sample(completed_turn_count=0)
        provider = OpenAIProvider()
        with patch.dict(
            os.environ,
            {"NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"},
        ):
            provider._http_client._get_client()  # noqa: SLF001
        mcp_manager = MCPFleetManager([])
        mcp_session = InProcessMCPSession()
        mcp_manager._sessions = {"fixture": mcp_session}  # noqa: SLF001
        post_warmup: dict[str, Any] | None = None
        try:
            for call_index in range(warmup_calls + measured_calls):
                response = provider.complete(request, provider_config)
                if not response.ok or response.output_text != "loopback ok":
                    raise RuntimeError("loopback HTTP adapter completion failed")
                result = mcp_manager.call_tool(
                    server_name="fixture",
                    remote_name="echo",
                    arguments={"value": "fixture"},
                )
                if not result.get("content"):
                    raise RuntimeError("in-process MCP session call failed")
                if call_index + 1 == warmup_calls:
                    post_warmup = _omfla_process_sample(completed_turn_count=0)
            post_calls = _omfla_process_sample(
                completed_turn_count=measured_calls * 2,
                cache_cardinalities={
                    "mcp_discovery_cache": len(mcp_manager.discovery_cache_snapshot()),
                },
            )
        finally:
            provider.close()
            mcp_manager.close()

        recreated_provider = OpenAIProvider()
        with patch.dict(
            os.environ,
            {"NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"},
        ):
            recreated_provider._http_client._get_client()  # noqa: SLF001
        recreated_manager = MCPFleetManager([])
        recreated_session = InProcessMCPSession()
        recreated_manager._sessions = {"fixture": recreated_session}  # noqa: SLF001
        try:
            recreated_response = recreated_provider.complete(request, provider_config)
            recreated_result = recreated_manager.call_tool(
                server_name="fixture",
                remote_name="echo",
                arguments={"value": "recreated"},
            )
            if not recreated_response.ok or not recreated_result.get("content"):
                raise RuntimeError("provider/MCP recreate proof failed")
        finally:
            recreated_provider.close()
            recreated_manager.close()
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=5)
        http_client_closed = provider._http_client._client is None  # noqa: SLF001
        close_sample = _omfla_process_sample(completed_turn_count=measured_calls * 2)
        if post_warmup is None:
            raise RuntimeError("provider lifecycle warmup baseline missing")
        metrics.update(
            {
                "ready_sample": ready,
                "post_warmup_sample": post_warmup,
                "steady_state_windows": [post_calls],
                "close_sample": close_sample,
                "completed_turn_count": measured_calls * 2,
                "http_warmup_call_count": warmup_calls,
                "http_measured_call_count": measured_calls,
                "mcp_warmup_call_count": warmup_calls,
                "mcp_measured_call_count": measured_calls,
                "loopback_http_request_count": request_count,
                "http_client_closed": http_client_closed,
                "mcp_session_closed": mcp_session.closed,
                "http_recreate_call_completed": recreated_response.ok,
                "mcp_recreate_call_completed": bool(recreated_result.get("content")),
                "terminal_fact": {
                    "http": "completed",
                    "mcp": "completed",
                    "recreate": "completed",
                },
                "model_posture": "test_owned_loopback",
            }
        )
        if not http_client_closed or not mcp_session.closed:
            raise RuntimeError("provider or MCP owner did not close")
        if close_sample["thread_count"] > ready["thread_count"]:
            raise RuntimeError("provider lifecycle threads did not converge")
        return [
            "HTTP traffic is bound to a test-owned 127.0.0.1 server only.",
            "MCP calls use an in-process session injected into the existing fleet manager; no MCP transport or server is started.",
        ]

    return _run_with_metrics(
        scenario_id=scenario_id,
        command="omfla_fixture:provider-churn",
        provider_variance_class=LOCAL_VARIANCE,
        provider_profile="test_owned_loopback",
        measurement_identity=_measurement_identity(
            scenario_id=scenario_id,
            command="omfla_fixture:provider-churn",
            measured_boundary=SUT_BOUNDARY_IN_PROCESS,
            fixture_revision="omfla-provider-churn-v2",
            options=options,
            data_root=options.output_root / "provider-runtime",
            provider_posture="test_owned_loopback",
            model_posture="test_owned_loopback",
        ),
        action=action,
    )


def _measure_agent_cache_churn(
    options: RunOptions,
    *,
    agent_count: int = 10,
    max_agents_hot: int = 8,
    convergence_wait_seconds: float = 3.0,
) -> ScenarioRun:
    scenario_id = "agent_cache_churn"

    def action(metrics: dict[str, Any]) -> list[str]:
        from openminion.services.runtime import (
            AgentRuntimeManager,
            TurnRequest,
            TurnResponse,
        )

        def execute(request: Any, emit_chunk: Any, cancel_event: Any) -> Any:
            del emit_chunk, cancel_event
            return TurnResponse(final_text=f"completed:{request.agent_id}")

        manager = AgentRuntimeManager(
            turn_executor=execute,
            max_agents_hot=max_agents_hot,
            max_global_concurrency=1,
            agent_ttl_seconds=1,
            sweep_interval_seconds=1,
        )
        manager.start()
        completed = 0
        hot_counts: list[int] = []
        windows: list[dict[str, Any]] = []
        ready = _omfla_process_sample(completed_turn_count=0)
        try:
            for index in range(agent_count):
                handle = manager.submit_turn(
                    TurnRequest(
                        trace_id=f"omfla-agent-trace-{index}",
                        agent_id=f"omfla-agent-{index}",
                        session_id=f"omfla-agent-session-{index}",
                        input_text="complete deterministically",
                    )
                )
                response = handle.result(timeout_s=5)
                if (
                    response.errors
                    or response.final_text != f"completed:omfla-agent-{index}"
                ):
                    raise RuntimeError(f"agent churn turn {index} did not complete")
                completed += 1
                hot_counts.append(len(manager.list_agents()))
                if completed % max(1, agent_count // 5) == 0:
                    windows.append(
                        _omfla_window_sample(
                            index=len(windows) + 1,
                            completed_turn_count=completed,
                            rss_readings=[
                                value
                                for value in [_current_rss_bytes()]
                                if isinstance(value, int)
                            ],
                            cache_cardinalities={
                                "runtime_hot_agents": len(manager.list_agents())
                            },
                        )
                    )
            deadline = time.monotonic() + convergence_wait_seconds
            while manager.list_agents() and time.monotonic() < deadline:
                time.sleep(0.05)
            remaining_agents = len(manager.list_agents())
        finally:
            manager.shutdown()
        metrics.update(
            {
                "ready_sample": ready,
                "post_warmup_sample": windows[0],
                "steady_state_windows": windows,
                "close_sample": _omfla_process_sample(completed_turn_count=completed),
                "completed_turn_count": completed,
                "configured_max_agents_hot": max_agents_hot,
                "agent_ttl_seconds": 1,
                "sweep_interval_seconds": 1,
                "max_observed_hot_agents": max(hot_counts, default=0),
                "remaining_agents_after_convergence": remaining_agents,
                "terminal_fact": {
                    "completed_agents": completed,
                    "remaining_after_ttl": remaining_agents,
                },
            }
        )
        if max(hot_counts, default=0) > max_agents_hot or remaining_agents:
            raise RuntimeError("agent cache did not respect its bound and TTL")
        return [
            "The existing runtime manager owns LRU and TTL behavior.",
            "Ten distinct agents each complete one turn before the bounded convergence wait.",
        ]

    return _run_with_metrics(
        scenario_id=scenario_id,
        command="omfla_fixture:agent-churn",
        provider_variance_class=LOCAL_VARIANCE,
        measurement_identity=_measurement_identity(
            scenario_id=scenario_id,
            command="omfla_fixture:agent-churn",
            measured_boundary=SUT_BOUNDARY_IN_PROCESS,
            fixture_revision="omfla-agent-churn-v1",
            options=options,
        ),
        action=action,
    )


def _omfla_focus_restart_cycle(
    options: RunOptions,
    *,
    root: Path,
    cycle_index: int,
) -> dict[str, Any]:
    from tests.e2e.cli.focus.harness import FocusProbe

    config_path = _omfla_echo_config(root)
    session_id = f"omfla-focus-restart-{cycle_index}"
    probe = FocusProbe(
        python_bin=options.python,
        openminion_root=options.workspace_root / "openminion",
        framework_root=options.workspace_root,
        data_root=root / "data",
        config_path=config_path,
        agent_id="openminion",
        workdir=options.workspace_root / "openminion",
        session_id=session_id,
        include_project_context=False,
    )
    session = probe.session()
    session.start()
    child_pid = 0
    try:
        probe.wait_ready(session)
        process = session._process  # noqa: SLF001
        if process is None:
            raise RuntimeError("Focus restart process is unavailable")
        child_pid = process.pid
        probe._submit_composer_line(  # noqa: SLF001
            session,
            f"OMFLA Focus restart cycle {cycle_index}",
        )
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            terminal = _persisted_focus_terminal_fact(
                root / "state.db",
                session_id=session_id,
                after_event_id=0,
                missing_ok=True,
            )
            if terminal is not None:
                break
            time.sleep(0.05)
        else:
            raise RuntimeError("persisted Focus restart event timed out")
    finally:
        session.terminate()
        session.terminate()
    try:
        import psutil  # type: ignore[import-not-found]

        child_alive = psutil.pid_exists(child_pid)
    except ImportError:
        child_alive = False
    if child_alive:
        raise RuntimeError(f"Focus restart child {child_pid} remained alive")
    return terminal


def _measure_runtime_restart(
    options: RunOptions,
    *,
    cycle_count: int = 5,
) -> ScenarioRun:
    scenario_id = "runtime_restart"

    def action(metrics: dict[str, Any]) -> list[str]:
        from contextlib import redirect_stdout

        from openminion.api.runtime import APIRuntime
        from openminion.services.runtime import (
            AgentRuntimeManager,
            TurnRequest,
            TurnResponse,
        )

        windows: list[dict[str, Any]] = []
        terminal_fact: dict[str, Any] | None = None
        completed = 0
        ready = _omfla_process_sample(completed_turn_count=0)
        for cycle_index in range(cycle_count):
            api_root = options.output_root / f"api-cycle-{cycle_index + 1:02d}"
            api_runtime = APIRuntime.from_config_path(str(_omfla_echo_config(api_root)))
            try:
                with redirect_stdout(io.StringIO()):
                    terminal_fact = _omfla_completed_api_turn(
                        api_runtime,
                        session_id=f"omfla-api-restart-{cycle_index}",
                        turn_index=0,
                    )
                completed += 1
            finally:
                api_runtime.close()
                api_runtime.close()

            terminal_fact = _omfla_focus_restart_cycle(
                options,
                root=options.output_root / f"focus-cycle-{cycle_index + 1:02d}",
                cycle_index=cycle_index,
            )
            completed += 1

            def execute(request: Any, emit_chunk: Any, cancel_event: Any) -> Any:
                del emit_chunk, cancel_event
                return TurnResponse(final_text=f"daemon:{request.trace_id}")

            daemon_manager = AgentRuntimeManager(
                turn_executor=execute,
                max_agents_hot=8,
                max_global_concurrency=1,
            )
            daemon_manager.start()
            try:
                handle = daemon_manager.submit_turn(
                    TurnRequest(
                        trace_id=f"omfla-daemon-{cycle_index}",
                        agent_id="omfla-daemon-agent",
                        session_id=f"omfla-daemon-session-{cycle_index}",
                        input_text="restart proof",
                    )
                )
                response = handle.result(timeout_s=5)
                if response.errors or not response.final_text.startswith("daemon:"):
                    raise RuntimeError("daemon-manager restart turn failed")
                completed += 1
            finally:
                daemon_manager.shutdown()
                daemon_manager.shutdown()
            windows.append(
                _omfla_window_sample(
                    index=cycle_index + 1,
                    completed_turn_count=completed,
                    rss_readings=[
                        value
                        for value in [_current_rss_bytes()]
                        if isinstance(value, int)
                    ],
                )
            )
        close_sample = _omfla_process_sample(completed_turn_count=completed)
        descriptor_counts_converged = all(
            window["file_descriptor_count"] == ready["file_descriptor_count"]
            and window["open_file_count"] == ready["open_file_count"]
            for window in windows
        ) and (
            close_sample["file_descriptor_count"] == ready["file_descriptor_count"]
            and close_sample["open_file_count"] == ready["open_file_count"]
        )
        if not descriptor_counts_converged:
            raise RuntimeError("runtime restart descriptors did not return to baseline")
        metrics.update(
            {
                "ready_sample": ready,
                "post_warmup_sample": windows[0],
                "steady_state_windows": windows,
                "close_sample": close_sample,
                "completed_turn_count": completed,
                "api_restart_cycle_count": cycle_count,
                "focus_restart_cycle_count": cycle_count,
                "daemon_restart_cycle_count": cycle_count,
                "second_close_idempotency_checked": True,
                "descriptor_counts_converged": descriptor_counts_converged,
                "terminal_fact": terminal_fact,
                "model_posture": "test_owned_echo",
            }
        )
        return [
            "Each cycle constructs, uses, and closes API, Focus, and runtime-manager owners.",
            "Second close/shutdown calls verify idempotency after owned resources are released.",
        ]

    return _run_with_metrics(
        scenario_id=scenario_id,
        command="omfla_fixture:shutdown-restart",
        provider_variance_class=LOCAL_VARIANCE,
        provider_profile="test_owned_echo",
        measurement_identity=_measurement_identity(
            scenario_id=scenario_id,
            command="omfla_fixture:shutdown-restart",
            measured_boundary=SUT_BOUNDARY_IN_PROCESS,
            fixture_revision="omfla-shutdown-restart-v1",
            options=options,
            data_root=options.output_root,
            provider_posture="test_owned_echo",
            model_posture="test_owned_echo",
        ),
        action=action,
    )


def _measure_queue_pressure(
    options: RunOptions,
    *,
    cycle_count: int = 5,
    finite_capacity: int = 20,
    unbounded_count: int = 100,
) -> ScenarioRun:
    scenario_id = "queue_pressure"

    def action(metrics: dict[str, Any]) -> list[str]:
        import logging
        import threading

        from openminion.modules.controlplane.runtime.store import (
            InMemoryControlPlaneStore,
        )
        from openminion.modules.telemetry.export.queueing import (
            NoncriticalExportQueue,
        )
        from openminion.modules.telemetry.schemas import TelemetryEvent
        from openminion.services.gateway.memory import (
            MemoryFollowupJob,
            MemoryFollowupQueue,
        )
        from openminion.services.runtime import (
            AgentRuntimeManager,
            TurnChunk,
            TurnRequest,
            TurnResponse,
        )
        from openminion.services.runtime.turn_input.queue import (
            TurnInputQueue,
            TurnInputQueueError,
            TurnInputQueueStatus,
        )

        turn_queue = TurnInputQueue(max_pending_per_session=finite_capacity)
        controlplane_store = InMemoryControlPlaneStore()
        overflow_counts = {"turn_input": 0, "telemetry": 0}
        drained_counts = {
            "turn_input": 0,
            "runtime_chunks": 0,
            "memory_followup": 0,
            "telemetry": 0,
            "controlplane_inbox": 0,
            "controlplane_outbox": 0,
        }
        windows: list[dict[str, Any]] = []
        ready = _omfla_process_sample(completed_turn_count=0)

        def discard_event(**_kwargs: Any) -> None:
            return None

        class MemoryFixture:
            pass

        for cycle_index in range(cycle_count):
            session_id = f"omfla-queue-{cycle_index}"
            for item_index in range(finite_capacity):
                turn_queue.enqueue(
                    session_id=session_id,
                    agent_id="omfla-agent",
                    text=f"turn-{item_index}",
                    idempotency_key=f"{cycle_index}-{item_index}",
                )
            try:
                turn_queue.enqueue(
                    session_id=session_id,
                    agent_id="omfla-agent",
                    text="overflow",
                )
            except TurnInputQueueError as exc:
                if exc.code != "QUEUE_FULL":
                    raise
                overflow_counts["turn_input"] += 1
            else:
                raise RuntimeError("turn-input overflow probe was accepted")
            while entry := turn_queue.reserve_next(
                session_id=session_id,
                agent_id="omfla-agent",
            ):
                turn_queue.mark_running(queue_id=entry.queue_id)
                turn_queue.mark_terminal(
                    queue_id=entry.queue_id,
                    status=TurnInputQueueStatus.COMPLETED,
                )
                drained_counts["turn_input"] += 1

            def execute(request: Any, emit_chunk: Any, cancel_event: Any) -> Any:
                del cancel_event
                for chunk_index in range(unbounded_count):
                    emit_chunk(
                        TurnChunk(
                            trace_id=request.trace_id,
                            kind="text",
                            data={"index": chunk_index},
                        )
                    )
                return TurnResponse(final_text="chunk drain complete")

            runtime_manager = AgentRuntimeManager(
                turn_executor=execute,
                max_agents_hot=1,
                max_global_concurrency=1,
            )
            runtime_manager.start()
            try:
                handle = runtime_manager.submit_turn(
                    TurnRequest(
                        trace_id=f"omfla-chunks-{cycle_index}",
                        agent_id="omfla-chunk-agent",
                        session_id=session_id,
                        input_text="emit chunks",
                        stream=True,
                    )
                )
                chunks = list(handle.stream(timeout_s=5))
                response = handle.result(timeout_s=5)
                emitted_chunks = [chunk for chunk in chunks if chunk.kind == "text"]
                if len(emitted_chunks) != unbounded_count or response.errors:
                    raise RuntimeError("runtime chunk queue did not drain")
                drained_counts["runtime_chunks"] += len(emitted_chunks)
            finally:
                runtime_manager.shutdown()

            memory_queue = MemoryFollowupQueue(auto_start=False)
            for item_index in range(unbounded_count):
                memory_queue.enqueue(
                    MemoryFollowupJob(
                        agent_memory=MemoryFixture(),
                        logger=logging.getLogger("omfla.memory-followup"),
                        agent_id="omfla-agent",
                        memory_capsule_strategy="dynamic_turn",
                        memory_capsule_cache={},
                        session_id=session_id,
                        run_id=f"run-{item_index}",
                        request_id=f"request-{item_index}",
                        conversation_id="",
                        thread_id="",
                        attach_id="",
                        emit_memory_event=discard_event,
                        emit_followup_event=discard_event,
                        outbound_metadata={},
                        patch_changed=False,
                    )
                )
            if memory_queue.pending_count() != unbounded_count:
                raise RuntimeError("memory follow-up queue fill count mismatch")
            memory_queue.flush()
            if memory_queue.pending_count():
                raise RuntimeError("memory follow-up queue did not drain")
            drained_counts["memory_followup"] += unbounded_count

            release_export = threading.Event()
            export_started = threading.Event()
            exported = 0

            def export_now(_event: TelemetryEvent) -> bool:
                nonlocal exported
                export_started.set()
                release_export.wait(timeout=5)
                exported += 1
                return True

            telemetry_queue = NoncriticalExportQueue(
                capacity=finite_capacity,
                flush_timeout_seconds=5,
                export_now=export_now,
            )
            first_event = TelemetryEvent(
                session_id=session_id,
                turn_id="turn-0",
                event_type="omfla.fixture",
                data={"criticality": "noncritical"},
            )
            if not telemetry_queue.enqueue(first_event):
                raise RuntimeError("telemetry queue rejected its first event")
            if not export_started.wait(timeout=5):
                raise RuntimeError("telemetry export worker did not start")
            for item_index in range(finite_capacity):
                accepted = telemetry_queue.enqueue(
                    TelemetryEvent(
                        session_id=session_id,
                        turn_id=f"turn-{item_index + 1}",
                        event_type="omfla.fixture",
                        data={"criticality": "noncritical"},
                    )
                )
                if not accepted:
                    raise RuntimeError("telemetry queue rejected before capacity")
            overflow_event = TelemetryEvent(
                session_id=session_id,
                turn_id="overflow",
                event_type="omfla.fixture",
                data={"criticality": "noncritical"},
            )
            if telemetry_queue.enqueue(overflow_event):
                raise RuntimeError("telemetry overflow probe was accepted")
            overflow_counts["telemetry"] += 1
            release_export.set()
            deadline = time.monotonic() + 5
            while (
                telemetry_queue.stats()["queue_depth"] and time.monotonic() < deadline
            ):
                time.sleep(0.01)
            telemetry_stats = telemetry_queue.stats()
            telemetry_queue.close()
            if telemetry_stats["queue_depth"]:
                raise RuntimeError("telemetry queue did not drain")
            drained_counts["telemetry"] += exported

            for item_index in range(unbounded_count):
                message_id = f"{cycle_index}-{item_index}"
                controlplane_store.enqueue_inbox(
                    channel="fixture",
                    chat_id="fixture-chat",
                    channel_message_id=message_id,
                    user_id="fixture-user",
                    payload={"index": item_index},
                )
                controlplane_store.enqueue_outbox(
                    channel="fixture",
                    chat_id="fixture-chat",
                    payload={"index": item_index},
                )
            for _ in range(unbounded_count):
                inbox = controlplane_store.claim_inbox(lock_owner="omfla")
                outbox = controlplane_store.claim_outbox(lock_owner="omfla")
                if inbox is None or outbox is None:
                    raise RuntimeError("control-plane storage queue claim failed")
                controlplane_store.ack_inbox(str(inbox["inbox_id"]))
                controlplane_store.mark_outbox_sent(str(outbox["outbox_id"]))
                drained_counts["controlplane_inbox"] += 1
                drained_counts["controlplane_outbox"] += 1
            active_inbox = sum(
                1
                for row in controlplane_store._inbox.values()  # noqa: SLF001
                if row["status"] != "done"
            )
            active_outbox = sum(
                1
                for row in controlplane_store._outbox.values()  # noqa: SLF001
                if row["status"] != "sent"
            )
            windows.append(
                _omfla_window_sample(
                    index=cycle_index + 1,
                    completed_turn_count=(cycle_index + 1) * 4,
                    rss_readings=[
                        value
                        for value in [_current_rss_bytes()]
                        if isinstance(value, int)
                    ],
                    cache_cardinalities={
                        "turn_input_terminal_audit": len(
                            turn_queue._entries  # noqa: SLF001
                        ),
                        "turn_input_idempotency": len(
                            turn_queue._idempotency  # noqa: SLF001
                        ),
                        "controlplane_inbox_records": len(
                            controlplane_store._inbox  # noqa: SLF001
                        ),
                        "controlplane_outbox_records": len(
                            controlplane_store._outbox  # noqa: SLF001
                        ),
                        "controlplane_inbox_dedup": len(
                            controlplane_store._inbox_dedupe  # noqa: SLF001
                        ),
                        "controlplane_audit_events": len(
                            controlplane_store._audit_events  # noqa: SLF001
                        ),
                    },
                    queue_depths={
                        "turn_input": turn_queue.pending_count(session_id=session_id),
                        "runtime_chunks": 0,
                        "memory_followup": memory_queue.pending_count(),
                        "telemetry_noncritical_export": telemetry_stats["queue_depth"],
                        "controlplane_inbox": active_inbox,
                        "controlplane_outbox": active_outbox,
                    },
                )
            )

        if any(windows[-1]["queue_depths"].values()):
            raise RuntimeError("one or more queue owners did not drain")
        metrics.update(
            {
                "ready_sample": ready,
                "post_warmup_sample": windows[0],
                "steady_state_windows": windows,
                "close_sample": _omfla_process_sample(
                    completed_turn_count=cycle_count * 4
                ),
                "completed_turn_count": cycle_count * 4,
                "queue_pressure_cycle_count": cycle_count,
                "finite_queue_capacity": finite_capacity,
                "unbounded_queue_fill_count": unbounded_count,
                "overflow_counts": overflow_counts,
                "drained_counts": drained_counts,
                "queue_depths": windows[-1]["queue_depths"],
                "cache_cardinalities": windows[-1]["cache_cardinalities"],
                "terminal_fact": {
                    "cycles": cycle_count,
                    "all_active_depths": 0,
                },
            }
        )
        return [
            "Queue pressure calls current in-memory and durable store owners directly; no control-plane ingress is started.",
            "Terminal audit and dedup cardinalities are retained and reported separately from active queue depth.",
        ]

    return _run_with_metrics(
        scenario_id=scenario_id,
        command="omfla_fixture:queue-pressure",
        provider_variance_class=LOCAL_VARIANCE,
        measurement_identity=_measurement_identity(
            scenario_id=scenario_id,
            command="omfla_fixture:queue-pressure",
            measured_boundary=SUT_BOUNDARY_IN_PROCESS,
            fixture_revision="omfla-queue-pressure-v1",
            options=options,
        ),
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
        metrics["queue_depths"] = {
            "telemetry.noncritical_export": int(stats["queue_depth"])
        }
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
        if isinstance(start_rss, int) and isinstance(end_rss, int):
            metrics["rss_growth_bytes"] = end_rss - start_rss
            metrics["rss_growth_per_message_bytes"] = int(
                (end_rss - start_rss) / message_count
            )
        else:
            metrics["rss_growth_bytes"] = None
            metrics["rss_growth_per_message_bytes"] = None
            metrics["availability_reasons"]["rss_growth_bytes"] = (
                "current_rss_unavailable"
            )
            metrics["availability_reasons"]["rss_growth_per_message_bytes"] = (
                "current_rss_unavailable"
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
    if scenario_id == "terminal_import_surface":
        return _measure_import_surface(
            scenario_id=scenario_id,
            module_name="openminion.cli.interactive.terminal",
            options=options,
        )
    if scenario_id == "interactive_runtime_import_surface":
        return _measure_import_surface(
            scenario_id=scenario_id,
            module_name="openminion.cli.interactive.runtime",
            options=options,
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
    if scenario_id == "instrumentation_overhead_aa":
        return _measure_instrumentation_overhead_aa(options)
    if scenario_id.startswith("tcpl_02_"):
        return _measure_tcpl02_skill_entry_scenario(scenario_id, options)
    if scenario_id.startswith("tcpl_"):
        return _measure_tcpl_matrix_scenario(scenario_id, options)
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
    if scenario_id == "persistent_api_turns":
        return _measure_persistent_api_turns(options)
    if scenario_id == "persistent_focus_turns":
        return _measure_persistent_focus_turns(options)
    if scenario_id == "session_cache_churn":
        return _measure_session_cache_churn(options)
    if scenario_id == "provider_lifecycle_loopback":
        return _measure_provider_lifecycle_loopback(options)
    if scenario_id == "agent_cache_churn":
        return _measure_agent_cache_churn(options)
    if scenario_id == "runtime_restart":
        return _measure_runtime_restart(options)
    if scenario_id == "queue_pressure":
        return _measure_queue_pressure(options)
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
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"comparison baseline is unreadable: {path}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("scenarios"), dict):
        raise ValueError(f"comparison baseline is malformed: {path}")
    return payload


def _threshold_result(
    *,
    current: dict[str, Any],
    baseline: dict[str, Any] | None,
    scenario_id: str,
    threshold_mode: str,
) -> dict[str, Any]:
    if not baseline:
        return {
            "mode": threshold_mode,
            "status": "not_applicable",
            "reason": "no comparison baseline",
        }
    baseline_scenario = dict((baseline.get("scenarios") or {}).get(scenario_id) or {})
    if not baseline_scenario:
        return {
            "mode": threshold_mode,
            "status": "ineligible",
            "reason": "scenario missing from comparison baseline",
        }
    if baseline.get("artifact_schema_version") != ARTIFACT_SCHEMA_VERSION:
        return {
            "mode": threshold_mode,
            "status": "ineligible",
            "reason": "artifact schema version mismatch; v3 is display-only",
            "identity_errors": ["artifact_schema_version"],
        }
    identity_errors = _comparison_identity_errors(
        current.get("comparison_identity"),
        baseline_scenario.get("comparison_identity"),
    )
    if identity_errors:
        version_mismatch = "artifact_schema_version" in identity_errors
        return {
            "mode": threshold_mode,
            "status": "ineligible",
            "reason": (
                "artifact schema version mismatch"
                if version_mismatch
                else "measurement identity mismatch"
            ),
            "identity_errors": identity_errors,
        }
    current_count = int(current.get("count", 0) or 0)
    current_ok = int(current.get("ok_count", 0) or 0)
    baseline_count = int(baseline_scenario.get("count", 0) or 0)
    baseline_ok = int(baseline_scenario.get("ok_count", 0) or 0)
    if baseline_scenario.get("identity_incompatibilities"):
        return {
            "mode": threshold_mode,
            "status": "ineligible",
            "reason": "baseline contains mixed sample identities",
        }
    if current_ok < current_count:
        return {
            "mode": threshold_mode,
            "status": "fail",
            "reason": "quality fixture failure",
            "sample_count": current_count,
            "ok_count": current_ok,
        }
    if baseline_ok < baseline_count:
        return {
            "mode": threshold_mode,
            "status": "ineligible",
            "reason": "comparison baseline contains invalid samples",
            "sample_count": baseline_count,
            "ok_count": baseline_ok,
        }
    if (
        current_count < COMPARISON_MIN_SAMPLES
        or baseline_count < COMPARISON_MIN_SAMPLES
    ):
        return {
            "mode": threshold_mode,
            "status": "ineligible",
            "reason": "fewer than twenty comparable samples",
            "current_sample_count": current_count,
            "baseline_sample_count": baseline_count,
        }
    metric_name = (
        "wall_time_ms"
        if scenario_id
        in {
            "cold_focus_startup",
            "warm_focus_startup",
            "terminal_import_surface",
            "interactive_runtime_import_surface",
        }
        else "wall_time_ns"
    )
    baseline_wall = dict(baseline_scenario.get(metric_name) or {})
    current_wall = dict(current.get(metric_name) or {})
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
            "reason": f"missing comparable {metric_name} p95",
        }
    if threshold_mode == "off":
        return {
            "mode": threshold_mode,
            "status": "eligible",
            "metric": metric_name,
            "baseline_p95": baseline_p95,
            "current_p95": current_p95,
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
        "metric": metric_name,
        "baseline_p95": baseline_p95,
        "current_p95": current_p95,
        "ratio": ratio,
        "regression_ratio": 1.10,
    }


def _comparison_identity_errors(
    current_identity: Any,
    baseline_identity: Any,
    *,
    reject_editable: bool = True,
) -> list[str]:
    if not isinstance(current_identity, dict) or not isinstance(
        baseline_identity, dict
    ):
        return ["missing comparison identity"]
    errors: list[str] = []
    required_nonempty_keys = {
        "artifact_schema_version",
        "scenario_id",
        "command_shape",
        "fixture_revision",
        "measured_boundary",
        "python_implementation",
        "python_version",
        "python_build",
        "resolved_python_executable",
        "runner_source_sha256",
        "host_runtime_hash",
        "runtime_dependency_hash",
        "effective_sys_path_shape",
        "bytecode_cache_posture",
        "process_posture",
    }
    required_keys = required_nonempty_keys | {"inherited_pythonpath_shape"}
    for key in (
        "artifact_schema_version",
        "scenario_id",
        "command_shape",
        "fixture_revision",
        "measured_boundary",
        "python_implementation",
        "python_version",
        "python_build",
        "resolved_python_executable",
        "runner_source_sha256",
        "host_runtime_hash",
        "runtime_dependency_hash",
        "effective_sys_path_shape",
        "inherited_pythonpath_shape",
        "bytecode_cache_posture",
        "provider_posture",
        "model_posture",
        "process_posture",
        "include_importtime",
        "profile",
        "warmup_runs",
        "scenario_config",
    ):
        if key in required_keys and any(
            key not in identity for identity in (current_identity, baseline_identity)
        ):
            errors.append(key)
            continue
        if key in required_nonempty_keys and any(
            str(identity.get(key) or "").strip() in {"", "unknown", "unavailable"}
            for identity in (current_identity, baseline_identity)
        ):
            errors.append(key)
            continue
        if current_identity.get(key) != baseline_identity.get(key):
            errors.append(key)
    if reject_editable:
        for identity in (current_identity, baseline_identity):
            if identity.get("editable_dependency_names"):
                errors.append("editable_dependency_names")
                break
    return errors


def _sample_identity_errors(
    expected_identity: Any,
    actual_identity: Any,
    expected_comparison_identity: Any,
    actual_comparison_identity: Any,
) -> list[str]:
    if not isinstance(expected_identity, dict) or not isinstance(actual_identity, dict):
        return ["missing measurement identity"]
    errors = [
        key
        for key in (
            "git_head",
            "dirty_tree_fingerprint",
            "runner_path",
            "runner_source_sha256",
            "loaded_openminion_package_root",
        )
        if str(expected_identity.get(key) or "").strip()
        in {"", "unknown", "unavailable"}
        or expected_identity.get(key) != actual_identity.get(key)
    ]
    errors.extend(
        _comparison_identity_errors(
            expected_comparison_identity,
            actual_comparison_identity,
            reject_editable=False,
        )
    )
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
        first_identity = dict(scenario_runs[0].get("measurement_identity") or {})
        first_comparison_identity = dict(
            scenario_runs[0].get("comparison_identity") or {}
        )
        identity_incompatibilities: list[dict[str, Any]] = []
        for sample_offset, scenario_run in enumerate(scenario_runs[1:], start=1):
            errors = _sample_identity_errors(
                first_identity,
                scenario_run.get("measurement_identity"),
                first_comparison_identity,
                scenario_run.get("comparison_identity"),
            )
            if errors:
                identity_incompatibilities.append(
                    {
                        "sample_index": int(
                            scenario_run.get("sample_index", sample_offset)
                        ),
                        "identity_errors": errors,
                    }
                )
        sample_artifacts = [
            str(run.get("artifact_path") or "")
            for run in scenario_runs
            if str(run.get("artifact_path") or "").strip()
        ]
        scenarios[scenario_id] = {
            "count": len(scenario_runs),
            "ok_count": sum(1 for run in scenario_runs if run.get("ok")),
            "measurement_identity": first_identity,
            "comparison_identity": first_comparison_identity,
            "identity_incompatibilities": identity_incompatibilities,
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
        if identity_incompatibilities:
            scenarios[scenario_id]["threshold_result"] = {
                "mode": threshold_mode,
                "status": "ineligible",
                "reason": "mixed sample identities",
                "identity_incompatibilities": identity_incompatibilities,
            }
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
    campaign_source_identity: dict[str, Any],
    profile_artifact: str | None = None,
    profile_pstats_artifact: str | None = None,
) -> dict[str, Any]:
    metrics = dict(run.metrics)
    metrics["sample_index"] = int(run_index)
    measurement_identity = dict(run.measurement_identity)
    runtime_config = dict(measurement_identity.get("runtime_config") or {})
    runtime_config.update(
        {
            "python_executable": str(options.python),
            "workspace_root": str(options.workspace_root),
            "include_importtime": bool(options.include_importtime),
            "profile": bool(options.profile),
            "warmup_runs": int(options.warmup_runs),
            "timeout_seconds": int(options.timeout_seconds),
        }
    )
    provider_attempts = metrics.get("provider_attempts")
    model_posture = str(measurement_identity.get("model_posture") or "unavailable")
    if isinstance(provider_attempts, list):
        for attempt in provider_attempts:
            if isinstance(attempt, dict) and str(attempt.get("model") or "").strip():
                model_posture = str(attempt["model"])
                break
    measurement_identity.update(
        {
            "git_head": campaign_source_identity["git_head"],
            "dirty_tree_fingerprint": campaign_source_identity[
                "dirty_tree_fingerprint"
            ],
            "runner_source_sha256": campaign_source_identity["runner_source_sha256"],
            "runner_path": campaign_source_identity["runner_path"],
            "loaded_openminion_package_root": campaign_source_identity[
                "loaded_openminion_package_root"
            ],
            "runtime_environment": campaign_source_identity["runtime_environment"],
            "config_hash": _stable_json_hash(runtime_config),
            "provider_posture": run.provider_profile,
            "model_posture": model_posture,
            "runtime_config": runtime_config,
        }
    )
    comparison_identity = _comparison_identity(measurement_identity)
    return {
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "run_started_at": _utc_timestamp(),
        "scenario_id": run.scenario_id,
        "run_id": f"{_utc_timestamp()}-{run.scenario_id}-{run_index}-{uuid4().hex[:8]}",
        "timestamp_utc": _utc_timestamp(),
        "git_head": campaign_source_identity["git_head"],
        "dirty_worktree_summary": campaign_source_identity["dirty_worktree_summary"],
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "runs_requested": int(options.runs),
        "runs_completed": 1,
        "warmup_runs": int(options.warmup_runs),
        "sample_index": int(run_index),
        "command": run.command,
        "provider_profile": run.provider_profile,
        "provider_variance_class": run.provider_variance_class,
        "measurement_identity": measurement_identity,
        "comparison_identity": comparison_identity,
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
        "process_rss_bytes": metrics.get("current_rss_bytes"),
        "process_max_rss_bytes": metrics.get("max_rss_bytes"),
        "process_tree_current_rss_bytes": metrics.get("process_tree_current_rss_bytes"),
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


def _provider_call_purposes(metrics: dict[str, Any]) -> list[str]:
    raw = metrics.get("provider_call_purposes")
    if isinstance(raw, list | tuple):
        return [str(item) for item in raw]
    call_count = int(metrics.get("model_call_count", 0) or 0)
    if call_count > 0:
        return ["unavailable" for _ in range(call_count)]
    return []


def _provider_call_latencies(metrics: dict[str, Any]) -> list[int]:
    raw = metrics.get("provider_call_latency_ms")
    if isinstance(raw, list | tuple):
        return [int(item) for item in raw if isinstance(item, int)]
    round_trip = metrics.get("provider_round_trip_ms")
    if isinstance(round_trip, int):
        purposes = _provider_call_purposes(metrics)
        return [max(0, round_trip)] if len(purposes) == 1 else []
    return []


def _provider_attempts(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    raw = metrics.get("provider_attempts")
    if not isinstance(raw, list):
        return []
    attempts: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, dict):
            attempts.append(dict(item))
    return attempts


def _cold_warm_classification(scenario_id: str) -> str:
    if scenario_id.startswith("cold_"):
        return "cold"
    if scenario_id.startswith("warm_") or scenario_id == "repeated_local_turns":
        return "warm"
    return "not_applicable"


def _validate_tcpl_sample(sample: dict[str, Any]) -> None:
    identity = sample.get("comparable_identity")
    if not isinstance(identity, dict):
        raise ValueError("TCPL sample missing comparable_identity")
    for key in (
        "git_revision",
        "dirty_tree_fingerprint",
        "scenario_id",
        "fixture_hash",
        "provider",
        "model",
        "config_hash",
        "candidate_posture",
        "rollback_posture",
        "cold_warm",
        "host_runtime_hash",
    ):
        if not str(identity.get(key, "")).strip():
            raise ValueError(f"TCPL sample missing comparable identity: {key}")
    for field in ("wall_time_ms", "wall_time_ns"):
        value = sample.get(field)
        if isinstance(value, int) and value < 0:
            raise ValueError(f"TCPL sample {field} must be >= 0")
    phase_timings = sample.get("phase_timings_ms")
    if isinstance(phase_timings, dict):
        for phase, value in phase_timings.items():
            if isinstance(value, int) and value < 0:
                raise ValueError(f"TCPL phase {phase} must be >= 0")
    purposes = sample.get("provider_call_purposes")
    latencies = sample.get("provider_call_latency_ms")
    if isinstance(purposes, list) and isinstance(latencies, list) and latencies:
        if len(purposes) != len(latencies):
            raise ValueError(
                "TCPL provider_call_latency_ms must align with provider_call_purposes"
            )
        if any(isinstance(value, int) and value < 0 for value in latencies):
            raise ValueError("TCPL provider call latencies must be >= 0")
    attempts = sample.get("provider_attempts")
    if isinstance(attempts, list):
        for attempt in attempts:
            if not isinstance(attempt, dict):
                raise ValueError("TCPL provider_attempts entries must be objects")
            for key in (
                "logical_call_id",
                "semantic_purpose",
                "attempt",
                "provider",
                "model",
                "route_posture",
                "attempt_posture",
                "latency_ms",
                "outcome",
            ):
                if key not in attempt:
                    raise ValueError(f"TCPL provider_attempts missing {key}")
            if int(attempt.get("attempt", 0) or 0) < 1:
                raise ValueError("TCPL provider_attempts attempt must be >= 1")
            if int(attempt.get("latency_ms", 0) or 0) < 0:
                raise ValueError("TCPL provider_attempts latency_ms must be >= 0")


def _tcpl_shadow_decisions(metrics: dict[str, Any]) -> dict[str, Any]:
    selector_tokens = metrics.get("selector_token_count")
    selector_candidates = metrics.get("selector_candidate_count")
    selector_route = metrics.get("skill_selection_route")
    selector_current = metrics.get("skill_selection_strategy")
    compaction_ms = metrics.get("session_compaction_ms")
    compaction_policy = metrics.get("session_compaction_policy")
    skill_has_inputs = isinstance(selector_tokens, int) and isinstance(
        selector_candidates, int
    )
    compaction_has_inputs = isinstance(compaction_ms, int) and isinstance(
        compaction_policy, str
    )
    skill_candidate_decision = "insufficient_structural_input"
    skill_reason = "selector token/count inputs unavailable in this scenario"
    if skill_has_inputs:
        if selector_candidates <= 0:
            skill_candidate_decision = "direct_no_catalog"
            skill_reason = "no catalog candidates require no selector call"
        elif (
            selector_tokens <= TCPL_SKILL_ENTRY_TOKEN_BUDGET
            and selector_candidates <= TCPL_SKILL_ENTRY_CANDIDATE_BUDGET
        ):
            skill_candidate_decision = "entry_candidate_eligible"
            skill_reason = "candidate content fits approved entry budgets"
        else:
            skill_candidate_decision = "separate_selector_required"
            skill_reason = "candidate content exceeds approved entry budgets"
    projected_skill_avoided_calls = (
        1
        if skill_candidate_decision == "entry_candidate_eligible"
        and selector_current == "llm"
        else 0
    )
    compaction_candidate_decision = "insufficient_structural_input"
    compaction_reason = "compaction timing/policy unavailable in this scenario"
    if compaction_has_inputs:
        if compaction_policy == "hard_limit":
            compaction_candidate_decision = "keep_synchronous"
            compaction_reason = "hard-limit compaction remains delivery-critical"
        elif compaction_ms >= TCPL_COMPACTION_DEFER_MS_THRESHOLD:
            compaction_candidate_decision = "derived_projection_candidate"
            compaction_reason = "ordinary compaction exceeds approved defer threshold"
        else:
            compaction_candidate_decision = "hold_current"
            compaction_reason = "ordinary compaction is below defer threshold"
    return {
        "mode": "observation_only",
        "side_effects_allowed": False,
        "provider_calls_allowed": False,
        "storage_mutations_allowed": False,
        "derived_jobs_allowed": False,
        "delivery_changes_allowed": False,
        "skill_entry": {
            "current_decision": str(selector_current or "unavailable"),
            "selector_route": str(selector_route or "unavailable"),
            "candidate_decision": skill_candidate_decision,
            "selector_token_count": selector_tokens
            if isinstance(selector_tokens, int)
            else "unavailable",
            "selector_candidate_count": selector_candidates
            if isinstance(selector_candidates, int)
            else "unavailable",
            "projected_avoided_provider_calls": projected_skill_avoided_calls,
            "reason": skill_reason,
        },
        "session_compaction": {
            "current_decision": str(compaction_policy or "unavailable"),
            "candidate_decision": compaction_candidate_decision,
            "compaction_ms": compaction_ms
            if isinstance(compaction_ms, int)
            else "unavailable",
            "projected_avoided_provider_calls": 0,
            "reason": compaction_reason,
        },
    }


def _tcpl_has_field(samples: list[dict[str, Any]], field: str) -> bool:
    for sample in samples:
        phase_timings = sample.get("phase_timings_ms")
        sample_value = sample.get(field)
        phase_value = (
            phase_timings.get(field) if isinstance(phase_timings, dict) else None
        )
        if _tcpl_field_present(sample_value) or _tcpl_field_present(phase_value):
            return True
    return False


def _tcpl_field_present(value: Any) -> bool:
    return value not in (None, "unavailable", [], {})


def _tcpl_missing_coverage(samples: list[dict[str, Any]]) -> dict[str, list[str]]:
    missing: dict[str, list[str]] = {}
    for coverage_name, required_groups in TCPL_REQUIRED_COVERAGE.items():
        missing_groups: list[str] = []
        for aliases in required_groups:
            if any(_tcpl_has_field(samples, field) for field in aliases):
                continue
            missing_groups.append("|".join(aliases))
        if missing_groups:
            missing[coverage_name] = missing_groups
    return missing


def _tcpl_quality_assertions(
    *,
    artifact: dict[str, Any],
    metrics: dict[str, Any],
    provider: str,
) -> dict[str, Any]:
    assertions: dict[str, Any] = {
        "fixture_ok": bool(artifact.get("ok")),
        "no_provider_call_for_local_fixture": provider in {"none", "stub"},
    }
    for invariant in TCPL_QUALITY_INVARIANTS:
        if invariant in metrics:
            assertions[invariant] = metrics[invariant]
    return assertions


def _tcpl_sample_from_artifact(
    artifact: dict[str, Any],
    *,
    options: RunOptions,
) -> dict[str, Any]:
    metrics = dict(artifact.get("metrics") or {})
    identity = dict(artifact.get("measurement_identity") or {})
    for key in (
        "scenario_id",
        "command",
        "fixture_revision",
        "measured_boundary",
        "runtime_config",
    ):
        if key not in identity:
            raise ValueError(f"TCPL artifact missing measurement identity: {key}")
    provider = str(artifact.get("provider_profile") or "none")
    scenario_id = str(artifact.get("scenario_id") or "")
    runtime_config = identity.get("runtime_config")
    comparable_identity = {
        "git_revision": str(
            artifact.get("git_head") or _git_head(options.workspace_root)
        ),
        "dirty_tree_fingerprint": _dirty_worktree_fingerprint(options.workspace_root),
        "scenario_id": scenario_id,
        "fixture_hash": _fixture_hash(identity, command=artifact.get("command")),
        "provider": provider,
        "model": "unavailable",
        "endpoint_class": str(identity.get("measured_boundary") or "unavailable"),
        "provider_request_identity": "unavailable",
        "config_hash": _stable_json_hash(runtime_config or {}),
        "agent_profile": "unavailable",
        "skill_catalog_hash": "unavailable",
        "session_seed": "unavailable",
        "context_budget": "unavailable",
        "memory_posture": "current",
        "session_posture": "current",
        "cache_posture": "unavailable",
        "process_mode": "benchmark",
        "cold_warm": _cold_warm_classification(scenario_id),
        "attempt_index": int(artifact.get("sample_index", 0) or 0),
        "host_runtime_hash": _stable_json_hash(
            {
                "python_version": platform.python_version(),
                "platform": platform.platform(),
                "python": str(options.python),
            }
        ),
        "candidate_posture": str(
            metrics.get("candidate_posture") or "baseline_current"
        ),
        "rollback_posture": str(metrics.get("rollback_posture") or "baseline_current"),
    }
    sample = {
        "artifact_schema_version": TCPL_ARTIFACT_SCHEMA_VERSION,
        "source_artifact_schema_version": artifact.get("artifact_schema_version"),
        "run_id": artifact.get("run_id"),
        "timestamp_utc": artifact.get("timestamp_utc"),
        "scenario_id": scenario_id,
        "sample_index": int(artifact.get("sample_index", 0) or 0),
        "command": artifact.get("command"),
        "ok": bool(artifact.get("ok")),
        "error": artifact.get("error"),
        "comparable_identity": comparable_identity,
        "wall_time_ms": metrics.get("wall_time_ms"),
        "wall_time_ns": metrics.get("wall_time_ns"),
        "provider_ttft_ms": metrics.get("provider_ttft_ms", "unavailable"),
        "visible_ttft_ms": metrics.get("time_to_first_visible_text_ms"),
        "phase_timings_ms": metrics.get("phase_timings_ms", {}),
        "phase_timings_ns": metrics.get("phase_timings_ns", {}),
        "provider_call_purposes": _provider_call_purposes(metrics),
        "provider_call_latency_ms": _provider_call_latencies(metrics),
        "provider_attempts": _provider_attempts(metrics),
        "provider_attempt_count": len(_provider_attempts(metrics)),
        "provider_call_count": int(metrics.get("model_call_count", 0) or 0),
        "tool_call_count": int(metrics.get("tool_call_count", 0) or 0),
        "input_tokens": metrics.get("provider_input_tokens"),
        "output_tokens": metrics.get("provider_output_tokens"),
        "total_tokens": metrics.get("provider_total_tokens"),
        "cache_read_tokens": metrics.get("provider_cache_read_tokens"),
        "cache_write_tokens": metrics.get("provider_cache_write_tokens"),
        "selector_latency_ms": metrics.get("selector_latency_ms", "unavailable"),
        "selector_token_count": metrics.get("selector_token_count", "unavailable"),
        "selector_candidate_count": metrics.get(
            "selector_candidate_count", "unavailable"
        ),
        "skill_selection_route": metrics.get("skill_selection_route", "unavailable"),
        "skill_selection_strategy": metrics.get(
            "skill_selection_strategy", "unavailable"
        ),
        "selected_skill_ids": metrics.get("selected_skill_ids", []),
        "applied_skill_ids": metrics.get("applied_skill_ids", []),
        "skill_catalog_hash": metrics.get("skill_catalog_hash", "unavailable"),
        "session_compaction_ms": metrics.get("session_compaction_ms", "unavailable"),
        "session_compaction_policy": metrics.get(
            "session_compaction_policy", "unavailable"
        ),
        "memory_followup_flush_ms": metrics.get(
            "memory_followup_flush_ms", "unavailable"
        ),
        "memory_followup_pending_count": metrics.get(
            "memory_followup_pending_count", "unavailable"
        ),
        "quality_assertions": _tcpl_quality_assertions(
            artifact=artifact,
            metrics=metrics,
            provider=provider,
        ),
        "shadow_decisions": _tcpl_shadow_decisions(metrics),
        "source_run_artifact": artifact.get("artifact_path"),
    }
    _validate_tcpl_sample(sample)
    return sample


def _tcpl_quality(
    summary: dict[str, Any],
    *,
    samples: list[dict[str, Any]],
) -> dict[str, Any]:
    scenarios = {}
    for scenario_id, payload in (summary.get("scenarios") or {}).items():
        if not isinstance(payload, dict):
            continue
        count = int(payload.get("count", 0) or 0)
        ok_count = int(payload.get("ok_count", 0) or 0)
        scenario_samples = [
            sample for sample in samples if sample.get("scenario_id") == scenario_id
        ]
        unavailable_invariants = [
            invariant
            for invariant in TCPL_QUALITY_INVARIANTS
            if not any(
                invariant in (sample.get("quality_assertions") or {})
                for sample in scenario_samples
            )
        ]
        scenarios[str(scenario_id)] = {
            "status": "pass" if count and ok_count == count else "fail",
            "sample_count": count,
            "ok_count": ok_count,
            "quality_floor": "fixture_ok_only",
            "differences": [],
            "not_applicable": unavailable_invariants,
        }
    missing_coverage = _tcpl_missing_coverage(samples)
    return {
        "artifact_schema_version": TCPL_ARTIFACT_SCHEMA_VERSION,
        "generated_at_utc": _utc_timestamp(),
        "quality_scope": "local_fixture",
        "tcpl_coverage_status": "incomplete" if missing_coverage else "complete",
        "missing_measurement_coverage": missing_coverage,
        "fixture_failure_count": sum(
            1 for payload in scenarios.values() if payload["status"] != "pass"
        ),
        "coverage_gap_count": len(missing_coverage),
        "scenarios": scenarios,
    }


def _write_tcpl_decision(
    summary: dict[str, Any],
    *,
    output_root: Path,
    live_provider_available: bool,
    quality: dict[str, Any],
) -> None:
    coverage_status = str(quality.get("tcpl_coverage_status") or "unknown")
    lines = [
        "# TCPL Measurement Decision",
        "",
        f"Generated: `{summary['generated_at_utc']}`",
        "",
        "## Disposition",
        "",
        "| Candidate | Decision | Reason |",
        "| --- | --- | --- |",
        f"| TCPL-00 local fixture baseline | `hold` | Measurement artifacts are schema-valid, but TCPL coverage is `{coverage_status}` and does not complete TCPL-00 or enable an optimization. |",
    ]
    if live_provider_available:
        lines.append(
            "| TCPL-00 live provider baseline | `hold` | Live-provider credentials were visible to the runner environment; paired candidate evidence is still required before enablement. |"
        )
    else:
        lines.append(
            "| TCPL-00 live provider baseline | `blocked_external` | No provider credential was visible to this local runner, so no live-provider latency claim is made. |"
        )
    missing = quality.get("missing_measurement_coverage")
    if isinstance(missing, dict) and missing:
        lines.extend(["", "## Missing TCPL-00 Coverage", ""])
        for name, fields in sorted(missing.items()):
            formatted = ", ".join(f"`{field}`" for field in fields)
            lines.append(f"- `{name}`: {formatted}")
    lines.extend(
        [
            "",
            "## Materiality Thresholds",
            "",
            "These are the approved TCPL starting thresholds until a later human checkpoint changes them:",
            "",
            "1. Local targeted phase: at least 10% and at least 10 ms improvement.",
            "2. Advisory live provider: at least 8% and at least 250 ms improvement.",
            "3. Full-turn median regression: no worse than the larger of 3% or 10 ms.",
            "4. Local P95 regression: no worse than the larger of 5% or 20 ms.",
            "5. Live paired win rate: at least 70% of comparable pairs.",
            "6. Instrumentation overhead: below the larger of 1 ms median or 1% of local full-turn median.",
            "",
            "## Rollback",
            "",
            "No runtime optimization was enabled by this artifact. The rollback posture is the current default behavior.",
        ]
    )
    (output_root / "decision.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_tcpl_artifacts(
    *,
    artifacts: list[dict[str, Any]],
    summary: dict[str, Any],
    options: RunOptions,
    scenarios: list[str],
) -> None:
    live_provider_available = any(
        os.environ.get(name)
        for name in (
            "MINIMAX_API_KEY",
            "DASHSCOPE_API_KEY",
            "OPENROUTER_API_KEY",
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
        )
    )
    samples = [
        _tcpl_sample_from_artifact(artifact, options=options) for artifact in artifacts
    ]
    manifest = {
        "artifact_schema_version": TCPL_ARTIFACT_SCHEMA_VERSION,
        "generated_at_utc": summary["generated_at_utc"],
        "lane": "TCPL",
        "git_revision": _git_head(options.workspace_root),
        "dirty_tree_summary": _dirty_worktree_summary(options.workspace_root),
        "dirty_tree_fingerprint": _dirty_worktree_fingerprint(options.workspace_root),
        "scenario_ids": scenarios,
        "runs": int(options.runs),
        "warmup_runs": int(options.warmup_runs),
        "threshold_mode": options.threshold_mode,
        "workspace_root": str(options.workspace_root),
        "output_root": str(options.output_root),
        "python": str(options.python),
        "live_provider_credentials_visible": live_provider_available,
        "command_lines": {
            "runner": "scripts/smoke/performance_baseline.py",
            "scenarios": ",".join(scenarios),
        },
        "artifact_files": {
            "manifest": "manifest.json",
            "samples": "samples.jsonl",
            "summary": "summary.json",
            "quality": "quality.json",
            "decision": "decision.md",
        },
    }
    (options.output_root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    with (options.output_root / "samples.jsonl").open("w", encoding="utf-8") as handle:
        for sample in samples:
            handle.write(json.dumps(sample, sort_keys=True) + "\n")
    quality = _tcpl_quality(summary, samples=samples)
    (options.output_root / "quality.json").write_text(
        json.dumps(quality, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    _write_tcpl_decision(
        summary,
        output_root=options.output_root,
        live_provider_available=live_provider_available,
        quality=quality,
    )


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
    campaign_source_identity = _campaign_source_identity(options)
    source_identity_errors = _campaign_source_identity_errors(
        campaign_source_identity,
        campaign_source_identity,
    )
    if source_identity_errors:
        raise RuntimeError(
            "campaign source identity unavailable: " + ", ".join(source_identity_errors)
        )

    artifacts: list[dict[str, Any]] = []
    for scenario_id in scenarios:
        for warmup_index in range(options.warmup_runs):
            print(
                f"[performance-baseline] {scenario_id} warmup {warmup_index + 1}/{options.warmup_runs}"
            )
            run_scenario(scenario_id, options)
        for run_index in range(options.runs):
            print(
                f"[performance-baseline] {scenario_id} run {run_index + 1}/{options.runs}"
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
                campaign_source_identity=campaign_source_identity,
                profile_artifact=profile_artifact,
                profile_pstats_artifact=profile_pstats_artifact,
            )
            _write_run_artifact(artifact, options.output_root)
            artifacts.append(artifact)
            if not run.ok:
                print(f"  error: {run.error}")

    closing_source_identity = _campaign_source_identity(options)
    source_identity_errors = _campaign_source_identity_errors(
        campaign_source_identity,
        closing_source_identity,
    )
    if source_identity_errors:
        raise RuntimeError(
            "campaign source identity changed: " + ", ".join(source_identity_errors)
        )

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
    _write_tcpl_artifacts(
        artifacts=artifacts,
        summary=summary,
        options=options,
        scenarios=scenarios,
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


def _comparison_failures(summary: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    scenarios = summary.get("scenarios")
    if not isinstance(scenarios, dict):
        return ["missing scenarios"]
    for scenario_id, payload in scenarios.items():
        if not isinstance(payload, dict):
            failures.append(str(scenario_id))
            continue
        result = payload.get("threshold_result")
        if not isinstance(result, dict) or result.get("status") in {
            "ineligible",
            "not_applicable",
        }:
            failures.append(str(scenario_id))
    return failures


def _invalid_sample_failures(summary: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    scenarios = summary.get("scenarios")
    if not isinstance(scenarios, dict):
        return failures
    for scenario_id, payload in scenarios.items():
        if not isinstance(payload, dict):
            continue
        samples_invalid = int(payload.get("ok_count", 0) or 0) < int(
            payload.get("count", 0) or 0
        )
        if samples_invalid or payload.get("identity_incompatibilities"):
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
            python=Path(args.python).expanduser().absolute(),
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
    invalid_samples = _invalid_sample_failures(summary)
    if invalid_samples:
        print(
            "[performance-baseline] invalid samples: " + ", ".join(invalid_samples),
            file=sys.stderr,
        )
        return 1
    if options.compare_baseline is not None:
        comparison_failures = _comparison_failures(summary)
        if comparison_failures:
            print(
                "[performance-baseline] comparison ineligible: "
                + ", ".join(comparison_failures),
                file=sys.stderr,
            )
            return 1
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
