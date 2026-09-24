from __future__ import annotations

import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time

import pytest


COLLECTOR_IMAGE = (
    "otel/opentelemetry-collector-contrib:0.156.0@"
    "sha256:125bdbeb7590cc1952c5b3430ecf14063568980c2c93d5b38676cc0446ed8108"
)
COMPOSE_FILE = Path(__file__).with_name("compose.yaml")
ARTIFACT_ROOT = (
    Path(__file__).parents[4] / "workspace-tmp" / "agent-observability-e2e" / "current"
)


def _require_docker_daemon() -> None:
    try:
        result = subprocess.run(
            ["docker", "info"],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        pytest.skip("Docker is required for telemetry E2E collector tests")
    if result.returncode:
        reason = (
            result.stderr or result.stdout or "Docker daemon is unavailable"
        ).strip()
        pytest.skip(f"Docker is required for telemetry E2E collector tests: {reason}")


def _wait_for_collector() -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", 14317), timeout=1):
                return
        except OSError:
            time.sleep(0.25)
    raise RuntimeError("OpenTelemetry Collector did not accept OTLP traffic")


@pytest.fixture(scope="session")
def collector_artifacts() -> Path:
    _require_docker_daemon()
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    artifact_dir = Path(tempfile.mkdtemp(prefix="run-", dir=ARTIFACT_ROOT))
    artifact_dir.chmod(0o777)
    env = {**os.environ, "OTEL_E2E_ARTIFACTS": str(artifact_dir)}
    compose = ["docker", "compose", "-f", str(COMPOSE_FILE)]
    subprocess.run([*compose, "down", "--remove-orphans"], env=env, check=False)
    try:
        subprocess.run([*compose, "up", "-d"], env=env, check=True)
        _wait_for_collector()
        digest = subprocess.run(
            [
                "docker",
                "image",
                "inspect",
                COLLECTOR_IMAGE,
                "--format",
                "{{json .RepoDigests}}",
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert (
            "125bdbeb7590cc1952c5b3430ecf14063568980c2c93d5b38676cc0446ed8108" in digest
        )
        yield artifact_dir
    finally:
        subprocess.run([*compose, "down", "--remove-orphans"], env=env, check=False)
