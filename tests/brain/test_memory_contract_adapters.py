from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

from openminion.modules.brain.adapters.factory import (
    RLMBridgeMemoryClient,
    create_context_adapter,
    create_memory_adapter,
)
from openminion.modules.context.schemas import (
    BuildPackRequest,
    RecentSessionArtifactRef,
)
from openminion.modules.memory.models import MemoryRecord
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore


class _SessionStoreWithPath:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path


class _SessionStoreWithoutPath:
    pass


def test_create_memory_adapter_auto_put_and_stage_are_operational() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        adapter = create_memory_adapter(mode="auto", db_path=Path(tmp) / "memory")
        record_id = adapter.put_record(
            scope="session:s1",
            record_type="fact",
            title="orion",
            content={"text": "orion release date"},
        )
        candidate_id = adapter.stage_candidate(
            scope="session:s1",
            record_type="fact",
            title="candidate orion",
            content={"text": "candidate fact"},
        )
        assert record_id.startswith("mem_")
        assert candidate_id.startswith("cand_")


def test_memory_adapter_context_uses_configured_agent_scope() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        adapter = create_memory_adapter(
            mode="auto",
            db_path=Path(tmp) / "memory",
            agent_id="minimax-m2-7",
        )
        adapter.put_record(
            scope="agent:minimax-m2-7",
            record_type="fact",
            title="Email",
            content={"text": "User email is scoped-agent@example.com."},
        )

        context = adapter.build_context(
            session_id="fresh-session",
            user_message="What is my email?",
        )

        assert "scoped-agent@example.com" in context
        assert "Agent canonical memory" in context


def test_create_memory_adapter_auto_apply_outcome_feedback_is_operational() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        adapter = create_memory_adapter(mode="auto", db_path=Path(tmp) / "memory")
        record_id = adapter.put_record(
            scope="session:s1",
            record_type="fact",
            title="orion",
            content={"text": "orion release date"},
        )
        updated = adapter.apply_outcome_feedback(
            record_ids=[record_id, record_id],
            outcome="success",
            command_id="cmd-1",
            observed_at=datetime.now(timezone.utc).isoformat(),
            feedback_delta=0.2,
        )
        stored = adapter.store.get(record_id)

        assert updated == 1
        assert stored is not None
        assert stored.meta["feedback_score"] == 0.2
        assert stored.meta["outcome_success_count"] == 1


def test_local_memory_adapter_apply_outcome_feedback_contract_exists() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        adapter = create_memory_adapter(mode="local", db_path=Path(tmp) / "memory")
        updated = adapter.apply_outcome_feedback(
            record_ids=["mem_a", "mem_a", "mem_b"],
            outcome="failed",
            command_id="cmd-2",
            observed_at=datetime.now(timezone.utc).isoformat(),
            feedback_delta=-0.1,
        )

        assert updated == 2


def test_context_bridge_memory_query_mapping_returns_real_records() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp) / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        session_db = state_dir / "sessions.db"
        memory_db = state_dir / "memory.db"
        memory_store = SQLiteMemoryStore(memory_db)
        memory_service = MemoryService(store=memory_store)
        memory_service.write_record(
            scope="session:sx",
            record_type="fact",
            title="session fact",
            content={"text": "orion session fact"},
        )
        memory_service.write_record(
            scope="agent:ax",
            record_type="fact",
            title="agent fact",
            content={"text": "orion agent fact"},
        )
        now = datetime.now(timezone.utc).isoformat()
        memory_store.put(
            MemoryRecord(
                id="mem_structured_failure_fact",
                scope="agent:ax",
                type="fact",
                title="structured failure fact",
                content={"text": "orion tool failure fact"},
                created_at=now,
                updated_at=now,
                tags=["tool_failure"],
                meta={
                    "source_kind": "tool_outcome",
                    "source_negative_outcome": True,
                    "source_outcome_status": "failure",
                    "source_tool_name": "web.search",
                },
            )
        )

        ctx_adapter = create_context_adapter(
            mode="auto",
            session_store=_SessionStoreWithPath(session_db),
        )
        memctl = ctx_adapter.service._memctl  # noqa: SLF001
        facts = memctl.query_facts(
            session_id="sx",
            agent_id="ax",
            query="orion",
            limit=5,
        )
        cards = memctl.query_memory_cards(
            session_id="sx",
            agent_id="ax",
            query="orion",
            limit=5,
        )

        assert facts
        assert any("orion" in item.text.lower() for item in facts)
        assert all(not str(item.record_id).startswith("degraded:") for item in facts)
        structured = next(
            item for item in facts if item.record_id == "mem_structured_failure_fact"
        )
        assert structured.tags == ["tool_failure"]
        assert structured.meta["source_negative_outcome"] is True
        assert structured.meta["source_tool_name"] == "web.search"
        assert cards
        assert any("orion" in item.text.lower() for item in cards)
        assert all(not str(item.record_id).startswith("degraded:") for item in cards)


