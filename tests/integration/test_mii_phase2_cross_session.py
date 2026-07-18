from __future__ import annotations

from pathlib import Path

from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.models import MemoryCandidate
from openminion.modules.memory.storage.base import (
    CandidateListOptions,
    ListQueryOptions,
)
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore
from openminion.modules.brain.adapters.factory import create_context_adapter
from openminion.modules.context.schemas import (
    BuildPackRequest,
    ContextManifest,
    SessionSlice,
    SessionToolEvent,
    SessionTurn,
)
from openminion.modules.storage.runtime.migrations import migrate_database
from openminion.modules.storage.runtime.session_store import SessionStore
from openminion.modules.storage.runtime.sqlite import connect_database
from openminion.modules.session.storage.store import (
    SQLiteSessionStore as BrainSessionStore,
)
from openminion.services.agent.memory.gateway_adapter import MemoryServiceGatewayAdapter
from openminion.services.context.session import SessionContextService


def _session_summary_structurer(
    summary_text: str, turn_count: int
) -> dict[str, object]:
    del turn_count
    lowered = summary_text.lower()
    decisions: list[str] = []
    corrections: list[str] = []
    open_questions: list[str] = []
    topic_keywords: list[str] = []
    if "postgres" in lowered and "artifact store" in lowered:
        decisions.append("Use Postgres for the artifact store.")
        corrections.append("SQLite is only for local smoke tests.")
        open_questions.append("Who owns the backfill runbook?")
        topic_keywords.extend(["artifact", "postgres"])
    elif "midtown" in lowered or "hotel" in lowered:
        decisions.append("Stay near Midtown.")
        topic_keywords.extend(["travel", "hotel"])
    return {
        "outcome": "succeeded",
        "summary_text": summary_text,
        "decisions": decisions,
        "open_questions": open_questions,
        "corrections": corrections,
        "topic_keywords": topic_keywords,
    }


class _SessionStoreWithPath:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path


class _StaticSessionClient:
    contract_version = "v1"

    def __init__(self, session_slice: SessionSlice) -> None:
        self._session_slice = session_slice

    def get_slice(self, *, session_id: str, purpose: str, limits: dict[str, int]):
        del session_id, purpose, limits
        return self._session_slice


def test_phase2_cross_session_handoff_with_real_sqlite(tmp_path: Path) -> None:
    state_db = tmp_path / "state" / "openminion.db"
    memory_db = tmp_path / "state" / "memory.db"
    migrate_database(state_db)
    connection = connect_database(state_db)
    sessions = SessionStore(connection)
    session_context = SessionContextService(
        sessions,
        keep_recent_messages=1,
        max_compact_per_turn=100,
    )
    memory_service = MemoryService(store=SQLiteMemoryStore(memory_db))
    adapter = MemoryServiceGatewayAdapter(
        memory_service,
        agent_id="phase2-agent",
        session_context=session_context,
        session_summary_max_chars=120,
        session_handoff_max_summaries=5,
    )

    try:
        session_a = sessions.resolve_session(
            agent_id="phase2-agent",
            channel="console",
            target="session-a",
        )
        sessions.append_message(
            session_id=session_a.id,
            role="inbound",
            body="We decided to use pytest for unit coverage.",
        )
        sessions.append_message(
            session_id=session_a.id,
            role="outbound",
            body="Great, let's use pytest.",
        )
        sessions.append_message(
            session_id=session_a.id,
            role="inbound",
            body="Actually, wrong fixture scope for the db.",
        )
        session_context.on_session_close(session_id=session_a.id)

        session_b = sessions.resolve_session(
            agent_id="phase2-agent",
            channel="console",
            target="session-b",
        )
        first_context, _ = adapter.build_context_with_metadata(
            session_id=session_b.id,
            user_message="hello",
        )
        assert "Continuing from recent sessions" in first_context
        assert "pytest" in first_context.lower()

        sessions.append_message(
            session_id=session_b.id,
            role="inbound",
            body="new session follow-up",
        )
        second_context, _ = adapter.build_context_with_metadata(
            session_id=session_b.id,
            user_message="another turn",
        )
        assert "Continuing from recent sessions" not in second_context
    finally:
        connection.close()


