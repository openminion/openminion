import importlib.util
import json
from pathlib import Path

TEST_CORTENSOR_API_KEY = "fabc7432-a81e-47a9-a352-31145275809a"


def _load_suite_module():
    script_path = (
        Path(__file__).resolve().parents[1]
        / "tests"
        / "e2e"
        / "runners"
        / "run_cortensor_e2e_suite.py"
    )
    spec = importlib.util.spec_from_file_location(
        "openminion_tests_e2e_runners_run_cortensor_e2e_suite",
        script_path,
    )
    if spec is None or spec.loader is None:
        raise AssertionError("failed to load cortensor_e2e_suite module spec")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _minimal_base_config(storage_path: str) -> dict:
    return {
        "agents": {"openminion": {"name": "openminion", "provider": "cortensor"}},
        "default_agent": "openminion",
        "gateway": {"api_turn_timeout_seconds": 120},
        "providers": {
            "cortensor": {
                "api_key": TEST_CORTENSOR_API_KEY,
                "api_key_env": "CORTENSOR_API_KEY",
                "api_mode": "cortensor_completion",
                "prompt_type": 1,
                "session_id": 35,
                "session_ids": [35],
                "session_pool": "auto",
                "session_parallel_requests": 1,
                "session_retry_rounds": 1,
            }
        },
        "runtime": {"log_level": "INFO"},
        "storage": {"path": storage_path},
    }


def _suite_args(module, root: Path):
    return module._parse_args(
        [
            "--root",
            str(root),
            "--base-config",
            "test-configs/cortensor-e2e.json",
            "--fallback-config",
            "test-configs/per-agent.json",
        ]
    )


def test_missing_base_config_is_copied_from_fallback(tmp_path: Path) -> None:
    module = _load_suite_module()
    tmp_dir = tmp_path / ".tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    test_configs = tmp_path / "test-configs"
    test_configs.mkdir(parents=True, exist_ok=True)

    fallback_path = test_configs / "per-agent.json"
    fallback_payload = _minimal_base_config(
        storage_path=str(tmp_dir / "state" / "openminion.db")
    )
    fallback_path.write_text(
        json.dumps(fallback_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    suite = module.CortensorE2ESuite(_suite_args(module, tmp_path))

    assert suite.base_config_path.exists()
    copied_payload = json.loads(suite.base_config_path.read_text(encoding="utf-8"))
    assert copied_payload["providers"]["cortensor"]["session_id"] == 35
    assert suite.runtime_config.exists()
    assert suite.failover_config.exists()
    runtime_payload = json.loads(suite.runtime_config.read_text(encoding="utf-8"))
    failover_payload = json.loads(suite.failover_config.read_text(encoding="utf-8"))
    assert (
        runtime_payload["providers"]["cortensor"]["api_key"] == TEST_CORTENSOR_API_KEY
    )
    assert (
        failover_payload["providers"]["cortensor"]["api_key"] == TEST_CORTENSOR_API_KEY
    )


def test_missing_base_and_fallback_raises(tmp_path: Path) -> None:
    module = _load_suite_module()
    try:
        module.CortensorE2ESuite(_suite_args(module, tmp_path))
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("expected FileNotFoundError")