def test_srtf_prompt_pack_omits_structured_failure_facts_only() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp) / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        session_db = state_dir / "sessions.db"
        memory_db = state_dir / "memory.db"
        memory_store = SQLiteMemoryStore(memory_db)
        now = datetime.now(timezone.utc).isoformat()

        memory_store.put(
            MemoryRecord(
                id="mem_structured_failure_fact",
                scope="session:srtf-s1",
                type="fact",
                title="structured tool failure",
                content={"text": "Unknown tool: weather.search"},
                created_at=now,
                updated_at=now,
                tags=["tool_failure"],
                meta={
                    "source_kind": "tool_outcome",
                    "source_negative_outcome": True,
                    "source_outcome_status": "failure",
                    "source_tool_name": "weather.search",
                },
                confidence=0.95,
            )
        )
        memory_store.put(
            MemoryRecord(
                id="mem_legacy_text_only",
                scope="session:srtf-s1",
                type="fact",
                title="legacy ambiguous text",
                content={"text": "Unknown tool: weather.lookup"},
                created_at=now,
                updated_at=now,
                confidence=0.9,
            )
        )
        memory_store.put(
            MemoryRecord(
                id="mem_semantic_weather_pref",
                scope="session:srtf-s1",
                type="fact",
                title="semantic weather preference",
                content={"text": "User prefers metric weather reports."},
                created_at=now,
                updated_at=now,
                confidence=0.9,
            )
        )

        ctx_adapter = create_context_adapter(
            mode="auto",
            session_store=_SessionStoreWithPath(session_db),
        )
        pack = ctx_adapter.service.build_pack(
            BuildPackRequest(
                session_id="srtf-s1",
                agent_id="srtf-agent",
                purpose="act",
                query="weather",
            )
        )

        rendered = "\n".join(segment.content for segment in pack.segments)
        assert "Unknown tool: weather.search" not in rendered
        assert "mem_structured_failure_fact" not in pack.context_manifest.facts
        assert "User prefers metric weather reports." in rendered
        assert "mem_semantic_weather_pref" in pack.context_manifest.facts
        # infer operational meaning from prose. SRTF-03 owns diagnostics for
        assert "Unknown tool: weather.lookup" in rendered
        assert "mem_legacy_text_only" in pack.context_manifest.facts


def test_context_bridge_session_start_recall_lists_durable_agent_memory() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp) / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        session_db = state_dir / "sessions.db"
        memory_db = state_dir / "memory.db"
        memory_service = MemoryService(store=SQLiteMemoryStore(memory_db))
        recalled_id = memory_service.write_record(
            scope="agent:ax",
            record_type="user_preference",
            title="C++ style",
            content={"text": "User prefers terse C++ server examples."},
            confidence=0.91,
        )
        memory_service.write_record(
            scope="session:sx",
            record_type="fact",
            title="session fact",
            content={"text": "session-only fact should not be recalled"},
        )

        ctx_adapter = create_context_adapter(
            mode="auto",
            session_store=_SessionStoreWithPath(session_db),
        )
        memctl = ctx_adapter.service._memctl  # noqa: SLF001
        recalled = memctl.recall_session_start_memory(
            session_id="fresh-session",
            agent_id="ax",
            query="server examples",
            turn_index=0,
            limit=5,
        )
        later = memctl.recall_session_start_memory(
            session_id="fresh-session",
            agent_id="ax",
            query="server examples",
            turn_index=1,
            limit=5,
        )

        assert [item.record_id for item in recalled] == [recalled_id]
        assert recalled[0].record_type == "user_preference"
        assert "terse C++" in recalled[0].text
        assert later == []


