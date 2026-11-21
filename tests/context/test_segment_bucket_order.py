"""Regression tests for #483: context-to-LLM block_kind ordering and schema contract."""

from __future__ import annotations

from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from threading import Thread
from typing import Any, cast, get_args
import unittest

import pytest
from sophiagraph import SophiaGraphMemoryStore
from sophiagraph.models import MemoryBlock, MemoryNamespace

from openminion import Agent, APIRuntime
from openminion.base.config import OpenMinionConfig, save_config
from openminion.modules.brain.adapters.context.runtime import ContextCtlAdapter
from openminion.modules.brain.adapters.llm.request import (
    _build_request,
    _messages_from_context,
)
from openminion.modules.brain.diagnostics.events import CanonicalEventLogger
from openminion.modules.brain.runner import delegates as runner_delegates
from openminion.modules.brain.runtime import context as brain_runtime_context
from openminion.modules.brain.runtime.context import build_context
from openminion.modules.brain.schemas import BudgetCounters, WorkingState
from openminion.modules.context.schemas import (
    BuildPackRequest,
    ContextBudgets,
    ContextSegment,
    IdentitySnippet,
    MemoryCard,
    SegmentBucket,
    SessionSlice,
    SessionTurn,
    bucket_caps_for,
)
from openminion.modules.context.segment.messages import segments_to_messages
from openminion.modules.context.service import ContextCtlService
from openminion.modules.llm.schemas import PromptBlockKind
from openminion.modules.runtime.self_model import (
    DEGRADED_GENERIC_CANDIDATE_REGISTRY_UNAVAILABLE,
    SelfModelSnapshot,
    section_degraded,
    section_ok,
)
from tests._csc_fixtures import _csc_install_default_agent

_OVERRIDE_ENV_KEYS = (
    "OPENMINION_HOME",
    "OPENMINION_DATA_ROOT",
    "OPENMINION_GENERATED_ROOT",
    "OPENMINION_CONFIG_PATH",
)


def _segment(
    seg_id: str, bucket: str, content: str, *, role: str = "system"
) -> ContextSegment:
    return ContextSegment(
        id=seg_id,
        bucket=bucket,  # type: ignore[arg-type]
        role=role,  # type: ignore[arg-type]
        content=content,
        token_estimate=1,
    )


class SegmentBucketOrderTests(unittest.TestCase):
    def test_memory_and_self_awareness_render_before_turn_input(self) -> None:
        # Deliberately unsorted input: turn_input first to prove sorting.
        segments = [
            _segment("turn", "turn_input", "user says hi", role="user"),
            _segment("mem", "memory", "remembered fact"),
            _segment("self", "self_awareness", "self model"),
            _segment("prefix", "static_prefix", "system prompt"),
        ]
        messages = segments_to_messages(segments)
        block_order = [m.meta["block_kind"] for m in messages]
        self.assertEqual(
            block_order,
            ["static_prefix", "self_awareness", "memory", "turn_input"],
        )
        self.assertLess(
            block_order.index("self_awareness"), block_order.index("turn_input")
        )
        self.assertLess(block_order.index("memory"), block_order.index("turn_input"))
        self.assertEqual(messages[-1].role, "user")
        self.assertEqual(messages[-1].meta["block_kind"], "turn_input")

    def test_stable_ordering_within_bucket(self) -> None:
        segments = [
            _segment("self-1", "self_awareness", "first self note"),
            _segment("self-2", "self_awareness", "second self note"),
            _segment("turn", "turn_input", "go", role="user"),
        ]
        messages = segments_to_messages(segments)
        self_awareness = next(
            m for m in messages if m.meta["block_kind"] == "self_awareness"
        )
        self.assertIn("first self note", self_awareness.content)
        self.assertIn("second self note", self_awareness.content)
        self.assertLess(
            self_awareness.content.index("first self note"),
            self_awareness.content.index("second self note"),
        )


class _IdentityStub:
    contract_version = "v1"

    def render(
        self,
        *,
        agent_id: str,
        purpose: str,
        max_tokens: int,
        provider_pref: Any = None,
    ) -> IdentitySnippet:
        del max_tokens, provider_pref
        return IdentitySnippet(
            agent_id=agent_id,
            profile_version="prof-v1",
            render_version="rend-v1",
            text=f"Agent: {agent_id}\nPurpose: {purpose}",
        )


