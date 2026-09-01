from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from openminion.api.runtime import APIRuntime
from openminion.api.turns import run_turn
from openminion.base.config import OpenMinionConfig, save_config
from tests._csc_fixtures import _csc_install_default_agent


def _echo_config(tmp_path: Path) -> Path:
    config = OpenMinionConfig()
    _csc_install_default_agent(config, provider="echo")
    config.runtime.log_level = "ERROR"
    config.runtime.memory_enabled = True
    config.storage.path = str(tmp_path / "state" / "runtime.db")
    config_path = tmp_path / "config.json"
    save_config(config, str(config_path))
    return config_path


def test_terminal_capture_uses_shared_runtime_and_releases_hold(tmp_path) -> None:
    config_path = _echo_config(tmp_path)
    runtime = APIRuntime.from_config_path(str(config_path))
    try:
        result = run_turn(
            str(config_path),
            {
                "message": "Remember my name is Ada and keep responses concise.",
                "session_id": "mcrr-e2e",
            },
            runtime=runtime,
        )

        assembly = runtime.runtime_memory_assembly
        assert runtime.gateway._agent_memory is assembly.gateway
        assert runtime.agent._get_runner().memory_api is assembly.memctl
        intent = json.loads(result["metadata"]["terminal_capture_intent_receipt"])
        capture = json.loads(result["metadata"]["memory_capture_bundle_result"])
        assert intent["state"] == "pending"
        assert capture["capture_id"] == intent["capture_id"]
        assert capture["disposition"] in {"succeeded", "succeeded_no_output"}
        assert result["metadata"]["memory_capture_state"] == capture["disposition"]

        persisted_intent = runtime.sessions.get_event_by_canonical_id(
            intent["event_id"]
        )
        persisted_result = runtime.sessions.get_event_by_canonical_id(
            f"memory.capture.result:{intent['capture_id']}"
        )
        assert persisted_intent is not None
        assert persisted_result is not None
        assert "memory_capture_report" not in persisted_intent.payload
        assert "Ada" not in json.dumps(persisted_intent.payload)
        with sqlite3.connect(runtime.storage_path) as connection:
            pending_holds = connection.execute(
                "SELECT COUNT(*) FROM session_retention_holds WHERE released_at IS NULL"
            ).fetchone()[0]
        assert pending_holds == 0
    finally:
        runtime.close()
