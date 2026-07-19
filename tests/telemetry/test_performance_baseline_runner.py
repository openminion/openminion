from __future__ import annotations

import importlib.util
import json
import sys
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


def test_runner_script_importable() -> None:
    module = _load_module()

    assert hasattr(module, "main")
    assert hasattr(module, "run_baseline")
    assert hasattr(module, "summarize_runs")


def test_scenario_list_accepts_all_and_rejects_unknown() -> None:
    module = _load_module()

    assert module._scenario_list("all") == list(module.DEFAULT_SCENARIOS)
    assert module._scenario_list("simple_turn,context_heavy_turn") == [
        "simple_turn",
        "context_heavy_turn",
    ]

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


def test_comparison_rejects_identity_mismatch() -> None:
    module = _load_module()
    current_identity = module._measurement_identity(
        scenario_id="cold_focus_startup",
        command="python -m openminion --data-root /tmp/a --help",
        measured_boundary=module.SUT_BOUNDARY_SUBPROCESS,
        fixture_revision=module.STARTUP_FIXTURE_REVISION,
    )
    baseline_identity = dict(current_identity)
    baseline_identity["command"] = "python -m openminion --data-root /tmp/b --help"
    current = {
        "wall_time_ms": {"median": 100},
        "measurement_identity": current_identity,
    }
    baseline = {
        "scenarios": {
            "cold_focus_startup": {
                "wall_time_ms": {"median": 100},
                "measurement_identity": baseline_identity,
            }
        }
    }

    result = module._threshold_result(
        current=current,
        baseline=baseline,
        scenario_id="cold_focus_startup",
        threshold_mode="hard",
    )

    assert result["status"] == "ineligible"
    assert "command" in result["identity_errors"]


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
        "tool_call_count",
    ):
        assert key in run.metrics
    assert run.metrics["tool_call_count"] == 1
    assert run.metrics["wall_time_ns"] >= 0
    assert run.metrics["measurement_resolution"] == "perf_counter_ns"
    assert "local_status_collect_ns" in run.metrics["phase_timings_ns"]


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
            lambda metrics: metrics["required_lane_metadata_contract_preserved"] is True,
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
    assert isinstance(payload["wall_ns"], int)
    assert payload["phase_timings_ns"]["local_status_collect_ns"] >= 0