def test_context_bridge_session_start_recall_lists_plan_snapshot_records() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp) / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        session_db = state_dir / "sessions.db"
        memory_db = state_dir / "memory.db"
        memory_service = MemoryService(store=SQLiteMemoryStore(memory_db))
        recalled_id = memory_service.write_record(
            scope="agent:ax",
            record_type="plan_snapshot",
            title="plan_snapshot:sess-a:session_ended",
            content={
                "plan_steps": [{"step_id": "cmd-2", "status": "in_progress"}],
                "intent_states": [{"intent_id": "intent-1", "status": "pending"}],
                "last_work_summary": "Continue the pytest migration.",
                "incomplete_reason": "session_ended",
                "session_id": "sess-a",
                "turn_index": 3,
                "text": '{"incomplete_reason": "session_ended", "plan_steps": [{"step_id": "cmd-2", "status": "in_progress"}]}',
            },
            confidence=0.9,
        )

        ctx_adapter = create_context_adapter(
            mode="auto",
            session_store=_SessionStoreWithPath(session_db),
        )
        memctl = ctx_adapter.service._memctl  # noqa: SLF001
        recalled = memctl.recall_session_start_memory(
            session_id="fresh-session",
            agent_id="ax",
            query="pytest migration",
            turn_index=0,
            limit=5,
        )

        assert [item.record_id for item in recalled] == [recalled_id]
        assert recalled[0].record_type == "plan_snapshot"
        assert "session_ended" in recalled[0].text


def test_context_bridge_session_start_recall_lists_meta_rule_preferences() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp) / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        session_db = state_dir / "sessions.db"
        memory_db = state_dir / "memory.db"
        memory_service = MemoryService(store=SQLiteMemoryStore(memory_db))
        recalled_id = memory_service.write_record(
            scope="agent:ax",
            record_type="meta_rule_preference",
            title="meta_rule_preference:search_retry_count:3",
            content={
                "rule": "search_retry_count",
                "preferred_value": 3,
                "reasoning": "Broad web queries often need more retries.",
                "text": (
                    "rule=search_retry_count preferred_value=3 "
                    "reasoning=Broad web queries often need more retries."
                ),
            },
            confidence=0.84,
        )

        ctx_adapter = create_context_adapter(
            mode="auto",
            session_store=_SessionStoreWithPath(session_db),
        )
        memctl = ctx_adapter.service._memctl  # noqa: SLF001
        recalled = memctl.recall_session_start_memory(
            session_id="fresh-session",
            agent_id="ax",
            query="search retry count",
            turn_index=0,
            limit=5,
        )

        assert [item.record_id for item in recalled] == [recalled_id]
        assert recalled[0].record_type == "meta_rule_preference"
        assert "search_retry_count" in recalled[0].text
        assert "preferred_value=3" in recalled[0].text


def test_context_bridge_session_start_retry_guidance_prefers_matching_correction() -> (
    None
):
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp) / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        session_db = state_dir / "sessions.db"
        memory_db = state_dir / "memory.db"
        memory_service = MemoryService(store=SQLiteMemoryStore(memory_db))
        now = datetime.now(timezone.utc).isoformat()
        file_read_id = memory_service._store.put(  # noqa: SLF001
            MemoryRecord(
                id="mem-file-read-correction",
                scope="agent:ax",
                type="correction",
                title="Correction for file.read",
                content={
                    "text": "Before retrying file.read, verify the path exists first.",
                    "tool_name": "file.read",
                    "args_signature": '{"path":"/tmp/demo.txt"}',
                },
                created_at=now,
                updated_at=now,
                confidence=0.84,
                tags=["failure_path", "correction"],
                meta={
                    "source_failure_path": True,
                    "source_tool_name": "file.read",
                    "source_args_signature": '{"path":"/tmp/demo.txt"}',
                },
            )
        )
        weather_id = memory_service._store.put(  # noqa: SLF001
            MemoryRecord(
                id="mem-weather-search-correction",
                scope="agent:ax",
                type="correction",
                title="Correction for weather.search",
                content={
                    "text": "Before retrying weather.search, verify the location input first.",
                    "tool_name": "weather.search",
                    "args_signature": '{"location":"sf"}',
                },
                created_at=now,
                updated_at=now,
                confidence=0.85,
                tags=["failure_path", "correction"],
                meta={
                    "source_failure_path": True,
                    "source_tool_name": "weather.search",
                    "source_args_signature": '{"location":"sf"}',
                },
            )
        )

        ctx_adapter = create_context_adapter(
            mode="auto",
            session_store=_SessionStoreWithPath(session_db),
        )
        memctl = ctx_adapter.service._memctl  # noqa: SLF001
        recalled = memctl.recall_session_start_memory(
            session_id="fresh-session",
            agent_id="ax",
            query="What correction should I follow before retrying weather.search?",
            turn_index=0,
            limit=5,
        )

        assert recalled
        assert recalled[0].record_id == weather_id
        assert file_read_id in {item.record_id for item in recalled}
        assert "weather.search" in recalled[0].text