class _SessionStub:
    contract_version = "v1"

    def __init__(self, recent_turns: list[SessionTurn] | None = None) -> None:
        self._recent_turns = list(recent_turns or [])

    def get_slice(
        self,
        *,
        session_id: str,
        purpose: str,
        limits: dict[str, int],
    ) -> SessionSlice:
        del purpose, limits
        return SessionSlice(
            session_id=session_id,
            slice_version="slice-v1",
            last_event_id="evt-last",
            summary_short="summary short",
            recent_turns=list(self._recent_turns),
        )

    def append_event(
        self, session_id: str, event_type: str, payload: dict[str, Any], **kwargs: Any
    ) -> str:
        del session_id, event_type, payload, kwargs
        return "evt-manifest"

    def emit_canonical_event(
        self, session_id: str, event_type: str, payload: dict[str, Any], **kwargs: Any
    ) -> str:
        del session_id, event_type, payload, kwargs
        return "evt-canonical"


class _MemoryStub:
    contract_version = "v1"

    def __init__(self, cards: list[MemoryCard] | None = None) -> None:
        self._cards = list(cards or [])

    def query_facts(self, **kwargs: Any) -> list[Any]:
        del kwargs
        return []

    def query_memory_cards(self, **kwargs: Any) -> list[MemoryCard]:
        del kwargs
        return list(self._cards)

    def recall_session_start_memory(self, **kwargs: Any) -> list[Any]:
        del kwargs
        return []

    def recall_mid_session_memory(self, **kwargs: Any) -> list[Any]:
        del kwargs
        return []

    def recall_recent_session_artifacts(self, **kwargs: Any) -> list[Any]:
        del kwargs
        return []

    def get_procedure(self, *, procedure_id: str) -> None:
        del procedure_id
        return None


class _ArtifactStub:
    contract_version = "v1"

    def query_digests(self, **kwargs: Any) -> list[Any]:
        del kwargs
        return []


