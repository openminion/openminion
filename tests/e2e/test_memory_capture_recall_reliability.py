from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from openminion.api.runtime import APIRuntime
from openminion.api.turns import run_turn
from openminion.base.config import OpenMinionConfig, save_config
from openminion.modules.memory.runtime.capture_bundle import (
    CaptureBundleInput,
    CaptureCandidateInput,
)
from tests._csc_fixtures import _csc_install_default_agent

pytestmark = pytest.mark.e2e


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

        service = assembly.service
        assert service is not None
        agent_id = assembly.gateway._agent_id
        bundle = CaptureBundleInput(
            capture_id="capture-mrst-same-agent",
            root_turn_id="turn-mrst-same-agent",
            session_id="mcrr-capture-session",
            agent_id=agent_id,
            candidates=(
                CaptureCandidateInput(
                    kind="fact",
                    normalized_key="project_codename:blue-orchid",
                    title="Project codename",
                    content="The project codename is Blue Orchid.",
                    confidence=0.95,
                ),
            ),
        )
        receipt = service.apply_capture_bundle(bundle)
        candidate_id = receipt.output_ids[0]
        service.candidate_update(candidate_id, {"status": "approved"})
        promoted = service.promote_candidate(candidate_id, f"agent:{agent_id}")

        other_bundle = CaptureBundleInput(
            capture_id="capture-mrst-other-agent",
            root_turn_id="turn-mrst-other-agent",
            session_id="mcrr-other-session",
            agent_id="other",
            candidates=(
                CaptureCandidateInput(
                    kind="fact",
                    normalized_key="project_codename:red-cedar",
                    title="Other project codename",
                    content="The other project codename is Red Cedar.",
                    confidence=0.95,
                ),
            ),
        )
        other_receipt = service.apply_capture_bundle(other_bundle)
        other_candidate_id = other_receipt.output_ids[0]
        service.candidate_update(other_candidate_id, {"status": "approved"})
        other_promoted = service.promote_candidate(other_candidate_id, "agent:other")

        second_session = "mcrr-recall-session"
        hits, _precision = assembly.gateway.recall_context(
            session_id=second_session,
            query="Blue Orchid",
            scopes=assembly.gateway.context_scopes(session_id=second_session),
        )
        hit_by_id = {str(hit.get("meta", {}).get("record_id", "")): hit for hit in hits}

        assert promoted.id in hit_by_id
        assert other_promoted.id not in hit_by_id
        recalled = hit_by_id[promoted.id]
        assert recalled["meta"]["record_key"] == "project_codename:blue-orchid"
        assert recalled["text"] == "The project codename is Blue Orchid."
        assert bundle.capture_id in recalled["meta"]["record_evidence_refs"]
    finally:
        runtime.close()