def test_cross_session_semantic_recall_surfaces_relevant_structured_summary(
    tmp_path: Path,
) -> None:
    state_db = tmp_path / "state" / "openminion.db"
    memory_db = tmp_path / "state" / "memory.db"
    migrate_database(state_db)
    connection = connect_database(state_db)
    sessions = SessionStore(connection)
    session_context = SessionContextService(
        sessions,
        keep_recent_messages=1,
        max_compact_per_turn=100,
    )
    memory_service = MemoryService(store=SQLiteMemoryStore(memory_db))
    adapter = MemoryServiceGatewayAdapter(
        memory_service,
        agent_id="phase2-agent",
        session_context=session_context,
        session_summary_max_chars=200,
        session_handoff_max_summaries=5,
        session_summary_structurer=_session_summary_structurer,
    )

    try:
        storage_session = sessions.resolve_session(
            agent_id="phase2-agent",
            channel="console",
            target="storage",
        )
        sessions.append_message(
            session_id=storage_session.id,
            role="inbound",
            body="We decided to use Postgres for the artifact store migration.",
        )
        sessions.append_message(
            session_id=storage_session.id,
            role="outbound",
            body="Okay, Postgres for the artifact store.",
        )
        sessions.append_message(
            session_id=storage_session.id,
            role="inbound",
            body="Actually SQLite is only for local smoke tests.",
        )
        session_context.on_session_close(session_id=storage_session.id)

        travel_session = sessions.resolve_session(
            agent_id="phase2-agent",
            channel="console",
            target="travel",
        )
        sessions.append_message(
            session_id=travel_session.id,
            role="inbound",
            body="We booked the hotel and decided to stay near Midtown.",
        )
        session_context.on_session_close(session_id=travel_session.id)

        fresh_session = sessions.resolve_session(
            agent_id="phase2-agent",
            channel="console",
            target="fresh",
        )
        rendered, _ = adapter.build_context_with_metadata(
            session_id=fresh_session.id,
            user_message="What did we decide about the artifact store postgres migration?",
        )
        assert "Use Postgres for the artifact store." in rendered
        assert "Who owns the backfill runbook?" in rendered
        assert "Stay near Midtown." not in rendered
    finally:
        connection.close()


def test_mid_session_recall_surfaces_prior_session_summary_after_turn_zero(
    tmp_path: Path,
) -> None:
    state_db = tmp_path / "state" / "openminion.db"
    memory_db = tmp_path / "state" / "memory.db"
    migrate_database(state_db)
    connection = connect_database(state_db)
    sessions = SessionStore(connection)
    session_context = SessionContextService(
        sessions,
        keep_recent_messages=1,
        max_compact_per_turn=100,
    )
    memory_service = MemoryService(store=SQLiteMemoryStore(memory_db))
    adapter = MemoryServiceGatewayAdapter(
        memory_service,
        agent_id="phase2-agent",
        session_context=session_context,
        session_summary_max_chars=200,
        session_handoff_max_summaries=5,
        session_summary_structurer=_session_summary_structurer,
    )

    try:
        storage_session = sessions.resolve_session(
            agent_id="phase2-agent",
            channel="console",
            target="storage-mid",
        )
        sessions.append_message(
            session_id=storage_session.id,
            role="inbound",
            body="We decided to use Postgres for the artifact store migration.",
        )
        sessions.append_message(
            session_id=storage_session.id,
            role="outbound",
            body="Okay, Postgres for the artifact store.",
        )
        sessions.append_message(
            session_id=storage_session.id,
            role="inbound",
            body="Who owns the backfill runbook?",
        )
        session_context.on_session_close(session_id=storage_session.id)

        busy_session = sessions.resolve_session(
            agent_id="phase2-agent",
            channel="console",
            target="busy-mid",
        )
        sessions.append_message(
            session_id=busy_session.id,
            role="inbound",
            body="turn one already happened",
        )
        rendered, _ = adapter.build_context_with_metadata(
            session_id=busy_session.id,
            user_message="What did we decide about the artifact store migration again?",
        )
        assert "Continuing from recent sessions" in rendered
        assert "Use Postgres for the artifact store." in rendered
    finally:
        connection.close()


