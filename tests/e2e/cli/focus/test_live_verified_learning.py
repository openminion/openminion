"""Opt-in phased campaign; a gain claim requires the frozen external evaluator."""

import os
from pathlib import Path

import pytest

from tests.e2e.skill_learning.live_transfer import run_stage

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(6000)]


def test_live_verified_learning_stage():
    inputs = os.environ.get("OPENMINION_VERIFIED_LEARNING_INPUTS")
    if os.environ.get("OPENMINION_LIVE_CLI_FOCUS_E2E") != "1" or not inputs:
        pytest.skip("live verified learning requires explicit external campaign inputs")
    stage = os.environ["OPENMINION_VERIFIED_LEARNING_STAGE"]
    state = run_stage(Path(inputs), stage)
    expected = {
        "author": "awaiting_review",
        "prepare": "prepared",
        "evaluate": "evaluated",
    }
    assert state["stage"] == expected[stage], f"campaign stopped: {state['stage']}"
    if stage == "evaluate":
        assert state["verdict"] == "demonstrated_gain", state["verdict"]
