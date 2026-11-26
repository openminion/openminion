"""Capture/admission and authorized recall through the assembled runtime."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4
from unittest.mock import Mock

import pytest

from openminion.api import Agent
from openminion.api.handoff import DelegatedMemoryReadRequest, subagent
from openminion.api.runtime import APIRuntime
from openminion.api.turns import run_turn
from openminion.base.config import OpenMinionConfig, load_config, save_config
from openminion.modules.memory.runtime.capture_bundle import (
    CaptureBundleInput,
    CaptureCandidateInput,
)
from openminion.modules.memory.storage.base import ListQueryOptions
from openminion.modules.tool.base import ToolExecutionContext
from openminion.modules.tool.contracts import ProviderToolCall
from openminion.modules.tool.registry import ToolRegistry
from openminion.tools.memory import REGISTRAR
from openminion.services.constants import SERVICES_PROJECT_ID_ENV
from openminion.services.runtime.cron.executor import _capture_candidate_extractor
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


def _fresh_process_recall(config_path: Path, session_id: str) -> tuple[str, list[str]]:
    # Only config and session identity cross the process boundary, never the oracle.
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import json
import sys
from unittest.mock import patch
from openminion.api import Agent
from openminion.api.runtime import APIRuntime
from openminion.modules.memory.service import MemoryService
from tests.e2e.test_memory_capture_recall_reliability import _CaptureProvider

runtime = APIRuntime.from_config_path(sys.argv[1])
try:
    capture = _CaptureProvider()
    runtime.gateway._agent._llm_runtime = None
    runtime.gateway._agent._provider = capture
    with patch.object(
        MemoryService, "touch_last_hit", autospec=True,
        side_effect=MemoryService.touch_last_hit,
    ) as touched:
        Agent(runtime=runtime).run(
            "What is the continuity code?", session_id=sys.argv[2]
        )
    assert capture.requests
    selected_record_ids = [call.args[1] for call in touched.call_args_list]
    print(json.dumps([repr(capture.requests), selected_record_ids]))
finally:
    runtime.close()
""",
            str(config_path),
            session_id,
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    requests, selected_record_ids = json.loads(completed.stdout.splitlines()[-1])
    return requests, selected_record_ids


@pytest.mark.parametrize(
    "interrupt_capture", [False, True], ids=["terminal", "recovery"]
)
def test_automatic_capture_stays_proposed_across_fresh_process(
    tmp_path, monkeypatch, interrupt_capture
) -> None:
    config_path = _echo_config(tmp_path)
    token = uuid4().hex
    runtime = APIRuntime.from_config_path(str(config_path))
    try:
        runner = runtime.agent._get_runner()
        original = runner.llm_api.call_structured
        extraction_inputs = []

        def model_response(**kwargs):
            if kwargs["schema"].__name__ == "UserMessageCandidateReport":
                if interrupt_capture:
                    raise TimeoutError("fixture interrupted extraction")
                extraction_inputs.append(kwargs["context"])
                return {
                    "items": [
                        {
                            "kind": "fact",
                            "normalized_key": "fact:continuity_code",
                            "title": "Continuity code",
                            "content": token,
                            "tags": [],
                        }
                    ]
                }
            return original(**kwargs)

        monkeypatch.setattr(runner.llm_api, "call_structured", model_response)
        result = run_turn(
            str(config_path),
            {
                "message": f"The continuity code is {token}.",
                "session_id": "csma-auto-teach",
            },
            runtime=runtime,
        )
        receipt = json.loads(result["metadata"]["memory_capture_bundle_result"])
        intent = json.loads(result["metadata"]["terminal_capture_intent_receipt"])
        if interrupt_capture:
            assert receipt["disposition"] == "pending"
            interrupt_capture = False
            assembly = runtime.runtime_memory_assembly
            recovery_args = {
                "sessions": runtime.sessions,
                "agent_id": assembly.gateway._agent_id,
                "extract_candidates": _capture_candidate_extractor(runtime.agent),
                "authorize": Mock(return_value=True),
            }
            assert assembly.recover_pending_captures(**recovery_args).recovered == 1
            assert assembly.recover_pending_captures(**recovery_args).recovered == 0
            recovery_args["authorize"].assert_called_once_with(
                "csma-auto-teach",
                intent["root_turn_id"],
                intent["event_id"],
                intent["capture_id"],
            )
            receipt = runtime.sessions.get_event_by_canonical_id(
                f"memory.capture.result:{intent['capture_id']}"
            ).payload
        assert len(extraction_inputs) == 1
        assert token in repr(extraction_inputs[0])
        assert receipt["disposition"] == "succeeded"
        assert len(receipt["output_ids"]) == 1
        service = runtime.runtime_memory_assembly.service
        candidate = service.candidate_get(receipt["output_ids"][0])
        assert candidate.content == token
        assert candidate.status == "proposed"
        assert candidate.meta["capture_id"] == receipt["capture_id"]
        assert candidate.meta["source_root_turn_id"] == intent["root_turn_id"]
        assert (
            candidate.meta["source_agent_id"]
            == runtime.runtime_memory_assembly.gateway._agent_id
        )
        assert candidate.proposed_scope == f"agent:{candidate.meta['source_agent_id']}"
        assert candidate.session_id == "csma-auto-teach"
        assert service.list(ListQueryOptions(scopes=[candidate.proposed_scope])) == []
    finally:
        runtime.close()

    requests, selected_record_ids = _fresh_process_recall(
        config_path, "csma-auto-recall"
    )
    assert token not in requests
    assert candidate.candidate_id not in selected_record_ids
    runtime = APIRuntime.from_config_path(str(config_path))
    try:
        persisted = runtime.runtime_memory_assembly.service.candidate_get(
            candidate.candidate_id
        )
        assert persisted.content == token
        assert persisted.status == "proposed"
        assert persisted.meta["capture_id"] == receipt["capture_id"]
    finally:
        runtime.close()


