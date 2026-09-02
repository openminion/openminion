from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import types
from pathlib import Path


_SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "scripts"
    / "smoke"
    / "performance_baseline.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "performance_baseline_test_load", _SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _bound_identity(module, **kwargs):
    identity = module._measurement_identity(**kwargs)
    return _complete_identity(module, identity)


def _complete_identity(module, identity):
    runtime_config = dict(identity["runtime_config"])
    runtime_config.update(
        {
            "python_executable": sys.executable,
            "workspace_root": str(_SCRIPT_PATH.parents[2].parent),
        }
    )
    identity.update(
        {
            "git_head": "a" * 40,
            "dirty_tree_fingerprint": "b" * 64,
            "runner_path": "/opt/owpr/performance_baseline.py",
            "runner_source_sha256": "c" * 64,
            "loaded_openminion_package_root": "/opt/owpr/openminion",
            "runtime_environment": {
                "resolved_python_executable": sys.executable,
                "running_python_executable": sys.executable,
                "python_implementation": "CPython",
                "python_version": sys.version.split()[0],
                "python_build": ["test", "test"],
                "platform": "test-platform",
                "host_runtime_hash": "d" * 64,
                "effective_sys_path": ["/work/openminion/src", "/work/openminion"],
                "effective_sys_path_shape": ["<SUT_SRC>", "<SUT_REPO>"],
                "inherited_pythonpath": "/work/openminion/src:/work/openminion",
                "inherited_pythonpath_shape": ["<SUT_SRC>", "<SUT_REPO>"],
                "bytecode_cache_environment": {
                    "dont_write_bytecode": "1",
                    "pycache_prefix": "/tmp/pycache",
                    "pycache_posture": "external",
                    "no_user_site": "1",
                },
                "runtime_dependency_hash": "e" * 64,
                "editable_dependency_names": [],
                "distributions": [],
            },
            "config_hash": module._stable_json_hash(runtime_config),
            "runtime_config": runtime_config,
        }
    )
    return identity


def _omfla_options(module, output_root: Path):
    return module.RunOptions(
        workspace_root=Path(__file__).resolve().parents[3],
        output_root=output_root,
        python=Path(sys.executable),
        runs=1,
        timeout_seconds=60,
        include_importtime=False,
        profile=False,
        threshold_mode="off",
    )


def test_runner_script_importable() -> None:
    module = _load_module()

    assert hasattr(module, "main")
    assert hasattr(module, "run_baseline")
    assert hasattr(module, "summarize_runs")


def test_scenario_list_accepts_all_and_rejects_unknown() -> None:
    module = _load_module()

    assert module._scenario_list("all") == list(module.DEFAULT_SCENARIOS)
    assert module._scenario_list(
        "simple_turn,instrumentation_overhead_aa,context_heavy_turn"
    ) == ["simple_turn", "instrumentation_overhead_aa", "context_heavy_turn"]

    try:
        module._scenario_list("not_real")
    except ValueError as exc:
        assert "unknown scenario" in str(exc)
    else:
        raise AssertionError("unknown scenario should fail")


def test_summarize_runs_records_metric_units_and_warn_only() -> None:
    module = _load_module()
    identity = module._measurement_identity(
        scenario_id="local_status_tool_turn",
        command="local_status_fixture",
        measured_boundary=module.SUT_BOUNDARY_IN_PROCESS,
        fixture_revision="test",
    )
    runs = [
        {
            "scenario_id": "local_status_tool_turn",
            "ok": True,
            "provider_variance_class": module.LOCAL_VARIANCE,
            "measurement_identity": identity,
            "artifact_path": "/tmp/run-1.json",
            "metrics": {
                "wall_time_ms": 10,
                "rss_delta_bytes": 100,
                "tracemalloc_peak_bytes": 1000,
                "prompt_tokens_estimated": 5,
                "segment_family_metrics": [
                    {
                        "segment_family": "replay_user",
                        "prompt_bytes": 40,
                        "prompt_tokens_estimated": 10,
                    }
                ],
                "tool_family_metrics": [
                    {
                        "tool_family": "local_status",
                        "tool_schema_bytes": 120,
                        "tool_call_count": 1,
                    }
                ],
            },
        },
        {
            "scenario_id": "local_status_tool_turn",
            "ok": True,
            "provider_variance_class": module.LOCAL_VARIANCE,
            "measurement_identity": identity,
            "artifact_path": "/tmp/run-2.json",
            "metrics": {
                "wall_time_ms": 20,
                "rss_delta_bytes": 200,
                "tracemalloc_peak_bytes": 1500,
                "prompt_tokens_estimated": 7,
            },
        },
        {
            "scenario_id": "provider_turn",
            "ok": True,
            "provider_variance_class": module.WARN_ONLY_VARIANCE,
            "measurement_identity": module._measurement_identity(
                scenario_id="provider_turn",
                command="provider_fixture",
                measured_boundary=module.SUT_BOUNDARY_REPLAY,
                fixture_revision="test",
            ),
            "metrics": {
                "wall_time_ms": 50,
                "rss_delta_bytes": 0,
                "tracemalloc_peak_bytes": 500,
            },
        },
    ]
    for run in runs:
        run["measurement_identity"] = _complete_identity(
            module, run["measurement_identity"]
        )
        run["comparison_identity"] = module._comparison_identity(
            run["measurement_identity"]
        )

    summary = module.summarize_runs(runs)

    local = summary["scenarios"]["local_status_tool_turn"]
    assert local["count"] == 2
    assert local["wall_time_ms"]["median"] == 15
    assert local["wall_time_ns"]["count"] == 0
    assert local["prompt_tokens_estimated"]["max"] == 7
    assert local["segment_family_metrics"][0]["segment_family"] == "replay_user"
    assert local["segment_family_metrics"][0]["prompt_bytes"] == 40
    assert local["tool_family_metrics"][0]["tool_family"] == "local_status"
    assert local["tool_family_metrics"][0]["tool_schema_bytes"] == 120
    assert local["sample_artifacts"] == ["/tmp/run-1.json", "/tmp/run-2.json"]
    assert local["measurement_identity"]["command"] == "local_status_fixture"
    assert local["warn_only"] is False
    assert summary["scenarios"]["provider_turn"]["warn_only"] is True


def test_canonical_help_command_uses_root_help_and_explicit_data_root(
    tmp_path: Path,
) -> None:
    module = _load_module()
    options = module.RunOptions(
        workspace_root=Path(__file__).resolve().parents[3],
        output_root=tmp_path,
        python=Path(sys.executable),
        runs=1,
        timeout_seconds=5,
        include_importtime=False,
        profile=False,
    )
    data_root = tmp_path / "runtime-homes" / "warm" / ".openminion"

    command = module._canonical_help_command(options, data_root=data_root)

    assert command[-1:] == ["--help"]
    assert "focus" not in command
    assert command[command.index("--data-root") + 1] == str(data_root)


