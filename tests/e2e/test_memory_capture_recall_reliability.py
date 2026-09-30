from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from openminion.api import Agent
from openminion.api.handoff import DelegatedMemoryReadRequest, subagent
from openminion.api.runtime import APIRuntime
from openminion.api.turns import run_turn
from openminion.base.config import OpenMinionConfig, save_config
from openminion.modules.memory.runtime.capture_bundle import (
    CaptureBundleInput,
    CaptureCandidateInput,
)
from openminion.modules.llm.providers.base import (
    LLMProvider,
    ProviderRequest,
    ProviderResponse,
)
from tests._csc_fixtures import _csc_install_default_agent
from sophiagraph.models import MemoryNamespace, MemoryRecord

pytestmark = pytest.mark.e2e


class _CaptureProvider(LLMProvider):
    name = "capture"

    def __init__(self) -> None:
        self.requests: list[ProviderRequest] = []

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.requests.append(request)
        return ProviderResponse(text="captured", model="capture")


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


def test_developer_subagent_receives_only_grant_bound_memory(tmp_path) -> None:
    runtime = APIRuntime.from_config_path(str(_echo_config(tmp_path)))
    namespace = MemoryNamespace(
        agent_id="parent",
        project_id="project",
        graph_id="main",
    )
    now = datetime.now(UTC).isoformat()
    try:
        store = runtime.runtime_memory_assembly.delegated_store
        assert store is not None
        store.put_record(
            MemoryRecord(
                id="delegated-record",
                scope="agent:parent",
                type="fact",
                content="delegated marker",
                created_at=now,
                updated_at=now,
                namespace=namespace,
            )
        )
        store.put_record(
            MemoryRecord(
                id="foreign-record",
                scope="agent:other",
                type="fact",
                content="delegated marker foreign secret",
                created_at=now,
                updated_at=now,
                namespace=MemoryNamespace(
                    agent_id="other",
                    project_id="project",
                    graph_id="main",
                ),
            )
        )
        parent = Agent(runtime=runtime, name="parent")
        child = subagent(
            parent,
            name="child",
            memory=DelegatedMemoryReadRequest(
                namespaces=(namespace,),
                workspace_ids=("workspace",),
                record_types=("fact",),
                max_results=2,
                max_context_tokens=128,
            ),
        )
        capture = _CaptureProvider()
        runtime.gateway._agent._llm_runtime = None
        runtime.gateway._agent._provider = capture

        child.run("delegated marker")

        provider_context = repr(capture.requests)
        assert "delegated-record" in provider_context
        assert "delegated marker" in provider_context
        assert "foreign-record" not in provider_context
        assert "foreign secret" not in provider_context

        selection_events = [
            event
            for event in runtime.telemetry_service._store.fetch_events()
            if event.data.get("operation") == "delegated_access.selection"
        ]
        assert len(selection_events) == 1
        assert selection_events[0].data["selected_count"] == 1
        assert "delegated-record" not in repr(selection_events[0].data)
        assert "foreign-record" not in repr(selection_events[0].data)
        assert all(
            grant.revoked_at is not None
            for grant in runtime.action_policy.list_grants()
        )
    finally:
        runtime.close()