def test_public_correction_and_forget_exclude_records_after_process_restart(
    tmp_path,
) -> None:
    config_path = _echo_config(tmp_path)
    tokens = [uuid4().hex, uuid4().hex]
    registry = ToolRegistry()
    REGISTRAR.register(registry)
    record_ids = []
    for index, token in enumerate(tokens):
        runtime = APIRuntime.from_config_path(str(config_path))
        try:
            assembly = runtime.runtime_memory_assembly
            result = registry.execute_calls(
                [
                    ProviderToolCall(
                        name="memory.write",
                        arguments={
                            "scope": f"agent:{assembly.gateway._agent_id}",
                            "record_type": "fact",
                            "key": "fact:continuity_code",
                            "title": "Continuity code",
                            "content": token,
                        },
                        id=f"write-{index}",
                        source="test",
                    )
                ],
                context=ToolExecutionContext(
                    channel="console",
                    target="test",
                    session_id=f"teach-{index}",
                    memory_service=assembly.service,
                ),
            ).results[0]
            assert result.ok
            record_ids.append(result.data["record_id"])
            carriers = assembly.service.list(
                ListQueryOptions(scopes=[f"agent:{assembly.gateway._agent_id}"])
            )
            assert [record.id for record in carriers] == [record_ids[-1]]
        finally:
            runtime.close()
        requests, selected_record_ids = _fresh_process_recall(
            config_path, f"csma-recall-{index}"
        )
        assert token in requests
        assert record_ids[-1] in selected_record_ids
        if index:
            assert tokens[0] not in requests
            assert record_ids[0] not in selected_record_ids

    runtime = APIRuntime.from_config_path(str(config_path))
    try:
        result = registry.execute_calls(
            [
                ProviderToolCall(
                    name="memory.forget",
                    arguments={
                        "record_id": record_ids[-1],
                        "reason": "fixture complete",
                    },
                    id="forget",
                    source="test",
                )
            ],
            context=ToolExecutionContext(
                channel="console",
                target="test",
                session_id="forget",
                memory_service=runtime.runtime_memory_assembly.service,
            ),
        ).results[0]
        assert result.ok
        assert result.data["deleted"]
    finally:
        runtime.close()
    requests, selected_record_ids = _fresh_process_recall(
        config_path, "csma-after-forget"
    )
    assert all(token not in requests for token in tokens)
    assert not set(record_ids).intersection(selected_record_ids)


def test_disabled_memory_excludes_persisted_value_from_fresh_request(tmp_path) -> None:
    config_path = _echo_config(tmp_path)
    token = uuid4().hex
    runtime = APIRuntime.from_config_path(str(config_path))
    try:
        assembly = runtime.runtime_memory_assembly
        assembly.service.write_record(
            scope=f"agent:{assembly.gateway._agent_id}",
            record_type="fact",
            key="fact:continuity_code",
            title="Continuity code",
            content=token,
        )
    finally:
        runtime.close()
    requests, _ = _fresh_process_recall(config_path, "enabled-control")
    assert token in requests

    config = load_config(str(config_path))
    config.runtime.memory_enabled = False
    save_config(config, str(config_path))
    requests, selected_record_ids = _fresh_process_recall(
        config_path, "disabled-recall"
    )
    assert token not in requests
    assert selected_record_ids == []


def test_fresh_process_recall_obeys_project_and_private_scope(
    tmp_path, monkeypatch
) -> None:
    config_path = _echo_config(tmp_path)
    monkeypatch.setenv(SERVICES_PROJECT_ID_ENV, "shared")
    shared, foreign, unrelated = (uuid4().hex for _ in range(3))
    runtime = APIRuntime.from_config_path(str(config_path))
    try:
        for scope, token in (("project:shared", shared), ("agent:foreign", foreign)):
            runtime.runtime_memory_assembly.service.write_record(
                scope=scope,
                record_type="fact",
                key="fact:continuity_code",
                title="Continuity code",
                content=token,
            )
        runtime.runtime_memory_assembly.service.write_record(
            scope="project:shared",
            record_type="fact",
            key="fact:weather",
            title="Weather forecast",
            content=unrelated,
        )
    finally:
        runtime.close()

    requests, _ = _fresh_process_recall(config_path, "shared-project")
    assert shared in requests
    assert foreign not in requests
    assert unrelated not in requests
    monkeypatch.setenv(SERVICES_PROJECT_ID_ENV, "other")
    requests, _ = _fresh_process_recall(config_path, "other-project")
    assert all(token not in requests for token in (shared, foreign, unrelated))


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