def test_comparison_allows_source_and_workspace_identity_changes() -> None:
    module = _load_module()
    current_identity = _bound_identity(
        module,
        scenario_id="cold_focus_startup",
        command="python -m openminion --data-root /tmp/a --help",
        measured_boundary=module.SUT_BOUNDARY_SUBPROCESS,
        fixture_revision=module.STARTUP_FIXTURE_REVISION,
    )
    baseline_identity = dict(current_identity)
    baseline_identity["git_head"] = "f" * 40
    baseline_identity["dirty_tree_fingerprint"] = "0" * 64
    baseline_runtime = dict(baseline_identity["runtime_config"])
    baseline_runtime["workspace_root"] = "/different/workspace"
    baseline_runtime["data_root"] = "/different/data-root"
    baseline_identity["runtime_config"] = baseline_runtime
    current = {
        "count": 20,
        "ok_count": 20,
        "wall_time_ms": {"p95": 100, "coefficient_of_variation": 0.0},
        "measurement_identity": current_identity,
        "comparison_identity": module._comparison_identity(current_identity),
    }
    baseline = {
        "artifact_schema_version": module.ARTIFACT_SCHEMA_VERSION,
        "scenarios": {
            "cold_focus_startup": {
                "count": 20,
                "ok_count": 20,
                "wall_time_ms": {"p95": 100, "coefficient_of_variation": 0.0},
                "measurement_identity": baseline_identity,
                "comparison_identity": module._comparison_identity(baseline_identity),
            }
        },
    }

    result = module._threshold_result(
        current=current,
        baseline=baseline,
        scenario_id="cold_focus_startup",
        threshold_mode="hard",
    )

    assert result["status"] == "pass"


def test_comparison_rejects_semantic_and_environment_mismatches() -> None:
    module = _load_module()
    identity = _bound_identity(
        module,
        scenario_id="cold_focus_startup",
        command="python -m openminion --data-root /tmp/a --help",
        measured_boundary=module.SUT_BOUNDARY_SUBPROCESS,
        fixture_revision=module.STARTUP_FIXTURE_REVISION,
    )
    current = module._comparison_identity(identity)
    for key, changed in (
        ("artifact_schema_version", "pomv2.performance.v5"),
        ("fixture_revision", "changed-fixture"),
        ("measured_boundary", module.SUT_BOUNDARY_IN_PROCESS),
        ("python_implementation", "PyPy"),
        ("python_version", "0.0.0"),
        ("python_build", ["changed", "build"]),
        ("resolved_python_executable", "/different/python"),
        ("runner_source_sha256", "0" * 64),
        ("host_runtime_hash", "1" * 64),
        ("runtime_dependency_hash", "2" * 64),
        ("effective_sys_path_shape", ["<SUT_REPO>"]),
        ("inherited_pythonpath_shape", ["<SUT_SRC>"]),
        ("bytecode_cache_posture", {"pycache_posture": "interpreter_default"}),
        ("provider_posture", "provider"),
        ("model_posture", "model"),
        ("process_posture", "warm"),
        ("include_importtime", True),
        ("profile", True),
        ("warmup_runs", 2),
        ("scenario_config", {"timeout_seconds": 99}),
    ):
        baseline = dict(current)
        baseline[key] = changed
        assert key in module._comparison_identity_errors(current, baseline)


def test_comparison_accepts_empty_inherited_pythonpath_shape() -> None:
    module = _load_module()
    identity = _bound_identity(
        module,
        scenario_id="repeated_local_turns",
        command="in_process:repeated_local_turns",
        measured_boundary=module.SUT_BOUNDARY_IN_PROCESS,
        fixture_revision="adhoc",
    )
    current = module._comparison_identity(identity)
    current["inherited_pythonpath_shape"] = []

    assert module._comparison_identity_errors(current, dict(current)) == []

    missing = dict(current)
    del missing["inherited_pythonpath_shape"]
    assert "inherited_pythonpath_shape" in module._comparison_identity_errors(
        current, missing
    )


def test_dirty_fingerprint_includes_nested_untracked_file_bytes(
    tmp_path: Path,
) -> None:
    module = _load_module()
    repo = tmp_path / "openminion"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    nested = repo / "scratch" / "nested.txt"
    nested.parent.mkdir()
    nested.write_text("first", encoding="utf-8")

    first = module._dirty_worktree_fingerprint(tmp_path)
    nested.write_text("second", encoding="utf-8")
    second = module._dirty_worktree_fingerprint(tmp_path)

    assert first != second


def test_requested_baseline_must_be_readable_and_well_formed(tmp_path: Path) -> None:
    module = _load_module()
    missing = tmp_path / "missing.json"
    malformed = tmp_path / "malformed.json"
    malformed.write_text("[]", encoding="utf-8")

    for path in (missing, malformed):
        try:
            module._load_comparison_baseline(path)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid comparison baseline accepted: {path}")


def test_comparison_rejects_v3_artifact_for_v4_thresholds() -> None:
    module = _load_module()
    current_identity = _bound_identity(
        module,
        scenario_id="simple_turn",
        command="replay_fixture:simple_turn",
        measured_boundary=module.SUT_BOUNDARY_IN_PROCESS,
        fixture_revision="fixture-v1",
    )
    result = module._threshold_result(
        current={
            "count": 20,
            "ok_count": 20,
            "wall_time_ns": {"p95": 10, "coefficient_of_variation": 0.0},
            "measurement_identity": current_identity,
            "comparison_identity": module._comparison_identity(current_identity),
        },
        baseline={
            "artifact_schema_version": "pomv2.performance.v3",
            "scenarios": {
                "simple_turn": {
                    "count": 20,
                    "ok_count": 20,
                    "wall_time_ns": {
                        "p95": 10,
                        "coefficient_of_variation": 0.0,
                    },
                }
            },
        },
        scenario_id="simple_turn",
        threshold_mode="hard",
    )

    assert result["status"] == "ineligible"
    assert result["reason"] == "artifact schema version mismatch; v3 is display-only"
    assert result["identity_errors"] == ["artifact_schema_version"]


def test_comparison_rejects_quality_failure_before_timing_gain() -> None:
    module = _load_module()
    identity = _bound_identity(
        module,
        scenario_id="simple_turn",
        command="replay_fixture:simple_turn",
        measured_boundary=module.SUT_BOUNDARY_IN_PROCESS,
        fixture_revision="fixture-v1",
    )
    current = {
        "count": 20,
        "ok_count": 19,
        "wall_time_ns": {"p95": 1, "coefficient_of_variation": 0.0},
        "measurement_identity": identity,
        "comparison_identity": module._comparison_identity(identity),
    }
    baseline = {
        "artifact_schema_version": module.ARTIFACT_SCHEMA_VERSION,
        "scenarios": {
            "simple_turn": {
                "count": 20,
                "ok_count": 20,
                "wall_time_ns": {"p95": 10, "coefficient_of_variation": 0.0},
                "measurement_identity": identity,
                "comparison_identity": module._comparison_identity(identity),
            }
        },
    }

    result = module._threshold_result(
        current=current,
        baseline=baseline,
        scenario_id="simple_turn",
        threshold_mode="warn",
    )

    assert result["status"] == "fail"
    assert result["reason"] == "quality fixture failure"


def test_comparison_uses_twenty_sample_nanosecond_p95_and_variance_rule() -> None:
    module = _load_module()
    identity = _bound_identity(
        module,
        scenario_id="simple_turn",
        command="replay_fixture:simple_turn",
        measured_boundary=module.SUT_BOUNDARY_IN_PROCESS,
        fixture_revision="fixture-v1",
    )
    baseline_scenario = {
        "count": 20,
        "ok_count": 20,
        "wall_time_ns": {"p95": 100, "coefficient_of_variation": 0.10},
        "measurement_identity": identity,
        "comparison_identity": module._comparison_identity(identity),
    }
    baseline = {
        "artifact_schema_version": module.ARTIFACT_SCHEMA_VERSION,
        "scenarios": {"simple_turn": baseline_scenario},
    }
    current = {
        "count": 20,
        "ok_count": 20,
        "wall_time_ns": {"p95": 111, "coefficient_of_variation": 0.10},
        "measurement_identity": identity,
        "comparison_identity": module._comparison_identity(identity),
    }

    result = module._threshold_result(
        current=current,
        baseline=baseline,
        scenario_id="simple_turn",
        threshold_mode="hard",
    )
    assert result["status"] == "fail"
    assert result["regression_ratio"] == 1.10

    current["wall_time_ns"] = {"p95": 90, "coefficient_of_variation": 0.21}
    result = module._threshold_result(
        current=current,
        baseline=baseline,
        scenario_id="simple_turn",
        threshold_mode="hard",
    )
    assert result["status"] == "ineligible"
    assert "variance" in result["reason"]


