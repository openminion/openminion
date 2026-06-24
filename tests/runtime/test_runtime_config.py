from __future__ import annotations

import os
from pathlib import Path

from openminion.services.runtime.settings import RuntimeConfig


def _load_from_content(tmp_path: Path, content: str) -> RuntimeConfig:
    path = tmp_path / "runtime.yaml"
    path.write_text(content, encoding="utf-8")
    return RuntimeConfig.from_yaml(str(path))


def test_defaults_on_missing_file() -> None:
    cfg = RuntimeConfig.from_yaml("/nonexistent/path/runtime.yaml")
    assert cfg.max_agents_hot == 8
    assert cfg.max_global_concurrency == 8
    assert cfg.agent_ttl_seconds == 1800
    assert cfg.sweep_interval_seconds == 5
    assert cfg.cron.enabled is True
    assert cfg.cron.tick_ms == 2000


def test_load_from_yaml(tmp_path: Path) -> None:
    content = """\
runtimectl:
  max_agents_hot: 4
  max_global_concurrency: 2
  agent_ttl_seconds: 600
  sweep_interval_seconds: 10
"""
    cfg = _load_from_content(tmp_path, content)
    assert cfg.max_agents_hot == 4
    assert cfg.max_global_concurrency == 2
    assert cfg.agent_ttl_seconds == 600
    assert cfg.sweep_interval_seconds == 10


def test_zero_values_clamped_to_one() -> None:
    cfg = RuntimeConfig(
        max_agents_hot=0,
        max_global_concurrency=-5,
        agent_ttl_seconds=0,
        sweep_interval_seconds=-1,
    )
    assert cfg.max_agents_hot == 1
    assert cfg.max_global_concurrency == 1
    assert cfg.agent_ttl_seconds == 1
    assert cfg.sweep_interval_seconds == 1


def test_as_dict_returns_all_fields() -> None:
    cfg = RuntimeConfig()
    d = cfg.as_dict()
    assert set(d.keys()) == {
        "max_agents_hot",
        "max_global_concurrency",
        "agent_ttl_seconds",
        "sweep_interval_seconds",
        "cron",
    }
    assert "run_log" in d["cron"]


def test_load_from_actual_runtimectl_yaml() -> None:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    yaml_path = os.path.join(root, "runtime.yaml")
    if not os.path.exists(yaml_path):
        return  # skip if not present

    cfg = RuntimeConfig.from_yaml(yaml_path)
    assert cfg.max_agents_hot >= 1
    assert cfg.max_global_concurrency >= 1
    assert cfg.agent_ttl_seconds >= 1
    assert cfg.sweep_interval_seconds >= 1


def test_partial_yaml_uses_defaults_for_missing_keys(tmp_path: Path) -> None:
    content = """\
runtimectl:
  max_agents_hot: 3
"""
    cfg = _load_from_content(tmp_path, content)
    assert cfg.max_agents_hot == 3
    assert cfg.max_global_concurrency == 8
    assert cfg.agent_ttl_seconds == 1800
    assert cfg.sweep_interval_seconds == 5
    assert cfg.cron.max_concurrent_runs == 4


def test_cron_yaml_section_loads_values(tmp_path: Path) -> None:
    content = """\
runtimectl:
  cron:
    enabled: false
    tick_ms: 1500
    max_concurrent_runs: 2
    lease_ttl_seconds: 45
    session_retention: false
    run_log:
      max_bytes: 1000
      keep_lines: 50
"""
    cfg = _load_from_content(tmp_path, content)
    assert cfg.cron.enabled is False
    assert cfg.cron.tick_ms == 1500
    assert cfg.cron.max_concurrent_runs == 2
    assert cfg.cron.lease_ttl_seconds == 45
    assert cfg.cron.session_retention is False
    assert cfg.cron.run_log.max_bytes == 1000
    assert cfg.cron.run_log.keep_lines == 50
