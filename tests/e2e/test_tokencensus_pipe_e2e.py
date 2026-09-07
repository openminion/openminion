from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


pytestmark = pytest.mark.e2e

_RUNNER_PATH = (
    Path(__file__).resolve().parent / "runners" / "run_tokencensus_pipe_e2e.py"
)


def _load_runner():
    spec = importlib.util.spec_from_file_location(
        "run_tokencensus_pipe_e2e", _RUNNER_PATH
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_usage_payload_assertion_rejects_prompt_or_secret_leak() -> None:
    runner = _load_runner()

    with pytest.raises(RuntimeError, match="prompt or secret content leaked"):
        runner._assert_usage_payload(
            {
                "schema_version": "openminion.token_usage.v1",
                "session_id": "token-pipe-session",
                "totals": {"provider_tokens": 18, "derived_tokens": 3},
                "costs": {
                    "provider_cost_usd": 0.003,
                    "estimated_cost_usd": 0.001,
                },
                "coverage": {
                    "failed_llm_call_events": 2,
                    "unmetered_llm_call_events": 1,
                },
                "records": [{"extra": "do-not-export-secret"}],
            }
        )


def test_tokencensus_payload_assertion_requires_complete_pipe() -> None:
    runner = _load_runner()

    with pytest.raises(RuntimeError, match="complete=true"):
        runner._assert_tokencensus_payload(
            {
                "complete": False,
                "envelope_count": 1,
                "record_count": 2,
                "totals": {"provider_tokens": 18},
            }
        )


def test_installed_env_removes_source_path_injection(tmp_path: Path) -> None:
    runner = _load_runner()

    env = runner._installed_env(tmp_path / "home", tmp_path / "data")

    assert "PYTHONPATH" not in env
    runner._assert_no_source_injection(env)
