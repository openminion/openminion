from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def test_collector_ci_is_independent_and_preserves_evidence() -> None:
    workflow = yaml.load(
        (ROOT / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader
    )
    job = workflow["jobs"]["telemetry-collector-e2e"]
    assert "needs" not in job
    assert "if" not in job
    assert any(
        step.get("run") == "make test-telemetry-collector-ci" for step in job["steps"]
    )

    upload = job["steps"][-1]
    assert upload["if"] == "always()"
    assert upload["with"]["if-no-files-found"] == "error"
    assert upload["with"]["retention-days"] == "14"
    assert upload["with"]["path"] == (
        "../workspace-tmp/agent-observability-e2e/current/"
    )


def test_collector_make_target_requires_docker_and_real_transport_tests() -> None:
    target = (
        (ROOT / "Makefile")
        .read_text()
        .split("test-telemetry-collector-ci:", 1)[1]
        .split("ci-check:", 1)[0]
    )
    assert "docker info" in target
    assert "test_live_export_probe_reaches_pinned_collector" in target
    assert "test_otlp_http_routes_traces_metrics_and_logs" in target