def _patch_empty_tool_bundle(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Bundle:
        system_tools: list[dict[str, Any]] = []

    monkeypatch.setattr(
        "openminion.modules.brain.adapters.llm.request._TOOL_SCHEMA_SERVICE.get_tools_for_purpose",
        lambda **kwargs: _Bundle(),
    )


def _self_snapshot(agent_id: str = "agent-1") -> SelfModelSnapshot:
    return SelfModelSnapshot.from_sections(
        agent_id=agent_id,
        identity=section_ok(
            display_name="Mini",
            mission="Verify contracts.",
            api_token="top-secret-token",
        ),
        capabilities=section_ok(provider="echo", tool_count=4, enabled_tool_count=3),
        policy=section_ok(permission_mode="ask", sandbox="workspace-write"),
        memory_state=section_ok(
            provider="SophiaGraphMemoryStore", scopes=[f"agent:{agent_id}"]
        ),
        context_state=section_ok(budget_total=2048),
        knowledge_state=section_ok(providers=[]),
        improvement_state=section_degraded(
            DEGRADED_GENERIC_CANDIDATE_REGISTRY_UNAVAILABLE,
            phase="phase_a_bsil_only",
            policy="never",
            candidate_count=0,
            promotion_posture="bsil_only",
        ),
    )


def _memory_block(
    block_id: str,
    *,
    agent_id: str = "agent-1",
    class_name: str = "agent_identity",
    mode: str = "pinned",
    content: str,
    token_estimate: int = 16,
) -> MemoryBlock:
    now = datetime(2026, 7, 18, tzinfo=timezone.utc).isoformat()
    return MemoryBlock(
        block_id=block_id,
        class_name=class_name,
        mode=mode,
        content=content,
        token_estimate=token_estimate,
        owner_namespace=MemoryNamespace(agent_id=agent_id),
        source="operator_pin",
        created_at=now,
        last_updated_at=now,
        last_updated_by="operator",
    )


def _budgets(*, total: int = 2048, memory_tokens: int = 256) -> ContextBudgets:
    return ContextBudgets(
        total_max_tokens=total,
        identity_tokens=120,
        summary_tokens=120,
        conversation_summary_tokens=80,
        active_plan_tokens=80,
        task_digest_tokens=60,
        recent_turn_tokens=220,
        facts_tokens=120,
        memory_tokens=memory_tokens,
        skills_tokens=80,
        artifact_tokens=120,
        instructions_tokens=120,
    )


def test_prompt_block_kind_matches_context_segment_buckets() -> None:
    assert set(get_args(PromptBlockKind)) == set(get_args(SegmentBucket))


def test_retrieval_and_turn_input_neighbors_accept_without_optional_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_empty_tool_bundle(monkeypatch)
    service = ContextCtlService(
        identityctl=_IdentityStub(),
        sessctl=_SessionStub(),
        memctl=_MemoryStub(
            cards=[
                MemoryCard(
                    record_id="card-1",
                    record_type="note",
                    text="Retrieved context card.",
                    score=0.9,
                )
            ]
        ),
        artifactctl=_ArtifactStub(),
        memory_block_store=None,
        memory_blocks_enabled=False,
    )
    pack = service.build_pack(
        BuildPackRequest(
            session_id="s-neighbor",
            agent_id="agent-1",
            purpose="chat",
            query="check neighbor buckets",
            budgets_override=_budgets(),
        )
    )

    request = _build_request(
        model="fake-model",
        purpose="chat",
        context=pack.model_dump(),
        schema=type("Reply", (), {}),
        temperature=0.0,
    )

    part_kinds = [
        msg.content_parts[0].block_kind for msg in request.messages if msg.content_parts
    ]
    assert "static_prefix" in part_kinds
    assert "retrieval" in part_kinds
    assert part_kinds[-1] == "turn_input"
    assert "self_awareness" not in part_kinds
    assert "memory" not in part_kinds


def test_request_adapter_accepts_memory_and_self_awareness_pack_with_metadata_and_budgets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_empty_tool_bundle(monkeypatch)
    store = SophiaGraphMemoryStore()
    store.put_memory_block(
        _memory_block(
            "blk-1",
            class_name="agent_identity",
            mode="pinned",
            content="First durable identity rule.",
        )
    )
    store.put_memory_block(
        _memory_block(
            "blk-2",
            class_name="session_pin",
            mode="read_only",
            content="Second read-only session pin.",
        )
    )
    store.put_memory_block(
        _memory_block(
            "blk-shared",
            class_name="session_pin",
            mode="shared",
            content="Inactive shared mode block.",
        )
    )
    service = ContextCtlService(
        identityctl=_IdentityStub(),
        sessctl=_SessionStub(
            recent_turns=[
                SessionTurn(turn_id="t1", role="user", content="earlier question"),
                SessionTurn(turn_id="t2", role="assistant", content="earlier answer"),
            ]
        ),
        memctl=_MemoryStub(
            cards=[
                MemoryCard(
                    record_id="card-1",
                    record_type="note",
                    text="Supporting retrieval neighbor.",
                    score=0.85,
                )
            ]
        ),
        artifactctl=_ArtifactStub(),
        memory_block_store=store,
        memory_blocks_enabled=True,
    )
    pack = service.build_pack(
        BuildPackRequest(
            session_id="s-full",
            agent_id="agent-1",
            purpose="chat",
            query="current user question",
            budgets_override=_budgets(),
            self_awareness=_self_snapshot().model_dump(mode="json"),
        )
    )

    request = _build_request(
        model="fake-model",
        purpose="chat",
        context=pack.model_dump(),
        schema=type("Reply", (), {}),
        temperature=0.0,
    )

    block_kinds = [
        msg.content_parts[0].block_kind for msg in request.messages if msg.content_parts
    ]
    assert block_kinds.index("self_awareness") < block_kinds.index("recent_window")
    assert block_kinds.index("recent_window") < block_kinds.index("memory")
    assert block_kinds.index("memory") < block_kinds.index("retrieval")
    assert block_kinds.index("retrieval") < block_kinds.index("turn_input")
    assert block_kinds[-1] == "turn_input"

    static_msg = next(
        msg for msg in request.messages if msg.meta.get("block_kind") == "static_prefix"
    )
    assert static_msg.role == "system"
    assert static_msg.cache_control == {"type": "ephemeral"}
    assert static_msg.content_parts[0].cache_eligible is True

    self_msg = next(
        msg
        for msg in request.messages
        if msg.meta.get("block_kind") == "self_awareness"
    )
    assert self_msg.role == "system"
    assert self_msg.cache_control is None
    assert self_msg.content_parts[0].block_kind == "self_awareness"
    assert self_msg.content_parts[0].cache_eligible is False
    assert self_msg.content_parts[0].segment_ids == ["self_awareness"]
    assert "[SELF AWARENESS]" in self_msg.content
    assert "top-secret-token" not in self_msg.content

    mem_msg = next(
        msg for msg in request.messages if msg.meta.get("block_kind") == "memory"
    )
    assert mem_msg.role == "system"
    assert mem_msg.cache_control is None
    assert mem_msg.content_parts[0].block_kind == "memory"
    assert mem_msg.content_parts[0].cache_eligible is False
    assert mem_msg.content_parts[0].segment_ids == [
        "memory-block:blk-1",
        "memory-block:blk-2",
    ]
    assert mem_msg.content_parts[0].refs == [
        "memory-block:blk-1",
        "memory-block:blk-2",
    ]
    assert mem_msg.meta["cache_invalidation_refs"] == [
        "memory-block:blk-1",
        "memory-block:blk-2",
    ]
    assert mem_msg.content.index(
        "First durable identity rule."
    ) < mem_msg.content.index("Second read-only session pin.")
    assert "Inactive shared mode block." not in mem_msg.content
    assert pack.token_budget_report.buckets["memory"].selected_count == 2
    assert pack.token_budget_report.buckets["memory"].dropped_count == 1

    final_msg = request.messages[-1]
    assert final_msg.role == "user"
    assert final_msg.content_parts[0].block_kind == "turn_input"


def test_request_adapter_preserves_tight_budget_protections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_empty_tool_bundle(monkeypatch)
    store = SophiaGraphMemoryStore()
    store.put_memory_block(
        _memory_block(
            "blk-fit",
            mode="pinned",
            content="Short pinned note.",
            token_estimate=12,
        )
    )
    store.put_memory_block(
        _memory_block(
            "blk-overflow",
            mode="read_only",
            content=" ".join(["overflow"] * 240),
            token_estimate=260,
        )
    )
    bucket_tight = ContextBudgets(
        total_max_tokens=1024,
        identity_tokens=60,
        summary_tokens=40,
        conversation_summary_tokens=0,
        active_plan_tokens=0,
        task_digest_tokens=0,
        trailer_feedback_tokens=0,
        recent_turn_tokens=80,
        facts_tokens=32,
        memory_tokens=150,
        skills_tokens=0,
        artifact_tokens=0,
        instructions_tokens=40,
    )
    service = ContextCtlService(
        identityctl=_IdentityStub(),
        sessctl=_SessionStub(),
        memctl=_MemoryStub(),
        artifactctl=_ArtifactStub(),
        memory_block_store=store,
        memory_blocks_enabled=True,
    )
    pack = service.build_pack(
        BuildPackRequest(
            session_id="s-tight-bucket",
            agent_id="agent-1",
            purpose="decide",
            query="tight budget check",
            budgets_override=bucket_tight,
            self_awareness=_self_snapshot().model_dump(mode="json"),
        )
    )

    request = _build_request(
        model="fake-model",
        purpose="decide",
        context=pack.model_dump(),
        schema=type("Reply", (), {}),
        temperature=0.0,
    )

    self_msg = next(
        msg
        for msg in request.messages
        if msg.meta.get("block_kind") == "self_awareness"
    )
    assert self_msg.content_parts[0].block_kind == "self_awareness"
    assert (
        len(self_msg.content)
        <= max(32, bucket_caps_for(bucket_tight)["self_awareness"]) * 8
    )
    mem_msg = next(
        msg for msg in request.messages if msg.meta.get("block_kind") == "memory"
    )
    assert mem_msg.content_parts[0].block_kind == "memory"
    assert "Short pinned note." in mem_msg.content
    assert pack.token_budget_report.buckets["memory"].trim_applied is True

    total_tight = ContextBudgets(
        total_max_tokens=160,
        identity_tokens=20,
        summary_tokens=12,
        conversation_summary_tokens=0,
        active_plan_tokens=0,
        task_digest_tokens=0,
        trailer_feedback_tokens=0,
        recent_turn_tokens=20,
        facts_tokens=8,
        memory_tokens=36,
        skills_tokens=0,
        artifact_tokens=0,
        instructions_tokens=12,
    )
    trimmed_pack = service.build_pack(
        BuildPackRequest(
            session_id="s-tight-total",
            agent_id="agent-1",
            purpose="decide",
            query="tight total cap",
            budgets_override=total_tight,
            self_awareness=_self_snapshot().model_dump(mode="json"),
        )
    )
    trimmed_request = _build_request(
        model="fake-model",
        purpose="decide",
        context=trimmed_pack.model_dump(),
        schema=type("Reply", (), {}),
        temperature=0.0,
    )
    trimmed_kinds = [
        msg.content_parts[0].block_kind
        for msg in trimmed_request.messages
        if msg.content_parts
    ]
    assert "self_awareness" in trimmed_kinds
    assert trimmed_kinds[-1] == "turn_input"


def test_brain_build_context_forwards_self_awareness_hint_without_filtering() -> None:
    service = ContextCtlService(
        identityctl=_IdentityStub(),
        sessctl=_SessionStub(),
        memctl=_MemoryStub(),
        artifactctl=_ArtifactStub(),
    )
    adapter = ContextCtlAdapter(service=service)
    emitted: list[tuple[str, dict[str, Any]]] = []

    class _SessionSink:
        @staticmethod
        def append_event(
            session_id: str, event_type: str, payload: dict[str, Any], **kwargs: Any
        ) -> str:
            del session_id, kwargs
            emitted.append((event_type, dict(payload)))
            return "evt-sink"

    class _Runner:
        context_api = adapter
        session_api = None
        profile = None
        options = None
        llm_api = None
        meta_engine = None

        @staticmethod
        def _emit_brain_operation(**kwargs: Any) -> None:
            del kwargs

        @staticmethod
        def _validate_call_order(*args: Any) -> dict[str, Any]:
            del args
            return {"valid": True, "reason": ""}

    logger = CanonicalEventLogger(
        session_api=_SessionSink(),
        session_id="s-hint",
        agent_id="agent-1",
    )
    state = WorkingState(
        session_id="s-hint",
        agent_id="agent-1",
        budgets_remaining=BudgetCounters(
            ticks=4,
            tool_calls=2,
            a2a_calls=0,
            tokens=512,
            time_ms=5000,
        ),
        trace_id="trace-hint",
        goal="check self awareness",
    )

    context = build_context(
        cast(Any, _Runner()),
        state=state,
        purpose="decide",
        budget={"max_tokens": 512},
        hints={
            "user_input": "what are you?",
            "self_awareness": _self_snapshot("agent-1").model_dump(mode="json"),
            "unexpected_private_hint": "drop-me",
        },
        logger=logger,
    )

    filtered_events = [
        data
        for event_type, data in emitted
        if event_type == "context.phase_hints.filtered"
    ]
    assert filtered_events == [
        {"purpose": "decide", "dropped_keys": ["unexpected_private_hint"]}
    ]
    rendered = _messages_from_context(context)
    kinds = [msg.content_parts[0].block_kind for msg in rendered if msg.content_parts]
    assert "self_awareness" in kinds
    assert kinds.index("self_awareness") < kinds.index("turn_input")


@pytest.fixture
def local_openai_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[APIRuntime, list[dict[str, Any]]]:
    for key in _OVERRIDE_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    root = tmp_path / "root"
    monkeypatch.chdir(workspace)

    requests: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            content_len = int(self.headers.get("Content-Length", "0"))
            request = json.loads(self.rfile.read(content_len))
            requests.append(request)
            body = {
                "id": "local-483-response",
                "object": "chat.completion",
                "model": request["model"],
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "acknowledged",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 8,
                    "completion_tokens": 2,
                    "total_tokens": 10,
                },
            }
            encoded = json.dumps(body).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *_args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("LOCAL_SDK_KEY_483", "dummy-local-483")

    config = OpenMinionConfig()
    _csc_install_default_agent(
        config,
        provider="openai",
        provider_config_overrides={"model": "local-483-model"},
    )
    config.providers.openai.api_key_env = "LOCAL_SDK_KEY_483"
    config.providers.openai.base_url = f"http://127.0.0.1:{server.server_port}/v1"
    config.runtime.process_mode = "single-process"
    config.runtime.daemon_auto_start = False
    config.storage.path = str(root / "data" / "state.db")
    config_path = root / "agents.json"
    save_config(config, str(config_path))

    runtime = APIRuntime.from_config_path(
        str(config_path),
        home_root=root,
        data_root=root / "data",
    )
    try:
        yield runtime, requests
    finally:
        runtime.close()
        server.shutdown()
        server.server_close()
        thread.join()


