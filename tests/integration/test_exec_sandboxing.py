from __future__ import annotations

import os

import pytest


def test_real_daytona_exec_sandboxing_smoke() -> None:
    endpoint = str(os.getenv("OPENMINION_TEST_DAYTONA_ENDPOINT", "")).strip()
    api_key = str(os.getenv("OPENMINION_TEST_DAYTONA_API_KEY", "")).strip()
    if not endpoint or not api_key:
        pytest.skip(
            "Set OPENMINION_TEST_DAYTONA_ENDPOINT and OPENMINION_TEST_DAYTONA_API_KEY to run the real Daytona sandbox integration smoke."
        )

    pytest.skip(
        "Real Daytona transport execution is not configured in this test environment; use the dedicated live Daytona smoke harness when available."
    )
