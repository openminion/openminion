from __future__ import annotations

import os
from pathlib import Path
import shutil
import socket
import subprocess
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
    shutil.rmtree(ARTIFACT_ROOT, ignore_errors=True)
    ARTIFACT_ROOT.mkdir(parents=True, mode=0o777)
    ARTIFACT_ROOT.chmod(0o777)
    env = {**os.environ, "OTEL_E2E_ARTIFACTS": str(ARTIFACT_ROOT)}
    compose = ["docker", "compose", "-f", str(COMPOSE_FILE)]

    subprocess.run([*compose, "down", "--remove-orphans"], env=env, check=False)
    subprocess.run([*compose, "up", "-d"], env=env, check=True)
    try:
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
        yield ARTIFACT_ROOT
    finally:
        subprocess.run([*compose, "down", "--remove-orphans"], env=env, check=False)