def test_context_bridge_session_start_recall_prefers_semantic_session_summary_match() -> (
    None
):
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp) / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        session_db = state_dir / "sessions.db"
        memory_db = state_dir / "memory.db"
        memory_service = MemoryService(store=SQLiteMemoryStore(memory_db))
        relevant_id = memory_service.write_record(
            scope="agent:ax",
            record_type="session_summary",
            title="artifact store decision",
            content={
                "summary_text": "We chose Postgres for the artifact store migration.",
                "decisions": ["Use Postgres for the artifact store."],
                "open_questions": ["Who will own the backfill runbook?"],
                "corrections": ["SQLite is only for local smoke tests."],
                "topic_keywords": ["artifact", "postgres"],
                "turn_count": 6,
            },
            confidence=0.91,
        )
        memory_service.write_record(
            scope="agent:ax",
            record_type="session_summary",
            title="travel planning",
            content={
                "summary_text": "Booked the New York City hotel itinerary.",
                "decisions": ["Stay near Midtown."],
                "open_questions": [],
                "corrections": [],
                "topic_keywords": ["travel"],
                "turn_count": 5,
            },
            confidence=0.7,
        )

        ctx_adapter = create_context_adapter(
            mode="auto",
            session_store=_SessionStoreWithPath(session_db),
        )
        memctl = ctx_adapter.service._memctl  # noqa: SLF001
        recalled = memctl.recall_session_start_memory(
            session_id="fresh-session",
            agent_id="ax",
            query="What did we decide about the artifact store postgres migration?",
            turn_index=0,
            limit=5,
        )

        assert recalled
        assert recalled[0].record_id == relevant_id
        assert "Most relevant prior session:" in recalled[0].text
        assert "Prior decisions:" in recalled[0].text
        assert "Prior corrections:" in recalled[0].text


def test_context_bridge_mid_session_recall_uses_typed_state_query() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp) / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        session_db = state_dir / "sessions.db"
        memory_db = state_dir / "memory.db"
        memory_service = MemoryService(store=SQLiteMemoryStore(memory_db))
        recalled_id = memory_service.write_record(
            scope="agent:ax",
            record_type="fact",
            title="pytest-migration",
            content={"text": "pytest-migration follow-up note"},
            confidence=0.89,
        )

        ctx_adapter = create_context_adapter(
            mode="auto",
            session_store=_SessionStoreWithPath(session_db),
        )
        memctl = ctx_adapter.service._memctl  # noqa: SLF001
        recalled = memctl.recall_mid_session_memory(
            session_id="mid-session",
            agent_id="ax",
            turn_index=6,
            latest_user_message="What next for pytest migration?",
            intent_ids=["pytest-migration"],
            intent_statuses=["active"],
            active_skill_id="python-tests",
            resolved_skill_ids=["python-tests"],
            plan_cursor=2,
            plan_step_ids=["cmd-2"],
            recent_tool_families=["file"],
            limit=5,
        )

        assert [item.record_id for item in recalled] == [recalled_id]
        assert recalled[0].record_type == "fact"
        assert "pytest-migration" in recalled[0].text