def test_canonical_context_pack_recalls_durable_memory_on_fresh_session(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    session_db = state_dir / "sessions.db"
    memory_db = state_dir / "memory.db"
    memory_service = MemoryService(store=SQLiteMemoryStore(memory_db))
    recalled_id = memory_service.write_record(
        scope="agent:phase2-agent",
        record_type="user_preference",
        title="C++ style preference",
        content={"text": "User prefers terse C++ HTTP server examples."},
        confidence=0.93,
    )

    context_api = create_context_adapter(
        mode="auto",
        session_store=_SessionStoreWithPath(session_db),
    )
    pack = context_api.service.build_pack(
        BuildPackRequest(
            session_id="fresh-session",
            agent_id="phase2-agent",
            purpose="plan",
            query="schedule the cleanup run",
        )
    )

    assert pack.context_manifest is not None
    assert pack.context_manifest.recalled_memory == [recalled_id]
    assert recalled_id in pack.context_manifest.memory
    rendered = "\n".join(segment.content for segment in pack.segments)
    assert "User prefers terse C++ HTTP server examples." in rendered


def test_canonical_context_pack_surfaces_recent_session_artifact_refs_on_fresh_session(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    session_db = state_dir / "sessions.db"
    memory_db = state_dir / "memory.db"
    memory_service = MemoryService(store=SQLiteMemoryStore(memory_db))
    artifact_id = memory_service.write_record(
        scope="agent:phase2-agent",
        record_type="artifact_digest",
        title="artifact_digest:auth.py",
        content={
            "artifact_type": "file",
            "artifact_path": "/workspace/auth.py",
            "artifact_digest": "sha256:abc123",
            "session_id": "session-a",
            "turn_index": 5,
            "tool_name": "file.write",
        },
        confidence=0.88,
    )

    context_api = create_context_adapter(
        mode="auto",
        session_store=_SessionStoreWithPath(session_db),
    )
    pack = context_api.service.build_pack(
        BuildPackRequest(
            session_id="fresh-session",
            agent_id="phase2-agent",
            purpose="plan",
            query="continue the auth work",
        )
    )

    assert pack.context_manifest is not None
    assert pack.context_manifest.recent_session_artifacts == [artifact_id]
    rendered = "\n".join(segment.content for segment in pack.segments)
    assert "[RECENT SESSION ARTIFACTS]" in rendered
    assert "path=/workspace/auth.py" in rendered


def test_plan_snapshot_candidate_stages_on_incomplete_session_close(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    session_db = state_dir / "sessions.db"
    brain_session_db = state_dir / "brain-sessions.db"
    memory_db = state_dir / "memory.db"
    migrate_database(session_db)
    connection = connect_database(session_db)
    sessions = SessionStore(connection)
    session_context = SessionContextService(
        sessions,
        keep_recent_messages=1,
        max_compact_per_turn=100,
    )
    memory_service = MemoryService(store=SQLiteMemoryStore(memory_db))
    brain_sessions = BrainSessionStore(brain_session_db)
    _adapter = MemoryServiceGatewayAdapter(
        memory_service,
        agent_id="phase2-agent",
        session_context=session_context,
        brain_sessions_db_path=brain_session_db,
    )

    try:
        session_a = sessions.resolve_session(
            agent_id="phase2-agent",
            channel="console",
            target="plan-session-a",
        )
        brain_sessions.create_session(
            session_id=session_a.id,
            initial_agent_id="phase2-agent",
        )
        brain_sessions.put_working_state(
            session_a.id,
            state_inline={
                "session_id": session_a.id,
                "agent_id": "phase2-agent",
                "goal": "Finish the pytest migration.",
                "status": "done",
                "budgets_remaining": {
                    "ticks": 1,
                    "tool_calls": 1,
                    "a2a_calls": 0,
                    "tokens": 1000,
                    "time_ms": 1000,
                },
                "plan": {
                    "objective": "Finish the pytest migration.",
                    "steps": [
                        {"command_id": "cmd-1", "kind": "tool", "args": {}},
                        {"command_id": "cmd-2", "kind": "tool", "args": {}},
                    ],
                },
                "cursor": 1,
                "intent_execution_states": [
                    {
                        "intent_id": "intent-1",
                        "description": "finish migration",
                        "status": "in_progress",
                    }
                ],
                "session_work_summary": "Continue the pytest migration from command 2.",
            },
        )
        sessions.append_message(
            session_id=session_a.id,
            role="outbound",
            body="We should continue the pytest migration next time.",
            metadata={
                "brain_status": "done",
                "tool_loop_termination_reason": "iteration_cap",
            },
        )

        session_context.on_session_close(session_id=session_a.id)

        candidates = memory_service.candidate_list(
            CandidateListOptions(session_id=session_a.id)
        )
        assert len(candidates) == 1
        assert candidates[0].type == "plan_snapshot"
        assert candidates[0].content["incomplete_reason"] == "iteration_cap"
        assert candidates[0].content["plan_steps"] == [
            {"step_id": "cmd-1", "status": "succeeded"},
            {"step_id": "cmd-2", "status": "in_progress"},
        ]
    finally:
        brain_sessions.close()
        connection.close()


def test_plan_snapshot_not_staged_when_session_ends_cleanly(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    session_db = state_dir / "sessions.db"
    brain_session_db = state_dir / "brain-sessions.db"
    memory_db = state_dir / "memory.db"
    migrate_database(session_db)
    connection = connect_database(session_db)
    sessions = SessionStore(connection)
    session_context = SessionContextService(
        sessions,
        keep_recent_messages=1,
        max_compact_per_turn=100,
    )
    memory_service = MemoryService(store=SQLiteMemoryStore(memory_db))
    brain_sessions = BrainSessionStore(brain_session_db)
    _adapter = MemoryServiceGatewayAdapter(
        memory_service,
        agent_id="phase2-agent",
        session_context=session_context,
        brain_sessions_db_path=brain_session_db,
    )

    try:
        session_a = sessions.resolve_session(
            agent_id="phase2-agent",
            channel="console",
            target="plan-session-clean",
        )
        brain_sessions.create_session(
            session_id=session_a.id,
            initial_agent_id="phase2-agent",
        )
        brain_sessions.put_working_state(
            session_a.id,
            state_inline={
                "session_id": session_a.id,
                "agent_id": "phase2-agent",
                "goal": "Done already.",
                "status": "done",
                "budgets_remaining": {
                    "ticks": 1,
                    "tool_calls": 1,
                    "a2a_calls": 0,
                    "tokens": 1000,
                    "time_ms": 1000,
                },
                "intent_execution_states": [
                    {
                        "intent_id": "intent-1",
                        "description": "done work",
                        "status": "succeeded",
                    }
                ],
                "cursor": 0,
            },
        )
        sessions.append_message(
            session_id=session_a.id,
            role="outbound",
            body="Everything is finished.",
            metadata={
                "brain_status": "done",
                "tool_loop_termination_reason": "model_final",
            },
        )

        session_context.on_session_close(session_id=session_a.id)

        candidates = memory_service.candidate_list(
            CandidateListOptions(session_id=session_a.id)
        )
        assert candidates == []
    finally:
        brain_sessions.close()
        connection.close()


def test_plan_snapshot_promotes_and_surfaces_on_fresh_session(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    session_db = state_dir / "sessions.db"
    brain_session_db = state_dir / "brain-sessions.db"
    memory_db = state_dir / "memory.db"
    migrate_database(session_db)
    connection = connect_database(session_db)
    sessions = SessionStore(connection)
    session_context = SessionContextService(
        sessions,
        keep_recent_messages=1,
        max_compact_per_turn=100,
    )
    memory_service = MemoryService(store=SQLiteMemoryStore(memory_db))
    brain_sessions = BrainSessionStore(brain_session_db)
    _adapter = MemoryServiceGatewayAdapter(
        memory_service,
        agent_id="phase2-agent",
        session_context=session_context,
        brain_sessions_db_path=brain_session_db,
    )

    try:
        session_a = sessions.resolve_session(
            agent_id="phase2-agent",
            channel="console",
            target="plan-session-recall-a",
        )
        brain_sessions.create_session(
            session_id=session_a.id,
            initial_agent_id="phase2-agent",
        )
        brain_sessions.put_working_state(
            session_a.id,
            state_inline={
                "session_id": session_a.id,
                "agent_id": "phase2-agent",
                "goal": "Resume the auth migration.",
                "status": "done",
                "budgets_remaining": {
                    "ticks": 1,
                    "tool_calls": 1,
                    "a2a_calls": 0,
                    "tokens": 1000,
                    "time_ms": 1000,
                },
                "plan": {
                    "objective": "Resume the auth migration.",
                    "steps": [
                        {"command_id": "cmd-1", "kind": "tool", "args": {}},
                        {"command_id": "cmd-2", "kind": "tool", "args": {}},
                        {"command_id": "cmd-3", "kind": "tool", "args": {}},
                    ],
                },
                "cursor": 2,
                "intent_execution_states": [
                    {
                        "intent_id": "intent-1",
                        "description": "migrate auth",
                        "status": "succeeded",
                    },
                    {
                        "intent_id": "intent-2",
                        "description": "wire tests",
                        "status": "pending",
                    },
                ],
                "session_work_summary": "Continue at command 3 and then wire tests.",
            },
        )
        sessions.append_message(
            session_id=session_a.id,
            role="outbound",
            body="We should continue at command 3 next time.",
            metadata={
                "brain_status": "done",
                "tool_loop_termination_reason": "budget_exhausted",
            },
        )

        session_context.on_session_close(session_id=session_a.id)

        staged = memory_service.candidate_list(
            CandidateListOptions(session_id=session_a.id)
        )
        assert len(staged) == 1
        memory_service.candidate_update(
            staged[0].candidate_id,
            {"status": "approved"},
        )
        promoted = memory_service.promote_candidate(
            staged[0].candidate_id,
            "agent:phase2-agent",
        )

        context_api = create_context_adapter(
            mode="auto",
            session_store=_SessionStoreWithPath(session_db),
        )
        pack = context_api.service.build_pack(
            BuildPackRequest(
                session_id="plan-session-recall-b",
                agent_id="phase2-agent",
                purpose="plan",
                query="continue where we left off",
            )
        )

        assert pack.context_manifest is not None
        assert promoted.id in pack.context_manifest.recalled_memory
        rendered = "\n".join(segment.content for segment in pack.segments)
        assert "plan_snapshot" in rendered
        assert "budget_exhausted" in rendered
    finally:
        brain_sessions.close()
        connection.close()


def test_negative_tool_outcome_candidate_stays_staged_and_suppressed(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    session_db = state_dir / "sessions.db"
    memory_db = state_dir / "memory.db"
    migrate_database(session_db)
    connection = connect_database(session_db)
    sessions = SessionStore(connection)
    session_context = SessionContextService(
        sessions,
        keep_recent_messages=1,
        max_compact_per_turn=100,
    )
    memory_service = MemoryService(store=SQLiteMemoryStore(memory_db))
    adapter = MemoryServiceGatewayAdapter(
        memory_service,
        agent_id="phase2-agent",
        session_context=session_context,
    )

    try:
        session_a = sessions.resolve_session(
            agent_id="phase2-agent",
            channel="console",
            target="tool-session-a",
        )
        memory_service.candidate_put(
            MemoryCandidate(
                candidate_id="cand_tool_outcome",
                session_id=session_a.id,
                proposed_scope="agent:phase2-agent",
                type="tool_outcome",
                title="tool_outcome:web.search:failure:PROVIDER_TIMEOUT",
                content={
                    "tool_name": "web.search",
                    "tool_family": "web",
                    "outcome": "failure",
                    "error_code": "PROVIDER_TIMEOUT",
                    "turn_index": 0,
                    "intent_id": "weather-intent",
                    "artifact_ref": None,
                },
                tags=["tool_outcome", "tool_family:web", "outcome:failure"],
                confidence=0.9,
                claim_key="tool_outcome:web.search:failure:provider_timeout",
                source_class="tool_result",
                meta={
                    "reconfirmation_count": 2,
                    "retrieval_hit_count": 3,
                    "source_negative_outcome": True,
                    "source_outcome_status": "failure",
                    "source_tool_name": "web.search",
                },
            )
        )
        adapter.record_turn(
            session_id=session_a.id,
            run_id="run-tool-1",
            request_id="req-tool-1",
            channel="console",
            target="chat",
            user_message="web.search failed for the weather lookup",
            assistant_message="The provider timed out, so we need a different tool.",
        )

        promoted = memory_service.list(
            ListQueryOptions(
                scopes=["agent:phase2-agent"],
                types=["tool_outcome"],
                limit=10,
            )
        )
        assert promoted == []
        staged = memory_service.candidate_get("cand_tool_outcome")
        assert staged.status == "proposed"
        assert staged.meta["trust_gate_reason_code"] == "BELOW_TRUST_THRESHOLD"

        context_api = create_context_adapter(
            mode="auto",
            session_store=_SessionStoreWithPath(session_db),
        )
        pack = context_api.service.build_pack(
            BuildPackRequest(
                session_id="tool-session-b",
                agent_id="phase2-agent",
                purpose="plan",
                query="what should we remember before trying again?",
            )
        )

        assert pack.context_manifest is not None
        # Negative outcomes remain staged operational evidence until their
        # trust/readiness score clears promotion policy.
        rendered = "\n".join(segment.content for segment in pack.segments)
        assert "tool_outcome:web.search:failure:PROVIDER_TIMEOUT" not in rendered
    finally:
        connection.close()


def test_failure_guidance_surfaces_via_correction_while_negative_tool_outcome_stays_suppressed(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    session_db = state_dir / "sessions.db"
    memory_db = state_dir / "memory.db"
    migrate_database(session_db)
    connection = connect_database(session_db)
    sessions = SessionStore(connection)
    session_context = SessionContextService(
        sessions,
        keep_recent_messages=1,
        max_compact_per_turn=100,
    )
    memory_service = MemoryService(store=SQLiteMemoryStore(memory_db))
    adapter = MemoryServiceGatewayAdapter(
        memory_service,
        agent_id="phase2-agent",
        session_context=session_context,
    )

    try:
        session_a = sessions.resolve_session(
            agent_id="phase2-agent",
            channel="console",
            target="failure-guidance-session-a",
        )
        memory_service.candidate_put(
            MemoryCandidate(
                candidate_id="cand_failure_correction",
                session_id=session_a.id,
                proposed_scope="agent:phase2-agent",
                type="correction",
                title="Correction for web.search",
                content={
                    "text": "Before retrying web.search, verify auth first.",
                    "tool_name": "web.search",
                    "args_signature": '{"query":"sf weather"}',
                },
                tags=["failure_path", "correction"],
                confidence=0.84,
                meta={
                    "reconfirmation_count": 2,
                    "retrieval_hit_count": 3,
                    "source_failure_path": True,
                    "source_negative_outcome": True,
                    "source_tool_name": "web.search",
                    "source_args_signature": '{"query":"sf weather"}',
                },
            )
        )
        memory_service.candidate_put(
            MemoryCandidate(
                candidate_id="cand_failure_tool_outcome",
                session_id=session_a.id,
                proposed_scope="agent:phase2-agent",
                type="tool_outcome",
                title="tool_outcome:web.search:failure:AUTH_REQUIRED",
                content={
                    "tool_name": "web.search",
                    "tool_family": "web",
                    "outcome": "failure",
                    "error_code": "AUTH_REQUIRED",
                    "turn_index": 0,
                    "intent_id": "weather-intent",
                    "args_signature": '{"query":"sf weather"}',
                    "artifact_ref": None,
                },
                tags=["tool_outcome", "tool_family:web", "outcome:failure"],
                confidence=0.4,
                meta={
                    "reconfirmation_count": 2,
                    "retrieval_hit_count": 3,
                    "source_negative_outcome": True,
                    "source_outcome_status": "failure",
                    "source_tool_name": "web.search",
                    "source_args_signature": '{"query":"sf weather"}',
                },
            )
        )
        adapter.record_turn(
            session_id=session_a.id,
            run_id="run-failure-guidance-1",
            request_id="req-failure-guidance-1",
            channel="console",
            target="chat",
            user_message="web.search failed because auth was missing",
            assistant_message="Next time we should verify auth before retrying web.search.",
        )

        staged = memory_service.candidate_list(
            CandidateListOptions(session_id=session_a.id)
        )
        for candidate in staged:
            memory_service.candidate_update(
                candidate.candidate_id,
                {"status": "approved"},
            )
            memory_service.promote_candidate(
                candidate.candidate_id,
                "agent:phase2-agent",
            )

        context_api = create_context_adapter(
            mode="auto",
            session_store=_SessionStoreWithPath(session_db),
        )
        pack = context_api.service.build_pack(
            BuildPackRequest(
                session_id="failure-guidance-session-b",
                agent_id="phase2-agent",
                purpose="plan",
                query="what correction should we follow before retrying web.search?",
            )
        )

        assert pack.context_manifest is not None
        rendered = "\n".join(segment.content for segment in pack.segments)
        assert "Before retrying web.search, verify auth first." in rendered
        assert "tool_outcome:web.search:failure:AUTH_REQUIRED" not in rendered
    finally:
        connection.close()


def test_tool_success_candidate_promotes_and_surfaces_on_fresh_session(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    session_db = state_dir / "sessions.db"
    memory_db = state_dir / "memory.db"
    migrate_database(session_db)
    connection = connect_database(session_db)
    sessions = SessionStore(connection)
    session_context = SessionContextService(
        sessions,
        keep_recent_messages=1,
        max_compact_per_turn=100,
    )
    memory_service = MemoryService(store=SQLiteMemoryStore(memory_db))
    adapter = MemoryServiceGatewayAdapter(
        memory_service,
        agent_id="phase2-agent",
        session_context=session_context,
    )

    try:
        session_a = sessions.resolve_session(
            agent_id="phase2-agent",
            channel="console",
            target="tool-success-session-a",
        )
        memory_service.candidate_put(
            MemoryCandidate(
                candidate_id="cand_tool_success",
                session_id=session_a.id,
                proposed_scope="agent:phase2-agent",
                type="tool_outcome",
                title="tool_outcome:web.fetch:success",
                content={
                    "tool_name": "web.fetch",
                    "tool_family": "web",
                    "outcome": "success",
                    "error_code": None,
                    "turn_index": 0,
                    "intent_id": "news-intent",
                    "artifact_ref": None,
                },
                tags=["tool_outcome", "tool_family:web", "outcome:success"],
                confidence=0.9,
                claim_key="tool_outcome:web.fetch:success",
                source_class="tool_result",
                meta={
                    "reconfirmation_count": 2,
                    "retrieval_hit_count": 3,
                    "source_negative_outcome": False,
                    "source_success_path": True,
                    "source_outcome_status": "success",
                    "source_tool_name": "web.fetch",
                },
            )
        )
        adapter.record_turn(
            session_id=session_a.id,
            run_id="run-tool-success-1",
            request_id="req-tool-success-1",
            channel="console",
            target="chat",
            user_message="web.fetch worked well for the news lookup",
            assistant_message="web.fetch returned the article cleanly, so we can reuse it.",
        )

        promoted = memory_service.list(
            ListQueryOptions(
                scopes=["agent:phase2-agent"],
                types=["tool_outcome"],
                limit=10,
            )
        )
        assert len(promoted) == 1
        promoted_id = promoted[0].id

        context_api = create_context_adapter(
            mode="auto",
            session_store=_SessionStoreWithPath(session_db),
        )
        pack = context_api.service.build_pack(
            BuildPackRequest(
                session_id="tool-success-session-b",
                agent_id="phase2-agent",
                purpose="plan",
                query="what worked well last time?",
            )
        )

        assert pack.context_manifest is not None
        assert promoted_id in pack.context_manifest.recalled_memory
        rendered = "\n".join(segment.content for segment in pack.segments)
        assert "tool_outcome:web.fetch:success" in rendered
    finally:
        connection.close()


def test_meta_rule_preference_candidate_promotes_and_surfaces_on_fresh_session(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    session_db = state_dir / "sessions.db"
    memory_db = state_dir / "memory.db"
    migrate_database(session_db)
    connection = connect_database(session_db)
    sessions = SessionStore(connection)
    session_context = SessionContextService(
        sessions,
        keep_recent_messages=1,
        max_compact_per_turn=100,
    )
    memory_service = MemoryService(store=SQLiteMemoryStore(memory_db))
    adapter = MemoryServiceGatewayAdapter(
        memory_service,
        agent_id="phase2-agent",
        session_context=session_context,
    )

    try:
        session_a = sessions.resolve_session(
            agent_id="phase2-agent",
            channel="console",
            target="meta-rule-session-a",
        )
        memory_service.candidate_put(
            MemoryCandidate(
                candidate_id="cand_meta_rule_pref",
                session_id=session_a.id,
                proposed_scope="agent:phase2-agent",
                type="meta_rule_preference",
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
                tags=["meta_rule_preference", "rule:search_retry_count"],
                confidence=0.7,
                meta={"source_meta_rule_preference": True},
            )
        )
        adapter.record_turn(
            session_id=session_a.id,
            run_id="run-meta-rule-1",
            request_id="req-meta-rule-1",
            channel="console",
            target="chat",
            user_message="Search retries needed to be increased",
            assistant_message="Let's remember to use three retries for broad queries.",
        )
        memory_service.candidate_update(
            "cand_meta_rule_pref",
            {"status": "approved"},
        )
        memory_service.promote_candidate(
            "cand_meta_rule_pref",
            "agent:phase2-agent",
        )

        promoted = memory_service.list(
            ListQueryOptions(
                scopes=["agent:phase2-agent"],
                types=["meta_rule_preference"],
                limit=10,
            )
        )
        assert len(promoted) == 1
        promoted_id = promoted[0].id

        context_api = create_context_adapter(
            mode="auto",
            session_store=_SessionStoreWithPath(session_db),
        )
        pack = context_api.service.build_pack(
            BuildPackRequest(
                session_id="meta-rule-session-b",
                agent_id="phase2-agent",
                purpose="plan",
                query="what should we remember before trying search again?",
            )
        )

        assert pack.context_manifest is not None
        assert promoted_id in pack.context_manifest.recalled_memory
        rendered = "\n".join(segment.content for segment in pack.segments)
        assert "meta_rule_preference" in rendered
        assert "search_retry_count" in rendered
        assert "preferred_value=3" in rendered
    finally:
        connection.close()


def test_canonical_context_pack_recalls_mid_session_memory_without_query_overlap(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    session_db = state_dir / "sessions.db"
    memory_db = state_dir / "memory.db"
    memory_service = MemoryService(store=SQLiteMemoryStore(memory_db))
    recalled_id = memory_service.write_record(
        scope="agent:phase2-agent",
        record_type="fact",
        title="pytest-migration",
        content={"text": "pytest-migration requires fixture layering cleanup."},
        confidence=0.84,
    )

    context_api = create_context_adapter(
        mode="auto",
        session_store=_SessionStoreWithPath(session_db),
    )
    service = context_api.service
    service._sessctl = _StaticSessionClient(  # noqa: SLF001
        SessionSlice(
            session_id="mid-session",
            slice_version="slice:v6",
            summary_short="",
            recent_turns=[
                SessionTurn(turn_id=f"t{i}", role="user", content=f"turn {i}")
                for i in range(1, 7)
            ],
            active_state={
                "intent_execution_states": [
                    {"intent_id": "pytest-migration", "status": "active"}
                ],
                "resolved_skill_ids": ["python-tests"],
                "active_skill_id": "python-tests",
                "cursor": 2,
                "plan": {"steps": [{"command_id": "cmd-1"}, {"command_id": "cmd-2"}]},
            },
            recent_tool_events=[
                SessionToolEvent(
                    event_id="evt-1",
                    tool_name="file.read",
                    excerpt="looked at a test file",
                )
            ],
        )
    )
    service._latest_manifest_by_session["mid-session"] = ContextManifest.model_validate(  # noqa: SLF001
        {
            "identity": {
                "agent_id": "phase2-agent",
                "profile_version": "pv",
                "render_version": "rv",
            },
            "session": {
                "slice_version": "slice:v5",
                "turn_index": 5,
                "turn_ids_included": [],
            },
            "facts": [],
            "memory": [],
            "recalled_memory": [],
            "procedures": [],
            "artifacts": [],
            "segment_ids": [],
            "included_segment_ids": [],
            "dropped_segment_ids": [],
            "mid_session_recall_state": {
                "turn_index": 5,
                "intent_states": [
                    {"intent_id": "pytest-migration", "status": "pending"}
                ],
                "active_skill_id": "python-tests",
                "resolved_skill_ids": ["python-tests"],
                "plan_cursor": 1,
                "plan_step_ids": ["cmd-1"],
                "recent_tool_families": ["file"],
            },
        }
    )

    pack = service.build_pack(
        BuildPackRequest(
            session_id="mid-session",
            agent_id="phase2-agent",
            purpose="plan",
            query="what should we do now?",
        )
    )

    assert pack.context_manifest.mid_session_recalled_memory == [recalled_id]
    assert pack.context_manifest.recalled_memory == [recalled_id]
    assert pack.context_manifest.session_start_recalled_memory == []
    rendered = "\n".join(segment.content for segment in pack.segments)
    assert "pytest-migration requires fixture layering cleanup." in rendered
