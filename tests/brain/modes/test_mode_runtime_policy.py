from __future__ import annotations

from pathlib import Path
import tempfile

import pytest

from openminion.base.config import (
    ConfigError,
    OpenMinionConfig,
    build_capability_runtime_diagnostics,
    build_runtime_config,
)
from openminion.modules.brain.adapters.a2a import LocalA2AAdapter
from openminion.modules.brain.adapters.context import LocalContextAdapter
from openminion.modules.brain.adapters.memory import LocalMemoryAdapter
from openminion.modules.brain.adapters.policy import LocalPolicyAdapter
from openminion.modules.brain.adapters.session import LocalSessionStore
from openminion.modules.brain.adapters.tool import LocalToolAdapter
from openminion.modules.brain.bootstrap.route_catalog import (
    available_routes,
    is_route_enabled,
)
from openminion.modules.brain.runner import RunnerOptions, BrainRunner
from openminion.services.brain.service import _runtime_mode_config_from_agent
from tests.brain.runner_test_support import _profile


def _runner_with_mode_config(mode_config):
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    session = LocalSessionStore(root / "sessions")
    profile = _profile().model_copy(update={"mode_config": mode_config})
    runner = BrainRunner(
        profile=profile,
        session_api=session,
        context_api=LocalContextAdapter(session_store=session),
        tool_api=LocalToolAdapter(),
        a2a_api=LocalA2AAdapter(),
        memory_api=LocalMemoryAdapter(root / "memory"),
        policy_api=LocalPolicyAdapter(),
        options=RunnerOptions(metactl_enabled=False),
    )
    return tmp, runner


def test_system_disabled_mode_stays_disabled_even_when_agent_requests_enable() -> None:
    config = OpenMinionConfig.from_dict(
        {
            "system": {
                "runtime": {
                    "modes": {
                        "delegate": {"enabled": False},
                    }
                }
            },
            "agents": {
                "openminion": {
                    "name": "openminion",
                    "modes": {
                        "delegate": {"enabled": True},
                    },
                },
            },
            "default_agent": "openminion",
        }
    )

    effective = build_runtime_config(config)
    mode_config = _runtime_mode_config_from_agent(effective)
    diagnostics = build_capability_runtime_diagnostics(config)

    assert mode_config["delegate"].enabled is False
    assert "delegate" in diagnostics["modes"]["blocked_reasons"]

    tmp, runner = _runner_with_mode_config(mode_config)
    try:
        assert "delegate" not in available_routes(runner.profile)
        assert is_route_enabled(runner.profile, "delegate") is False
    finally:
        tmp.cleanup()


def test_runtime_mode_policy_carries_strategy_budget_fields_through_bridge() -> None:
    config = OpenMinionConfig.from_dict(
        {
            "system": {
                "runtime": {
                    "modes": {
                        "research": {
                            "enabled": True,
                            "checkpoint_interval": 2,
                            "max_resume_count": 4,
                            "max_research_iterations": 6,
                        },
                        "coding": {
                            "enabled": True,
                            "max_adaptive_iterations": 30,
                            "max_self_corrections": 5,
                        },
                        "orchestrate": {
                            "enabled": True,
                            "parallel_enabled": False,
                            "max_parallel_workers": 2,
                            "max_subtasks": 4,
                        },
                    }
                }
            },
            "agents": {
                "openminion": {
                    "name": "openminion",
                    "modes": {
                        "research": {
                            "max_research_iterations": 9,
                        },
                        "coding": {
                            "max_self_corrections": 3,
                        },
                        "orchestrate": {
                            "parallel_enabled": True,
                            "max_subtasks": 7,
                        },
                    },
                },
            },
            "default_agent": "openminion",
        }
    )

    effective = build_runtime_config(config)
    mode_config = _runtime_mode_config_from_agent(effective)

    assert mode_config["research"].checkpoint_interval == 2
    assert mode_config["research"].max_resume_count == 4
    assert mode_config["research"].max_research_iterations == 9
    assert mode_config["coding"].max_adaptive_iterations == 30
    assert mode_config["coding"].max_self_corrections == 3
    assert mode_config["orchestrate"].parallel_enabled is True
    assert mode_config["orchestrate"].max_parallel_workers == 2
    assert mode_config["orchestrate"].max_subtasks == 7


def test_runtime_mode_policy_rejects_invalid_strategy_budget_values() -> None:
    with pytest.raises(
        ConfigError,
        match=r"system\.runtime\.modes\.research\.max_resume_count must be >= 0\.",
    ):
        OpenMinionConfig.from_dict(
            {
                "system": {
                    "runtime": {
                        "modes": {
                            "research": {
                                "enabled": True,
                                "max_resume_count": -1,
                            }
                        }
                    }
                }
            }
        )
