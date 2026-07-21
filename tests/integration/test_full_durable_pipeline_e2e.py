from __future__ import annotations

from pathlib import Path

from tests.integration.test_durable_pipeline_e2e import (
    test_durable_pipeline_full_flow as _run_durable_pipeline_full_flow,
)


def test_full_durable_pipeline_flow(tmp_path: Path) -> None:
    _run_durable_pipeline_full_flow(tmp_path)
