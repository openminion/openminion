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
    runs = [
        {
            "scenario_id": "local_status_tool_turn",
            "ok": True,
            "provider_variance_class": module.LOCAL_VARIANCE,
            "metrics": {
                "wall_time_ms": 10,
                "rss_delta_bytes": 100,
                "tracemalloc_peak_bytes": 1000,
                "prompt_tokens_estimated": 5,
            },
        },
        {
            "scenario_id": "local_status_tool_turn",
            "ok": True,
            "provider_variance_class": module.LOCAL_VARIANCE,
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
    assert local["prompt_tokens_estimated"]["max"] == 7
    assert local["warn_only"] is False
    assert summary["scenarios"]["provider_turn"]["warn_only"] is True


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
        "rss_start_bytes",
        "rss_end_bytes",
        "rss_delta_bytes",
        "tracemalloc_current_bytes",
        "tracemalloc_peak_bytes",
        "tool_call_count",
    ):
        assert key in run.metrics
    assert run.metrics["tool_call_count"] == 1


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