def test_context_bridge_recent_session_artifacts_list_durable_agent_records() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp) / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        session_db = state_dir / "sessions.db"
        memory_db = state_dir / "memory.db"
        memory_service = MemoryService(store=SQLiteMemoryStore(memory_db))
        recent_id = memory_service.write_record(
            scope="agent:ax",
            record_type="artifact_digest",
            title="artifact_digest:auth.py",
            content={
                "artifact_type": "file",
                "artifact_path": "/workspace/auth.py",
                "artifact_digest": "sha256:abc123",
                "session_id": "sess-prev",
                "turn_index": 4,
                "tool_name": "file.write",
            },
            confidence=0.9,
        )
        memory_service.write_record(
            scope="agent:ax",
            record_type="artifact_digest",
            title="artifact_digest:current.py",
            content={
                "artifact_type": "file",
                "artifact_path": "/workspace/current.py",
                "artifact_digest": "sha256:def456",
                "session_id": "fresh-session",
                "turn_index": 1,
                "tool_name": "file.write",
            },
        )

        ctx_adapter = create_context_adapter(
            mode="auto",
            session_store=_SessionStoreWithPath(session_db),
        )
        memctl = ctx_adapter.service._memctl  # noqa: SLF001
        recalled = memctl.recall_recent_session_artifacts(
            session_id="fresh-session",
            agent_id="ax",
            max_results=5,
            max_session_age=14,
        )

        assert recalled == [
            RecentSessionArtifactRef(
                record_id=recent_id,
                artifact_type="file",
                artifact_path="/workspace/auth.py",
                artifact_digest="sha256:abc123",
                session_id="sess-prev",
                turn_index=4,
                tool_name="file.write",
            )
        ]


def test_context_bridge_memory_query_degraded_marker_when_backend_unavailable() -> None:
    ctx_adapter = create_context_adapter(
        mode="auto",
        session_store=_SessionStoreWithoutPath(),
    )
    memctl = ctx_adapter.service._memctl  # noqa: SLF001
    facts = memctl.query_facts(
        session_id="s1",
        agent_id="a1",
        query="anything",
        limit=5,
    )
    cards = memctl.query_memory_cards(
        session_id="s1",
        agent_id="a1",
        query="anything",
        limit=5,
    )
    assert facts
    assert cards
    assert str(facts[0].record_id).startswith("degraded:")
    assert str(cards[0].record_id).startswith("degraded:")
    assert (
        memctl.recall_session_start_memory(
            session_id="s1",
            agent_id="a1",
            query="anything",
            turn_index=0,
            limit=5,
        )
        == []
    )
    assert (
        memctl.recall_mid_session_memory(
            session_id="s1",
            agent_id="a1",
            turn_index=4,
            latest_user_message="anything",
            intent_ids=["pytest-migration"],
            intent_statuses=["active"],
            active_skill_id="python-tests",
            resolved_skill_ids=["python-tests"],
            plan_cursor=1,
            plan_step_ids=["cmd-1"],
            recent_tool_families=["file"],
            limit=5,
        )
        == []
    )


def test_rlm_bridge_memory_client_supports_retrieve_query_and_stage() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        memory_api = create_memory_adapter(mode="auto", db_path=Path(tmp) / "memory")
        memory_api.put_record(
            scope="session:rlm-s1",
            record_type="fact",
            title="rlm fact",
            content={"text": "rlm retrieval fact"},
        )
        bridge = RLMBridgeMemoryClient(memory_api)
        rows = bridge.retrieve(
            session_id="rlm-s1",
            agent_id="rlm-a1",
            query="retrieval",
            k=5,
            filters=None,
        )
        facts = bridge.query_facts(
            session_id="rlm-s1",
            agent_id="rlm-a1",
            query="retrieval",
            limit=5,
        )
        candidate_id = bridge.stage_candidate(
            scope="session:rlm-s1",
            record_type="fact",
            title="candidate",
            content={"text": "candidate fact"},
        )

        assert rows
        assert facts
        assert str(candidate_id).startswith(("cand_", "mem_"))


def test_stage_candidate_preserves_confidence_and_meta_for_auto_adapter() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        adapter = create_memory_adapter(mode="auto", db_path=Path(tmp) / "memory")
        candidate_id = adapter.stage_candidate(
            scope="agent:auto-agent",
            record_type="procedure",
            title="Procedure candidate",
            content={"text": "reuse this"},
            confidence=0.91,
            meta={"source_success_path": True},
        )

        candidate = adapter.store.candidate_get(candidate_id)
        assert candidate is not None
        assert candidate.confidence == 0.91
        assert candidate.meta["source_success_path"] is True