def test_agent_run_accepts_memory_blocks_and_self_awareness_snapshot(
    local_openai_runtime: tuple[APIRuntime, list[dict[str, Any]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, requests = local_openai_runtime

    with Agent(runtime=runtime, session_id="control-483") as control_agent:
        control_result = control_agent.run("hello control")
    assert control_result.run_state == "completed"
    assert requests
    requests.clear()

    runner = runtime.agent._get_runner()
    context_service = runner.context_api.service
    snapshot = runtime.runtime_self_model()
    snapshot["identity"]["facts"]["api_token"] = "SECRET_SNAPSHOT_TOKEN_483"

    store = SophiaGraphMemoryStore()
    store.put_memory_block(
        _memory_block(
            "blk-sdk-483",
            agent_id=str(snapshot["agent_id"]),
            mode="pinned",
            content="MEMORY_MARKER_483: cite exact test receipts.",
        )
    )
    context_service._memory_block_store = store
    context_service._memory_blocks_enabled = True

    original_build_context = brain_runtime_context.build_context

    def _build_with_self_awareness(
        active_runner: Any,
        *,
        state: Any,
        purpose: str,
        budget: dict[str, Any],
        hints: dict[str, Any] | None,
        logger: Any,
        mode_name: str | None = None,
    ) -> dict[str, Any]:
        enriched_hints = dict(hints or {})
        enriched_hints.setdefault("self_awareness", snapshot)
        return original_build_context(
            active_runner,
            state=state,
            purpose=purpose,
            budget=budget,
            hints=enriched_hints,
            logger=logger,
            mode_name=mode_name,
        )

    monkeypatch.setattr(
        brain_runtime_context, "build_context", _build_with_self_awareness
    )
    monkeypatch.setitem(
        runner_delegates.RUNNER_DELEGATES,
        "_build_context",
        _build_with_self_awareness,
    )

    with Agent(runtime=runtime, session_id="blocks-483") as agent:
        first = agent.run("first turn with blocks")
        second = agent.run("second turn with history")

    assert first.run_state == "completed"
    assert second.run_state == "completed"
    assert len(requests) >= 2

    matching_requests = [
        req
        for req in requests
        if any(
            msg.get("role") == "user"
            and "second turn with history" in str(msg.get("content", ""))
            for msg in req.get("messages", [])
        )
    ]
    assert matching_requests
    outbound = matching_requests[0]["messages"]
    assert outbound[-1]["role"] == "user"
    assert "second turn with history" in str(outbound[-1]["content"])

    system_text = "\n\n".join(
        str(msg.get("content", "")) for msg in outbound if msg.get("role") == "system"
    )
    assert "[SELF AWARENESS]" in system_text
    assert "MEMORY_MARKER_483: cite exact test receipts." in system_text
    assert system_text.index("[SELF AWARENESS]") < system_text.index(
        "MEMORY_MARKER_483: cite exact test receipts."
    )
    assert "SECRET_SNAPSHOT_TOKEN_483" not in json.dumps(matching_requests[0])