def test_summary_rejects_mixed_sample_identities() -> None:
    module = _load_module()
    first_identity = _bound_identity(
        module,
        scenario_id="simple_turn",
        command="replay_fixture:simple_turn",
        measured_boundary=module.SUT_BOUNDARY_IN_PROCESS,
        fixture_revision="fixture-v1",
    )
    second_identity = dict(first_identity)
    second_identity["git_head"] = "d" * 40
    runs = [
        {
            "scenario_id": "simple_turn",
            "sample_index": sample_index,
            "ok": True,
            "provider_variance_class": module.LOCAL_VARIANCE,
            "measurement_identity": identity,
            "comparison_identity": module._comparison_identity(identity),
            "metrics": {"wall_time_ns": 10},
        }
        for sample_index, identity in enumerate((first_identity, second_identity))
    ]

    summary = module.summarize_runs(runs, threshold_mode="off")

    scenario = summary["scenarios"]["simple_turn"]
    assert scenario["identity_incompatibilities"] == [
        {"sample_index": 1, "identity_errors": ["git_head"]}
    ]
    assert scenario["threshold_result"]["reason"] == "mixed sample identities"
    assert module._invalid_sample_failures(summary) == ["simple_turn"]


def test_hard_gate_failures_only_return_failed_scenarios_in_hard_mode() -> None:
    module = _load_module()
    summary = {
        "threshold_mode": "hard",
        "scenarios": {
            "stable": {"threshold_result": {"status": "pass"}},
            "provider": {"threshold_result": {"status": "warn"}},
            "local_regression": {"threshold_result": {"status": "fail"}},
            "not_comparable": {"threshold_result": {"status": "ineligible"}},
        },
    }

    assert module._hard_gate_failures(summary) == ["local_regression"]
    summary["threshold_mode"] = "warn"
    assert module._hard_gate_failures(summary) == []