def test_stage_candidate_preserves_confidence_and_meta_for_local_adapter() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        adapter = create_memory_adapter(mode="local", db_path=Path(tmp) / "memory")
        candidate_id = adapter.stage_candidate(
            scope="agent:local-agent",
            record_type="tool_habit",
            title="Local tool habit",
            content={"tool": "weather"},
            confidence=0.83,
            meta={"source_success_path": True},
        )

        payloads = (
            (Path(tmp) / "memory" / "memory.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        )
        assert payloads
        assert candidate_id.startswith("cand_")
        assert '"confidence": 0.83' in payloads[-1]
        assert '"source_success_path": true' in payloads[-1].lower()


def test_stage_candidate_accepts_tool_outcome_record_type_for_auto_adapter() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        adapter = create_memory_adapter(mode="auto", db_path=Path(tmp) / "memory")
        candidate_id = adapter.stage_candidate(
            scope="agent:auto-agent",
            record_type="tool_outcome",
            title="tool_outcome:web.search:failure",
            content={
                "tool_name": "web.search",
                "tool_family": "web",
                "outcome": "failure",
                "error_code": "PROVIDER_TIMEOUT",
                "turn_index": 0,
                "intent_id": "intent-1",
                "artifact_ref": None,
            },
            confidence=0.7,
            meta={"source_negative_outcome": True},
        )

        candidate = adapter.store.candidate_get(candidate_id)
        assert candidate is not None
        assert candidate.type == "tool_outcome"
        assert candidate.meta["source_negative_outcome"] is True


def test_stage_candidate_accepts_meta_rule_preference_record_type_for_auto_adapter() -> (
    None
):
    with tempfile.TemporaryDirectory() as tmp:
        adapter = create_memory_adapter(mode="auto", db_path=Path(tmp) / "memory")
        candidate_id = adapter.stage_candidate(
            scope="agent:auto-agent",
            record_type="meta_rule_preference",
            title="meta_rule_preference:search_retry_count:3",
            content={
                "rule": "search_retry_count",
                "preferred_value": 3,
                "reasoning": "Broad web queries often need more retries.",
                "text": (
                    "rule=search_retry_count preferred_value=3 "
                    "reasoning=Broad web queries often need more retries."
                ),
            },
            confidence=0.7,
            meta={"source_meta_rule_preference": True},
        )

        candidate = adapter.store.candidate_get(candidate_id)
        assert candidate is not None
        assert candidate.type == "meta_rule_preference"
        assert candidate.meta["source_meta_rule_preference"] is True


def test_memory_procedure_lookup_returns_none_for_missing_id() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        memory_api = create_memory_adapter(mode="auto", db_path=Path(tmp) / "memory")
        payload = memory_api.get_procedure(procedure_id="proc-demo")
        assert payload is None
        # Negative: the unsupported dict shape is no longer emitted anywhere.
        assert not isinstance(payload, dict)

        ctx_adapter = create_context_adapter(
            mode="auto",
            session_store=_SessionStoreWithPath(Path(tmp) / "state" / "sessions.db"),
        )
        memctl = ctx_adapter.service._memctl  # noqa: SLF001
        # Bridge surfaces the same honest not-found.
        assert memctl.get_procedure(procedure_id="proc-demo") is None


def test_memory_procedure_lookup_returns_typed_record_when_present() -> None:
    from openminion.modules.memory.contracts.types import MemoryProcedure
    from openminion.modules.memory.models import MemoryRecord
    from datetime import datetime, timezone

    with tempfile.TemporaryDirectory() as tmp:
        memory_api = create_memory_adapter(mode="auto", db_path=Path(tmp) / "memory")
        service = memory_api._backend  # noqa: SLF001
        store = service._store  # noqa: SLF001
        now = datetime.now(timezone.utc).isoformat()
        record = MemoryRecord(
            id="proc-real-1",
            scope="agent:openminion",
            type="procedure",
            content={
                "steps": ["step-a", "step-b"],
                "preflight": ["pre-a"],
                "rollback_hint": "undo",
            },
            created_at=now,
            updated_at=now,
            title="real procedure",
        )
        store.put(record)

        payload = memory_api.get_procedure(procedure_id="proc-real-1")
        assert isinstance(payload, MemoryProcedure)
        assert payload.procedure_id == "proc-real-1"
        assert payload.title == "real procedure"
        assert payload.steps == ["step-a", "step-b"]
        assert payload.preflight == ["pre-a"]
        assert payload.rollback_hint == "undo"


def test_memory_procedure_bridge_passes_through_typed_record() -> None:
    from openminion.modules.memory.contracts.types import MemoryProcedure
    from openminion.modules.brain.adapters.context.bridges.memory import (
        BridgeMemoryClient,
    )
    from unittest.mock import MagicMock

    procedure = MemoryProcedure(
        procedure_id="proc-bridge-1",
        title="bridge procedure",
        steps=["x", "y"],
        preflight=[],
        rollback_hint="",
    )
    fake_memory_ctl = MagicMock()
    fake_memory_ctl.get_procedure.return_value = procedure

    bridge = BridgeMemoryClient(backing_store=MagicMock())
    bridge._memory_ctl = fake_memory_ctl  # noqa: SLF001 — short-circuit lazy build

    result = bridge.get_procedure(procedure_id="proc-bridge-1")
    assert result is procedure
    fake_memory_ctl.get_procedure.assert_called_once_with(procedure_id="proc-bridge-1")

    # Negative path: when the underlying service returns None, the bridge
    # surfaces None (not an unsupported dict).
    fake_memory_ctl.get_procedure.reset_mock()
    fake_memory_ctl.get_procedure.return_value = None
    assert bridge.get_procedure(procedure_id="missing") is None


def test_bridge_memory_client_renders_improvement_note_records_structurally() -> None:
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from openminion.modules.brain.adapters.context.bridges.memory import (
        BridgeMemoryClient,
    )

    bridge = BridgeMemoryClient(backing_store=MagicMock())
    record = SimpleNamespace(
        record_id="note-1",
        record_type="improvement_note",
        content={
            "status": "active",
            "tool_slugs": ["weather-openmeteo-current"],
            "error_slugs": ["missing-city"],
            "guidance": "Validate args before retrying.",
        },
        meta={},
        score=0.8,
        source="self_improvement",
        tags=["tool:weather-openmeteo-current"],
    )

    card = bridge._memory_card_from_record(record)  # noqa: SLF001

    assert card is not None
    assert card.record_type == "improvement_note"
    assert "tool_slugs=weather-openmeteo-current" in card.text
    assert "guidance=Validate args before retrying." in card.text


def test_bridge_memory_client_renders_session_summary_lists_cleanly() -> None:
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from openminion.modules.brain.adapters.context.bridges.memory import (
        BridgeMemoryClient,
    )

    bridge = BridgeMemoryClient(backing_store=MagicMock())
    record = SimpleNamespace(
        record_id="summary-1",
        record_type="session_summary",
        content={
            "summary_text": "Wrapped up the deploy checks.",
            "decisions": ["ship", " ", "", "watch metrics"],
            "corrections": ["re-run lint", None, ""],
            "open_questions": ["need live smoke?", "   "],
        },
        meta={},
        score=0.4,
        source="session_runtime",
        tags=[],
    )

    card = bridge._memory_card_from_record(record)  # noqa: SLF001

    assert card is not None
    assert "Prior decisions: ship | watch metrics" in card.text
    assert "Prior corrections: re-run lint" in card.text
    assert "Open questions from earlier: need live smoke?" in card.text


def test_bridge_memory_client_renders_strategy_outcome_records_structurally() -> None:
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from openminion.modules.brain.adapters.context.bridges.memory import (
        BridgeMemoryClient,
    )

    bridge = BridgeMemoryClient(backing_store=MagicMock())
    record = SimpleNamespace(
        record_id="so-1",
        record_type="strategy_outcome",
        content={
            "strategy_id": "research",
            "capability_category": "live_information",
            "intent_category": "latest_news",
            "outcome_status": "success",
            "termination_reason": "",
        },
        meta={},
        score=0.8,
        source="brain_runtime",
        tags=["strategy_outcome", "strategy_id:research"],
    )

    card = bridge._memory_card_from_record(record)  # noqa: SLF001

    assert card is not None
    assert card.record_type == "strategy_outcome"
    assert "strategy_id=research" in card.text
    assert "capability_category=live_information" in card.text
    assert "intent_category=latest_news" in card.text
    assert "outcome_status=success" in card.text