def test_main_rejects_invalid_samples_when_thresholds_are_off(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_module()
    monkeypatch.setattr(
        module,
        "run_baseline",
        lambda _options, _scenarios: {
            "scenario_count": 1,
            "run_count": 1,
            "threshold_mode": "off",
            "scenarios": {
                "repeated_local_turns": {
                    "count": 1,
                    "ok_count": 0,
                    "threshold_result": {"status": "not_applicable"},
                }
            },
        },
    )

    exit_code = module.main(
        [
            "--scenarios",
            "repeated_local_turns",
            "--threshold-mode",
            "off",
            "--output-root",
            str(tmp_path),
        ]
    )

    assert exit_code == 1


def test_main_preserves_selected_virtualenv_python(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_module()
    selected_python = tmp_path / "python"
    selected_python.symlink_to(Path(sys.executable))
    captured: dict[str, Path] = {}

    def run_baseline(options, _scenarios):
        captured["python"] = options.python
        return {
            "scenario_count": 0,
            "run_count": 0,
            "threshold_mode": "off",
            "scenarios": {},
        }

    monkeypatch.setattr(module, "run_baseline", run_baseline)

    exit_code = module.main(
        [
            "--scenarios",
            "simple_turn",
            "--threshold-mode",
            "off",
            "--output-root",
            str(tmp_path / "artifacts"),
            "--python",
            str(selected_python),
        ]
    )

    assert exit_code == 0
    assert captured["python"] == selected_python.absolute()


def test_local_status_scenario_records_required_metric_keys() -> None:
    module = _load_module()

    run = module.run_scenario(
        "local_status_tool_turn",
        module.RunOptions(
            workspace_root=Path(__file__).resolve().parents[3],
            output_root=Path("/tmp/unused-pbhg-test"),
            python=Path(sys.executable),
            runs=1,
            timeout_seconds=5,
            include_importtime=False,
            profile=False,
        ),
    )

    assert run.ok is True
    assert run.provider_variance_class == module.LOCAL_VARIANCE
    for key in (
        "wall_time_ms",
        "wall_time_ns",
        "rss_start_bytes",
        "rss_end_bytes",
        "rss_delta_bytes",
        "tracemalloc_current_bytes",
        "tracemalloc_peak_bytes",
        "tracemalloc_overhead_bytes",
        "current_rss_bytes",
        "max_rss_bytes",
        "process_tree_current_rss_bytes",
        "thread_count",
        "async_task_count",
        "child_process_count",
        "file_descriptor_count",
        "open_file_count",
        "network_connection_count",
        "queue_depths",
        "cache_cardinalities",
        "phase",
        "sample_index",
        "elapsed_ms",
        "completed_turn_count",
        "terminal_fact",
        "measured_process_id",
        "children_included",
        "external_services_included",
        "process_tree_members",
        "availability_reasons",
        "tool_call_count",
    ):
        assert key in run.metrics
    assert run.metrics["tool_call_count"] == 1
    assert run.metrics["wall_time_ns"] >= 0
    assert run.metrics["measurement_resolution"] == "perf_counter_ns"
    assert "local_status_collect_ns" in run.metrics["phase_timings_ns"]
    assert run.metrics["rss_end_bytes"] == run.metrics["current_rss_bytes"]
    assert run.metrics["measured_process_id"] == os.getpid()
    assert run.metrics["terminal_fact"] is None
    assert run.metrics["availability_reasons"]["terminal_fact"] == "not_applicable"
    assert run.measurement_identity["artifact_schema_version"] == (
        "pomv2.performance.v4"
    )
    for key in (
        "git_head",
        "dirty_tree_fingerprint",
        "runner_source_sha256",
        "config_hash",
        "provider_posture",
        "model_posture",
    ):
        assert key in run.measurement_identity


def test_process_tree_bounds_members_without_publishing_partial_rss(
    monkeypatch,
) -> None:
    module = _load_module()

    class FakePsutilError(Exception):
        pass

    class FakeChild:
        def __init__(self, pid: int, *, readable: bool = True) -> None:
            self.pid = pid
            self._readable = readable

        def memory_info(self):
            if not self._readable:
                raise FakePsutilError
            return types.SimpleNamespace(rss=10)

    class FakeProcess:
        def __init__(self, _pid: int) -> None:
            self.pid = _pid

        def memory_info(self):
            return types.SimpleNamespace(rss=100)

        def children(self, *, recursive: bool):
            assert recursive is True
            return [FakeChild(index + 2) for index in range(65)] + [
                FakeChild(67, readable=False)
            ]

    monkeypatch.setitem(
        sys.modules,
        "psutil",
        types.SimpleNamespace(Process=FakeProcess, Error=FakePsutilError),
    )

    metrics = module._process_rss_metrics(1)

    assert metrics["child_process_count"] == 66
    assert len(metrics["process_tree_members"]) == module.PROCESS_TREE_MEMBER_LIMIT
    assert metrics["process_tree_current_rss_bytes"] is None
    assert metrics["availability_reasons"]["process_tree_members"] == (
        "member_limit_reached"
    )
    assert metrics["availability_reasons"]["process_tree_current_rss_bytes"] == (
        "descendant_rss_unavailable"
    )


def test_finish_inventory_does_not_inflate_in_process_tracemalloc_peak(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_module()
    original_process_metrics = module._process_metrics
    process_metric_calls = 0
    retained_finish_allocation: list[bytearray] = []

    def allocating_finish_inventory(process_id=None):
        nonlocal process_metric_calls
        process_metric_calls += 1
        if process_metric_calls == 2:
            retained_finish_allocation.append(bytearray(2_000_000))
        return original_process_metrics(process_id)

    monkeypatch.setattr(module, "_process_metrics", allocating_finish_inventory)

    run = module.run_scenario(
        "local_status_tool_turn",
        module.RunOptions(
            workspace_root=Path(__file__).resolve().parents[3],
            output_root=tmp_path,
            python=Path(sys.executable),
            runs=1,
            timeout_seconds=5,
            include_importtime=False,
            profile=False,
        ),
    )

    assert process_metric_calls == 2
    assert retained_finish_allocation
    assert run.metrics["tracemalloc_peak_bytes"] < 1_000_000


def test_focus_startup_samples_the_subprocess(tmp_path: Path) -> None:
    module = _load_module()

    run = module.run_scenario(
        "warm_focus_startup",
        module.RunOptions(
            workspace_root=Path(__file__).resolve().parents[3],
            output_root=tmp_path,
            python=Path(sys.executable),
            runs=1,
            timeout_seconds=15,
            include_importtime=False,
            profile=False,
        ),
    )

    assert run.ok is True, run.error
    assert run.metrics["phase"] == "startup"
    assert run.metrics["measured_process_id"] != os.getpid()
    assert run.metrics["process_sample_count"] > 0
    assert run.metrics["current_rss_bytes"] > 0
    assert (
        run.metrics["sampled_peak_current_rss_bytes"]
        >= (run.metrics["current_rss_bytes"])
    )
    assert run.metrics["max_rss_bytes"] is None
    assert run.metrics["availability_reasons"]["max_rss_bytes"] == "not_supported"
    assert run.metrics["tracemalloc_current_bytes"] is None
    assert run.metrics["harness_tracemalloc_current_bytes"] > 0
    assert run.metrics["availability_reasons"]["tracemalloc_current_bytes"] == (
        "not_supported_for_subprocess"
    )


def test_startup_loop_uses_one_full_resource_inventory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_module()
    options = module.RunOptions(
        workspace_root=Path(__file__).resolve().parents[3],
        output_root=tmp_path,
        python=Path(sys.executable),
        runs=1,
        timeout_seconds=5,
        include_importtime=False,
        profile=False,
    )
    full_calls = 0
    tree_calls = 0
    current_calls = 0
    full_metrics = module._process_metrics
    tree_metrics = module._process_rss_metrics
    current_rss = module._current_rss_bytes

    def count_full(process_id=None):
        nonlocal full_calls
        full_calls += 1
        return full_metrics(process_id)

    def count_tree(process_id=None):
        nonlocal tree_calls
        tree_calls += 1
        return tree_metrics(process_id)

    def count_current(process_id=None):
        nonlocal current_calls
        current_calls += 1
        return current_rss(process_id)

    monkeypatch.setattr(module, "_process_metrics", count_full)
    monkeypatch.setattr(module, "_process_rss_metrics", count_tree)
    monkeypatch.setattr(module, "_current_rss_bytes", count_current)

    completed, metrics = module._run_subprocess_measured(
        [sys.executable, "-c", "import time; time.sleep(0.03)"],
        options=options,
        data_root=tmp_path,
    )

    assert completed.returncode == 0
    assert metrics["process_sample_count"] > 0
    assert full_calls == 1
    assert tree_calls == 1
    assert current_calls > 1


def test_deterministic_full_turn_records_complete_turn_metrics(tmp_path: Path) -> None:
    module = _load_module()

    run = module.run_scenario(
        "deterministic_full_turn",
        module.RunOptions(
            workspace_root=Path(__file__).resolve().parents[3],
            output_root=tmp_path,
            python=Path(sys.executable),
            runs=1,
            timeout_seconds=5,
            include_importtime=False,
            profile=False,
        ),
    )

    assert run.ok is True
    assert run.provider_profile == "stub"
    assert run.measurement_identity["fixture_revision"] == "deterministic-full-turn-v1"
    assert run.metrics["model_call_count"] == 1
    assert run.metrics["provider_call_purposes"] == ["entry"]
    assert run.metrics["provider_attempts"] == [
        {
            "logical_call_id": "deterministic-full-turn-entry",
            "semantic_purpose": "entry",
            "attempt": 1,
            "provider": "stub",
            "model": "stub-model",
            "route_posture": "primary",
            "attempt_posture": "initial",
            "latency_ms": run.metrics["provider_round_trip_ms"],
            "outcome": "ok",
        }
    ]
    assert run.metrics["selector_latency_ms"] == 0
    assert run.metrics["session_compaction_policy"] == "noop"
    assert run.metrics["memory_followup_pending_count"] == 0
    assert run.metrics["tool_call_count"] == 1
    assert run.metrics["storage_operation_count"] == 6
    assert run.metrics["render_chunk_count"] == 2
    assert run.metrics["telemetry_queue_depth"] == 0
    assert run.metrics["retained_messages"] == 2
    for phase in (
        "runtime_ingress_ns",
        "context_assembly_ns",
        "provider_stub_round_trip_ns",
        "tool_execution_ns",
        "telemetry_persist_ns",
        "terminal_delivery_ns",
    ):
        assert run.metrics["phase_timings_ns"][phase] >= 0


def test_instrumentation_overhead_aa_records_20_by_20_comparison(
    tmp_path: Path,
) -> None:
    module = _load_module()

    run = module.run_scenario(
        "instrumentation_overhead_aa",
        module.RunOptions(
            workspace_root=Path(__file__).resolve().parents[3],
            output_root=tmp_path,
            python=Path(sys.executable),
            runs=1,
            timeout_seconds=5,
            include_importtime=False,
            profile=False,
        ),
    )

    assert run.ok is True
    assert run.metrics["instrumentation_aa_enabled_samples"] == 20
    assert run.metrics["instrumentation_aa_disabled_samples"] == 20
    assert run.metrics["instrumentation_overhead_median_ms"] >= 0
    assert run.metrics["provider_calls_allowed"] is False
    assert run.metrics["storage_mutations_allowed"] is False


def test_tcpl_matrix_scenarios_record_route_branch_inputs(tmp_path: Path) -> None:
    module = _load_module()
    options = module.RunOptions(
        workspace_root=Path(__file__).resolve().parents[3],
        output_root=tmp_path,
        python=Path(sys.executable),
        runs=1,
        timeout_seconds=5,
        include_importtime=False,
        profile=False,
    )

    direct = module.run_scenario("tcpl_selector_direct_route", options)
    retrieval = module.run_scenario("tcpl_selector_retrieval_route", options)
    llm = module.run_scenario("tcpl_selector_llm_route", options)
    compaction = module.run_scenario("tcpl_compaction_threshold_crossing", options)
    pending = module.run_scenario("tcpl_memory_followup_pending", options)
    direct_tool = module.run_scenario("tcpl_branch_direct_tool", options)
    repair = module.run_scenario("tcpl_branch_final_answer_repair", options)
    retry = module.run_scenario("tcpl_provider_retry_fallback", options)

    assert direct.metrics["skill_selection_route"] == "direct_no_catalog"
    assert direct.metrics["selector_candidate_count"] == 0
    assert retrieval.metrics["skill_selection_route"] == "retrieval"
    assert retrieval.metrics["selector_candidate_count"] == 3
    assert llm.metrics["provider_call_purposes"] == ["skill_selection", "entry"]
    assert llm.metrics["selector_candidate_count"] == 24
    assert compaction.metrics["session_compaction_policy"] == "threshold_crossing"
    assert compaction.metrics["session_compaction_ms"] >= 10
    assert pending.metrics["memory_projection_posture"] == "pending"
    assert pending.metrics["memory_summary_structure_ms"] > 0
    assert direct_tool.metrics["provider_call_purposes"] == ["entry", "act", "judge"]
    assert repair.metrics["closure_branch"] == "final_answer_repair"
    assert retry.metrics["provider_call_purposes"] == ["entry"]
    assert [item["route_posture"] for item in retry.metrics["provider_attempts"]] == [
        "primary",
        "primary",
        "fallback",
    ]
    assert [item["attempt_posture"] for item in retry.metrics["provider_attempts"]] == [
        "initial",
        "retry",
        "initial",
    ]


def test_provider_payload_serialization_reuses_wire_body() -> None:
    module = _load_module()

    run = module.run_scenario(
        "provider_payload_serialization",
        module.RunOptions(
            workspace_root=Path(__file__).resolve().parents[3],
            output_root=Path("/tmp/unused-payload-test"),
            python=Path(sys.executable),
            runs=1,
            timeout_seconds=5,
            include_importtime=False,
            profile=False,
        ),
    )

    assert run.ok is True
    assert run.metrics["duplicate_serialization_count"] == 0
    assert run.metrics["request_body_reused_for_trace"] is True
    assert run.metrics["provider_payload_bytes"] > 0


def test_terminal_render_burst_coalesces_after_first_text() -> None:
    module = _load_module()

    run = module.run_scenario(
        "terminal_render_burst",
        module.RunOptions(
            workspace_root=Path(__file__).resolve().parents[3],
            output_root=Path("/tmp/unused-render-test"),
            python=Path(sys.executable),
            runs=1,
            timeout_seconds=5,
            include_importtime=False,
            profile=False,
        ),
    )

    assert run.ok is True
    assert run.metrics["first_refresh_after_chars"] == 1
    assert run.metrics["render_refresh_count"] < run.metrics["render_chunk_count"]
    assert run.metrics["coalesced_refresh_count"] > 0


def test_telemetry_export_queue_flushes_noncritical_events() -> None:
    module = _load_module()

    run = module.run_scenario(
        "telemetry_export_queue",
        module.RunOptions(
            workspace_root=Path(__file__).resolve().parents[3],
            output_root=Path("/tmp/unused-telemetry-queue-test"),
            python=Path(sys.executable),
            runs=1,
            timeout_seconds=5,
            include_importtime=False,
            profile=False,
        ),
    )

    assert run.ok is True
    assert run.metrics["telemetry_events_enqueued"] == 100
    assert run.metrics["telemetry_events_exported"] == 100
    assert run.metrics["telemetry_queue_depth"] == 0
    assert run.metrics["queue_depths"] == {"telemetry.noncritical_export": 0}
    assert run.metrics["telemetry_queue_drops"] == 0
    assert run.metrics["telemetry_queue_flush_failures"] == 0


def test_transcript_retention_growth_caps_working_set() -> None:
    module = _load_module()

    run = module.run_scenario(
        "transcript_retention_growth",
        module.RunOptions(
            workspace_root=Path(__file__).resolve().parents[3],
            output_root=Path("/tmp/unused-transcript-test"),
            python=Path(sys.executable),
            runs=1,
            timeout_seconds=5,
            include_importtime=False,
            profile=False,
        ),
    )

    assert run.ok is True
    assert run.metrics["retained_messages"] == run.metrics["retention_limit"]
    assert run.metrics["transcript_messages_seen"] > run.metrics["retained_messages"]
    assert run.metrics["copy_last_ok"] is True


def test_rss_growth_metrics_remain_available_when_current_rss_is_not(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_module()
    monkeypatch.setattr(module, "_current_rss_bytes", lambda process_id=None: None)
    options = module.RunOptions(
        workspace_root=Path(__file__).resolve().parents[3],
        output_root=tmp_path,
        python=Path(sys.executable),
        runs=1,
        timeout_seconds=5,
        include_importtime=False,
        profile=False,
    )

    repeated = module.run_scenario("repeated_local_turns", options)
    transcript = module.run_scenario("transcript_retention_growth", options)

    assert repeated.ok is True
    assert repeated.metrics["rss_growth_bytes"] is None
    assert repeated.metrics["availability_reasons"]["rss_growth_bytes"] == (
        "current_rss_unavailable"
    )
    assert transcript.ok is True
    assert transcript.metrics["rss_growth_per_message_bytes"] is None


def test_remaining_performance_rows_record_decision_evidence(tmp_path: Path) -> None:
    module = _load_module()
    scenario_expectations = {
        "required_lane_branch_characterization": (
            "required_lane_branch_count",
            lambda metrics: metrics["provider_call_reduction_count"] == 0,
        ),
        "typeadapter_validation_probe": (
            "typeadapter_reuse_ratio",
            lambda metrics: metrics["typeadapter_new_global_cache_added"] is False,
        ),
        "metadata_json_churn": (
            "metadata_json_field_count",
            lambda metrics: (
                metrics["required_lane_metadata_contract_preserved"] is True
            ),
        ),
        "provider_connection_reuse_decision": (
            "provider_connection_dependency_decision",
            lambda metrics: metrics["provider_connection_reuse_change_count"] == 0,
        ),
        "storage_wal_index_matrix": (
            "storage_journal_mode",
            lambda metrics: metrics["storage_query_rows"] == 1,
        ),
        "retrieval_breakdown_profile": (
            "retrieval_candidate_count",
            lambda metrics: metrics["retrieval_source_grounding_ok"] is True,
        ),
    }

    for scenario_id, (metric_key, expectation) in scenario_expectations.items():
        run = module.run_scenario(
            scenario_id,
            module.RunOptions(
                workspace_root=Path(__file__).resolve().parents[3],
                output_root=tmp_path / scenario_id,
                python=Path(sys.executable),
                runs=1,
                timeout_seconds=5,
                include_importtime=False,
                profile=False,
            ),
        )

        assert run.ok is True, run.error
        assert metric_key in run.metrics
        assert expectation(run.metrics)


def test_run_baseline_writes_artifacts(tmp_path: Path) -> None:
    module = _load_module()
    options = module.RunOptions(
        workspace_root=Path(__file__).resolve().parents[3],
        output_root=tmp_path,
        python=Path(sys.executable),
        runs=1,
        timeout_seconds=5,
        include_importtime=False,
        profile=False,
    )

    summary = module.run_baseline(options, ["local_status_tool_turn"])

    assert summary["run_count"] == 1
    assert (tmp_path / "summary.json").is_file()
    assert (tmp_path / "summary.md").is_file()
    run_files = list((tmp_path / "runs").glob("*.json"))
    assert len(run_files) == 1
    payload = json.loads(run_files[0].read_text(encoding="utf-8"))
    assert payload["artifact_schema_version"] == module.ARTIFACT_SCHEMA_VERSION
    assert payload["scenario_id"] == "local_status_tool_turn"
    assert payload["artifact_path"] == str(run_files[0])
    assert payload["measurement_identity"]["measured_boundary"] == (
        module.SUT_BOUNDARY_IN_PROCESS
    )
    assert payload["measurement_identity"]["git_head"] not in {
        "",
        "unknown",
        "unavailable",
    }
    assert payload["measurement_identity"]["dirty_tree_fingerprint"] != ("unavailable")
    assert payload["measurement_identity"]["runner_source_sha256"] != ("unavailable")
    assert payload["measurement_identity"]["config_hash"] == module._stable_json_hash(
        payload["measurement_identity"]["runtime_config"]
    )
    assert isinstance(payload["wall_ns"], int)
    assert payload["phase_timings_ns"]["local_status_collect_ns"] >= 0
    for artifact_name in (
        "manifest.json",
        "samples.jsonl",
        "summary.json",
        "quality.json",
        "decision.md",
    ):
        assert (tmp_path / artifact_name).is_file()
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["artifact_schema_version"] == module.TCPL_ARTIFACT_SCHEMA_VERSION
    assert manifest["lane"] == "TCPL"
    samples = [
        json.loads(line)
        for line in (tmp_path / "samples.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert len(samples) == 1
    assert samples[0]["scenario_id"] == "local_status_tool_turn"
    assert samples[0]["comparable_identity"]["candidate_posture"] == "baseline_current"
    assert samples[0]["shadow_decisions"]["side_effects_allowed"] is False
    assert samples[0]["shadow_decisions"]["provider_calls_allowed"] is False
    assert samples[0]["shadow_decisions"]["storage_mutations_allowed"] is False
    quality = json.loads((tmp_path / "quality.json").read_text(encoding="utf-8"))
    assert quality["scenarios"]["local_status_tool_turn"]["status"] == "pass"
    assert quality["tcpl_coverage_status"] == "incomplete"
    assert quality["coverage_gap_count"] > 0
    assert "`hold`" in (tmp_path / "decision.md").read_text(encoding="utf-8")
    assert "Missing TCPL-00 Coverage" in (tmp_path / "decision.md").read_text(
        encoding="utf-8"
    )


def test_run_baseline_records_metric_sample_indices(tmp_path: Path) -> None:
    module = _load_module()
    options = module.RunOptions(
        workspace_root=Path(__file__).resolve().parents[3],
        output_root=tmp_path,
        python=Path(sys.executable),
        runs=3,
        timeout_seconds=5,
        include_importtime=False,
        profile=False,
        threshold_mode="off",
    )

    summary = module.run_baseline(options, ["repeated_local_turns"])

    assert (
        summary["scenarios"]["repeated_local_turns"]["identity_incompatibilities"] == []
    )
    payloads = sorted(
        (
            json.loads(path.read_text(encoding="utf-8"))
            for path in (tmp_path / "runs").glob("*.json")
        ),
        key=lambda payload: payload["sample_index"],
    )
    assert [payload["sample_index"] for payload in payloads] == [0, 1, 2]
    assert [payload["metrics"]["sample_index"] for payload in payloads] == [0, 1, 2]


def test_run_baseline_rejects_source_drift_at_campaign_close(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_module()
    options = module.RunOptions(
        workspace_root=Path(__file__).resolve().parents[3],
        output_root=tmp_path,
        python=Path(sys.executable),
        runs=1,
        timeout_seconds=5,
        include_importtime=False,
        profile=False,
        threshold_mode="off",
    )
    identities = iter(
        (
            {
                "git_head": "a" * 40,
                "dirty_tree_fingerprint": "b" * 64,
                "dirty_worktree_summary": {"available": True},
                "runner_path": "/opt/owpr/performance_baseline.py",
                "runner_source_sha256": "c" * 64,
                "loaded_openminion_package_root": "/opt/owpr/openminion",
                "runtime_environment": _bound_identity(
                    module,
                    scenario_id="repeated_local_turns",
                    command="repeated_local_fixture:single_iteration_sample",
                    measured_boundary=module.SUT_BOUNDARY_IN_PROCESS,
                    fixture_revision="test",
                )["runtime_environment"],
            },
            {
                "git_head": "d" * 40,
                "dirty_tree_fingerprint": "b" * 64,
                "dirty_worktree_summary": {"available": True},
                "runner_path": "/opt/owpr/performance_baseline.py",
                "runner_source_sha256": "c" * 64,
                "loaded_openminion_package_root": "/opt/owpr/openminion",
                "runtime_environment": _bound_identity(
                    module,
                    scenario_id="repeated_local_turns",
                    command="repeated_local_fixture:single_iteration_sample",
                    measured_boundary=module.SUT_BOUNDARY_IN_PROCESS,
                    fixture_revision="test",
                )["runtime_environment"],
            },
        )
    )
    monkeypatch.setattr(
        module, "_campaign_source_identity", lambda _options: next(identities)
    )

    try:
        module.run_baseline(options, ["repeated_local_turns"])
    except RuntimeError as exc:
        assert "campaign source identity changed: git_head" in str(exc)
    else:
        raise AssertionError("source drift should fail the campaign")


def test_tcpl02_skill_entry_candidate_records_parity_and_rollback(
    tmp_path: Path,
) -> None:
    module = _load_module()
    options = module.RunOptions(
        workspace_root=Path(__file__).resolve().parents[3],
        output_root=tmp_path,
        python=Path(sys.executable),
        runs=1,
        timeout_seconds=5,
        include_importtime=False,
        profile=False,
    )

    module.run_baseline(
        options,
        [
            "tcpl_02_skill_llm_baseline",
            "tcpl_02_skill_entry_candidate",
            "tcpl_02_skill_entry_rollback",
        ],
    )

    samples = [
        json.loads(line)
        for line in (tmp_path / "samples.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    by_id = {sample["scenario_id"]: sample for sample in samples}
    baseline = by_id["tcpl_02_skill_llm_baseline"]
    candidate = by_id["tcpl_02_skill_entry_candidate"]
    rollback = by_id["tcpl_02_skill_entry_rollback"]

    assert baseline["provider_call_purposes"] == ["skill_selection", "entry"]
    assert candidate["provider_call_purposes"] == ["entry"]
    assert rollback["provider_call_purposes"] == ["skill_selection", "entry"]
    assert baseline["provider_call_count"] == 2
    assert candidate["provider_call_count"] == 1
    assert rollback["provider_call_count"] == 2
    assert baseline["selected_skill_ids"] == candidate["selected_skill_ids"]
    assert candidate["applied_skill_ids"] == baseline["applied_skill_ids"]
    assert candidate["quality_assertions"]["skill_selection_quality"] == "pass"
    assert candidate["comparable_identity"]["candidate_posture"] == "entry_opt_in"
    assert candidate["comparable_identity"]["rollback_posture"] == "llm"
    assert rollback["comparable_identity"]["candidate_posture"] == "rollback_llm"
    assert (
        baseline["shadow_decisions"]["skill_entry"]["candidate_decision"]
        == "entry_candidate_eligible"
    )
    assert (
        baseline["shadow_decisions"]["skill_entry"]["projected_avoided_provider_calls"]
        == 1
    )


def test_tcpl_remaining_rows_record_nochange_dispositions(tmp_path: Path) -> None:
    module = _load_module()
    options = module.RunOptions(
        workspace_root=Path(__file__).resolve().parents[3],
        output_root=tmp_path,
        python=Path(sys.executable),
        runs=1,
        timeout_seconds=5,
        include_importtime=False,
        profile=False,
    )

    module.run_baseline(
        options,
        [
            "tcpl_01_streaming_safety_nochange",
            "tcpl_03_memory_projection_defer",
            "tcpl_04_compaction_defer",
            "tcpl_05_delivery_fence_retain",
        ],
    )

    samples = [
        json.loads(line)
        for line in (tmp_path / "samples.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    by_id = {sample["scenario_id"]: sample for sample in samples}

    streaming = by_id["tcpl_01_streaming_safety_nochange"]
    assert streaming["provider_call_purposes"] == ["entry"]
    assert streaming["comparable_identity"]["candidate_posture"] == (
        "streaming_nochange"
    )
    assert streaming["quality_assertions"]["transcript_quality"] == "pass"

    memory = by_id["tcpl_03_memory_projection_defer"]
    assert memory["provider_call_purposes"] == ["summarize", "entry"]
    assert memory["memory_followup_pending_count"] == 2
    assert memory["quality_assertions"]["memory_quality"] == "pass"

    compaction = by_id["tcpl_04_compaction_defer"]
    assert compaction["provider_call_purposes"] == ["self_compaction", "entry"]
    assert compaction["session_compaction_policy"] == "threshold_crossing"
    assert (
        compaction["shadow_decisions"]["session_compaction"]["candidate_decision"]
        == "derived_projection_candidate"
    )

    delivery = by_id["tcpl_05_delivery_fence_retain"]
    assert delivery["provider_call_purposes"] == ["entry"]
    assert delivery["comparable_identity"]["candidate_posture"] == (
        "delivery_fence_retain"
    )
    assert delivery["quality_assertions"]["policy_quality"] == "pass"


def test_tcpl_sample_validation_rejects_missing_identity(tmp_path: Path) -> None:
    module = _load_module()
    options = module.RunOptions(
        workspace_root=Path(__file__).resolve().parents[3],
        output_root=tmp_path,
        python=Path(sys.executable),
        runs=1,
        timeout_seconds=5,
        include_importtime=False,
        profile=False,
    )

    try:
        module._tcpl_sample_from_artifact(
            {
                "scenario_id": "local_status_tool_turn",
                "metrics": {"wall_time_ms": 1, "wall_time_ns": 1},
                "ok": True,
            },
            options=options,
        )
    except ValueError as exc:
        assert "measurement identity" in str(exc)
    else:
        raise AssertionError("missing measurement identity should fail")


def test_tcpl_sample_validation_rejects_negative_timing() -> None:
    module = _load_module()
    sample = {
        "comparable_identity": {
            "git_revision": "rev",
            "dirty_tree_fingerprint": "fingerprint",
            "scenario_id": "scenario",
            "fixture_hash": "fixture",
            "provider": "none",
            "model": "unavailable",
            "config_hash": "config",
            "candidate_posture": "baseline_current",
            "rollback_posture": "baseline_current",
            "cold_warm": "not_applicable",
            "host_runtime_hash": "host",
        },
        "wall_time_ms": -1,
        "wall_time_ns": 1,
    }

    try:
        module._validate_tcpl_sample(sample)
    except ValueError as exc:
        assert "wall_time_ms" in str(exc)
    else:
        raise AssertionError("negative timing should fail")


def test_tcpl_sample_validation_rejects_provider_latency_misalignment() -> None:
    module = _load_module()
    sample = {
        "comparable_identity": {
            "git_revision": "rev",
            "dirty_tree_fingerprint": "fingerprint",
            "scenario_id": "scenario",
            "fixture_hash": "fixture",
            "provider": "stub",
            "model": "unavailable",
            "config_hash": "config",
            "candidate_posture": "baseline_current",
            "rollback_posture": "baseline_current",
            "cold_warm": "not_applicable",
            "host_runtime_hash": "host",
        },
        "wall_time_ms": 1,
        "wall_time_ns": 1,
        "provider_call_purposes": ["entry", "finalize"],
        "provider_call_latency_ms": [7],
    }

    try:
        module._validate_tcpl_sample(sample)
    except ValueError as exc:
        assert "align" in str(exc)
    else:
        raise AssertionError("purpose/latency drift should fail")


def test_tcpl_provider_purpose_remains_unavailable_when_not_reported() -> None:
    module = _load_module()

    metrics = {
        "model_call_count": 1,
        "phase_timings_ms": {"provider_stub_round_trip_ms": 7},
        "provider_round_trip_ms": 7,
    }

    assert module._provider_call_purposes(metrics) == ["unavailable"]
    assert module._provider_call_latencies(metrics) == [7]
    assert module._provider_attempts(metrics) == []


def test_tcpl_missing_coverage_reports_required_measurements() -> None:
    module = _load_module()

    missing = module._tcpl_missing_coverage(
        [
            {
                "selector_latency_ms": 1,
                "selector_token_count": 10,
                "provider_attempts": [],
                "transcript_persistence_ms": 2,
                "phase_timings_ms": {"terminal_delivery_ms": 1},
            }
        ]
    )

    assert "selector" not in missing
    assert "delivery" not in missing
    assert "provider_attempts" in missing
    assert "compaction" in missing
    assert "memory_followup" in missing
    assert missing["persistence"] == ["base_memory_persistence_ms|memory_write_ms"]


def test_tcpl_missing_coverage_accepts_current_phase_owner_aliases() -> None:
    module = _load_module()

    missing = module._tcpl_missing_coverage(
        [
            {
                "provider_attempts": [
                    {
                        "logical_call_id": "call-1",
                        "semantic_purpose": "entry",
                        "attempt": 1,
                        "provider": "stub",
                        "model": "stub-model",
                        "route_posture": "primary",
                        "attempt_posture": "initial",
                        "latency_ms": 0,
                        "outcome": "ok",
                    }
                ],
                "selector_latency_ms": 1,
                "selector_token_count": 10,
                "session_compaction_ms": 0,
                "session_compaction_policy": "noop",
                "memory_followup_flush_ms": 0,
                "memory_followup_pending_count": 0,
                "phase_timings_ms": {
                    "response_persistence_ms": 2,
                    "memory_write_ms": 3,
                    "run_record_finish_ms": 1,
                    "response_delivery_ms": 1,
                    "response_delivered_event_ms": 1,
                    "terminal_event_ms": 1,
                },
            }
        ]
    )

    assert missing == {}


def test_tcpl_sample_validation_rejects_incomplete_provider_attempt() -> None:
    module = _load_module()
    sample = {
        "comparable_identity": {
            "git_revision": "rev",
            "dirty_tree_fingerprint": "fingerprint",
            "scenario_id": "scenario",
            "fixture_hash": "fixture",
            "provider": "stub",
            "model": "unavailable",
            "config_hash": "config",
            "candidate_posture": "baseline_current",
            "rollback_posture": "baseline_current",
            "cold_warm": "not_applicable",
            "host_runtime_hash": "host",
        },
        "wall_time_ms": 1,
        "wall_time_ns": 1,
        "provider_attempts": [{"semantic_purpose": "entry"}],
    }

    try:
        module._validate_tcpl_sample(sample)
    except ValueError as exc:
        assert "provider_attempts missing logical_call_id" in str(exc)
    else:
        raise AssertionError("incomplete provider attempt should fail")


def test_tcpl_shadow_decisions_are_observation_only() -> None:
    module = _load_module()

    decisions = module._tcpl_shadow_decisions(
        {
            "selector_token_count": 50,
            "selector_candidate_count": 2,
            "skill_selection_strategy": "llm",
            "skill_selection_route": "retrieval",
            "session_compaction_ms": 17,
            "session_compaction_policy": "ordinary",
        }
    )

    assert decisions["mode"] == "observation_only"
    assert decisions["side_effects_allowed"] is False
    assert decisions["provider_calls_allowed"] is False
    assert decisions["storage_mutations_allowed"] is False
    assert decisions["derived_jobs_allowed"] is False
    assert decisions["delivery_changes_allowed"] is False
    assert decisions["skill_entry"]["current_decision"] == "llm"
    assert decisions["skill_entry"]["candidate_decision"] == "entry_candidate_eligible"
    assert decisions["session_compaction"]["candidate_decision"] == (
        "derived_projection_candidate"
    )


def test_omfla_persistent_api_turns_records_typed_windows(tmp_path: Path) -> None:
    module = _load_module()

    run = module._measure_persistent_api_turns(
        _omfla_options(module, tmp_path),
        warmup_turns=1,
        measured_turns=5,
        window_count=5,
    )

    assert run.ok is True
    assert run.metrics["completed_turn_count"] == 6
    assert len(run.metrics["steady_state_windows"]) == 5
    assert run.metrics["terminal_fact"]["terminal_event_state"] == "completed"
    assert run.metrics["post_warmup_pre_close_tracemalloc_diff"]
    assert run.metrics["post_warmup_post_close_tracemalloc_diff"]
    assert run.metrics["diagnostic_gc_collected_objects"] >= 0
    assert run.metrics["post_warmup_post_gc_tracemalloc_diff"]
    assert run.metrics["phase"] == "post_diagnostic_gc"
    for key in (
        "post_warmup_post_close_event_loop_diff",
        "post_warmup_post_gc_event_loop_diff",
    ):
        assert set(run.metrics[key]) == {"size_diff_bytes", "count_diff"}
        assert isinstance(run.metrics[key]["size_diff_bytes"], int)
        assert isinstance(run.metrics[key]["count_diff"], int)
    for window in run.metrics["steady_state_windows"]:
        cardinalities = window["cache_cardinalities"]
        assert (
            cardinalities["contextctl_pack_cache"]
            <= cardinalities["contextctl_manifest_index"]
            <= cardinalities["contextctl_pack_cache"] + 1
        )
        assert cardinalities["contextctl_latest_sessions"] == 1
    assert run.metrics["close_sample"]["cache_cardinalities"] == {
        "contextctl_pack_cache": 0,
        "contextctl_manifest_index": 0,
        "contextctl_latest_sessions": 0,
    }
    assert run.metrics["close_sample"]["phase"] == "normal_close"
    assert run.metrics["post_gc_sample"]["cache_cardinalities"] == {
        "contextctl_pack_cache": 0,
        "contextctl_manifest_index": 0,
        "contextctl_latest_sessions": 0,
    }
    assert run.metrics["post_gc_sample"]["phase"] == "post_diagnostic_gc"


def test_omfla_persistent_focus_turns_uses_persisted_completion(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_module()
    from tests.e2e.cli.focus.harness import FocusProbe

    def reject_screen_owned_completion(*_args, **_kwargs):
        raise AssertionError("FocusProbe.run_turn must not own OMFLA completion")

    monkeypatch.setattr(FocusProbe, "run_turn", reject_screen_owned_completion)

    run = module._measure_persistent_focus_turns(
        _omfla_options(module, tmp_path),
        warmup_turns=1,
        measured_turns=1,
        window_count=1,
    )

    assert run.ok is True
    assert run.metrics["completed_turn_count"] == 2
    assert run.metrics["terminal_fact"]["terminal_event_state"] == "completed"
    assert run.metrics["focus_child_alive_after_close"] is False
    assert run.metrics["measured_process_id"] != os.getpid()


def test_omfla_session_churn_records_owner_cardinality(tmp_path: Path) -> None:
    module = _load_module()

    run = module._measure_session_cache_churn(
        _omfla_options(module, tmp_path),
        warmup_turns=1,
        session_count=5,
        window_count=5,
    )

    assert run.ok is True
    assert run.metrics["distinct_session_count"] == 5
    assert len(run.metrics["owner_cardinality_facts"]) == 7
    assert run.metrics["terminal_fact"]["terminal_event_state"] == "completed"
    for expected_sessions, window in enumerate(
        run.metrics["steady_state_windows"],
        start=2,
    ):
        cardinalities = window["cache_cardinalities"]
        assert cardinalities["contextctl_pack_cache"] == 0
        assert cardinalities["contextctl_manifest_index"] == expected_sessions
        assert cardinalities["contextctl_latest_sessions"] == expected_sessions
    assert run.metrics["close_sample"]["cache_cardinalities"] == {
        "contextctl_pack_cache": 0,
        "contextctl_manifest_index": 0,
        "contextctl_latest_sessions": 0,
    }
    owner_facts = {
        fact["owner"]: fact for fact in run.metrics["owner_cardinality_facts"]
    }
    assert owner_facts["ContextCtlService pack and manifest caches"] == {
        "owner": "ContextCtlService pack and manifest caches",
        "lifetime": "Brain/context-service runtime lifetime",
        "observed_cardinality": {
            "pack_cache": 0,
            "manifest_index": 6,
            "latest_sessions": 6,
        },
        "natural_invalidation": (
            "ContextCtl close delegated through Brain/runtime close"
        ),
        "disposition": "defer:session-lifecycle-contract-required",
    }
    for owner in (
        "repo-map cache",
        "file backend cache",
        "control-plane submission audit/dedup",
    ):
        assert owner_facts[owner]["observed_cardinality"] is None


def test_omfla_provider_lifecycle_closes_and_recreates(tmp_path: Path) -> None:
    module = _load_module()

    run = module._measure_provider_lifecycle_loopback(
        _omfla_options(module, tmp_path),
        warmup_calls=1,
        measured_calls=2,
    )

    assert run.ok is True
    assert run.metrics["http_client_closed"] is True
    assert run.metrics["mcp_session_closed"] is True
    assert (
        run.metrics["close_sample"]["thread_count"]
        <= run.metrics["ready_sample"]["thread_count"]
    )
    assert run.metrics["terminal_fact"] == {
        "http": "completed",
        "mcp": "completed",
        "recreate": "completed",
    }


def test_omfla_agent_cache_honors_bound_and_ttl(tmp_path: Path) -> None:
    module = _load_module()

    run = module._measure_agent_cache_churn(
        _omfla_options(module, tmp_path),
        agent_count=3,
        max_agents_hot=2,
        convergence_wait_seconds=3,
    )

    assert run.ok is True
    assert run.metrics["max_observed_hot_agents"] <= 2
    assert run.metrics["remaining_agents_after_convergence"] == 0


def test_omfla_runtime_restart_closes_each_owner_twice(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_module()
    from tests.e2e.cli.focus.harness import FocusProbe

    def reject_screen_owned_completion(*_args, **_kwargs):
        raise AssertionError("FocusProbe.run_turn must not own OMFLA completion")

    monkeypatch.setattr(FocusProbe, "run_turn", reject_screen_owned_completion)

    run = module._measure_runtime_restart(
        _omfla_options(module, tmp_path),
        cycle_count=5,
    )

    assert run.ok is True
    assert run.metrics["completed_turn_count"] == 15
    assert run.metrics["second_close_idempotency_checked"] is True
    assert run.metrics["descriptor_counts_converged"] is True
    ready = run.metrics["ready_sample"]
    for window in run.metrics["steady_state_windows"]:
        assert window["file_descriptor_count"] == ready["file_descriptor_count"]
        assert window["open_file_count"] == ready["open_file_count"]
    assert (
        run.metrics["close_sample"]["file_descriptor_count"]
        == ready["file_descriptor_count"]
    )
    assert run.metrics["close_sample"]["open_file_count"] == ready["open_file_count"]
    assert run.metrics["terminal_fact"]["terminal_event_state"] == "completed"


def test_omfla_queue_pressure_drains_and_reports_overflow(tmp_path: Path) -> None:
    module = _load_module()

    run = module._measure_queue_pressure(
        _omfla_options(module, tmp_path),
        cycle_count=1,
        finite_capacity=3,
        unbounded_count=5,
    )

    assert run.ok is True
    assert run.metrics["queue_depths"] == {
        "controlplane_inbox": 0,
        "controlplane_outbox": 0,
        "memory_followup": 0,
        "runtime_chunks": 0,
        "telemetry_noncritical_export": 0,
        "turn_input": 0,
    }
    assert run.metrics["overflow_counts"] == {"telemetry": 1, "turn_input": 1}
    assert run.metrics["cache_cardinalities"]["turn_input_terminal_audit"] == 3
    assert run.metrics["cache_cardinalities"]["turn_input_idempotency"] == 3
