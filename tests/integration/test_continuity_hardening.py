from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from openminion.modules.memory.models import MemoryRecord
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore
from openminion.modules.storage.runtime.migrations import migrate_database
from openminion.modules.storage.runtime.session_store import SessionStore
from openminion.modules.storage.runtime.sqlite import connect_database
from openminion.services.agent.memory.gateway_adapter import MemoryServiceGatewayAdapter
from openminion.services.context.session import SessionContextService


def test_continuity_hardening_full_handoff_cycle_with_compression(
    tmp_path: Path,
) -> None:
    state_db = tmp_path / "state" / "openminion.db"
    memory_db = tmp_path / "memory" / "memory.db"
    migrate_database(state_db)
    connection = connect_database(state_db)
    sessions = SessionStore(connection)
    session_context = SessionContextService(
        sessions,
        keep_recent_messages=20,
        max_compact_per_turn=100,
    )
    memory_service = MemoryService(store=SQLiteMemoryStore(memory_db))
    adapter = MemoryServiceGatewayAdapter(
        memory_service,
        agent_id="continuity-agent",
        session_context=session_context,
        capsule_max_chars=2400,
        session_summary_max_chars=500,
        session_handoff_max_summaries=5,
    )

    def write_summary(target: str, rolling_summary: str) -> str:
        session = sessions.resolve_session(
            agent_id="continuity-agent",
            channel="console",
            target=target,
        )
        context = sessions.ensure_session_context(session_id=session.id)
        sessions.update_session_context(
            session_id=session.id,
            rolling_summary=rolling_summary,
            summary_short=rolling_summary[:80],
            compacted_message_count=4,
            version=context.version + 1,
            expected_version=context.version,
        )
        summary_id = adapter.write_session_summary(session.id)
        assert summary_id is not None
        return session.id

    try:
        deploy_session = sessions.resolve_session(
            agent_id="continuity-agent",
            channel="console",
            target="deploy",
        )
        adapter.record_turn(
            session_id=deploy_session.id,
            run_id="deploy-run",
            request_id="deploy-req",
            channel="console",
            target="deploy",
            user_message="remember: deploy key rotates every 90 days",
            assistant_message="Captured.",
        )
        deploy_context = sessions.ensure_session_context(session_id=deploy_session.id)
        sessions.update_session_context(
            session_id=deploy_session.id,
            rolling_summary=(
                "Deploy planning review and rotation prep stayed in focus throughout "
                "the session. We agreed to keep deployment automation on Tuesdays. "
                "No issues found with the staging rollout."
            ),
            summary_short="deploy planning review",
            compacted_message_count=4,
            version=deploy_context.version + 1,
            expected_version=deploy_context.version,
        )
        adapter.write_session_summary(deploy_session.id)

        write_summary(
            "ui",
            (
                "Routine interface cleanup and backlog grooming stayed in focus "
                "throughout this session without changing delivery priorities. "
                "Actually, lunch vendor comparison matrix still needs another tab."
            ),
        )
        write_summary(
            "sidebar",
            (
                "Routine interface cleanup and backlog grooming stayed in focus "
                "throughout this session without changing delivery priorities. "
                "Actually, sidebar spacing audit needs one more pass."
            ),
        )
        write_summary(
            "dashboard",
            (
                "Routine interface cleanup and backlog grooming stayed in focus "
                "throughout this session without changing delivery priorities. "
                "Actually, benchmark dashboard color pass still needs a final review."
            ),
        )
        write_summary(
            "retro",
            (
                "Routine interface cleanup and backlog grooming stayed in focus "
                "throughout this session without changing delivery priorities. "
                "Actually, retro emoji poll still needs a final tally."
            ),
        )
        write_summary(
            "board",
            (
                "Routine interface cleanup and backlog grooming stayed in focus "
                "throughout this session without changing delivery priorities. "
                "Actually, kanban swimlane tidy-up still needs one more sweep."
            ),
        )

        old_timestamp = (datetime.now(timezone.utc) - timedelta(days=21)).isoformat()
        memory_service._store.put(  # noqa: SLF001
            MemoryRecord(
                id="summary-old",
                scope="agent:continuity-agent",
                type="session_summary",
                key="session_summary:old",
                title="Legacy deployment summary",
                content={
                    "decisions": ["legacy deployment exception"],
                    "open_questions": ["legacy question"],
                    "corrections": ["legacy correction"],
                    "topic_keywords": ["legacy", "deploy"],
                    "turn_count": 2,
                    "summary_text": (
                        "Legacy deployment exception details should be compressed on open."
                    ),
                },
                source="validated",
                confidence=0.8,
                created_at=old_timestamp,
                updated_at=old_timestamp,
            )
        )

        query_session = sessions.resolve_session(
            agent_id="continuity-agent",
            channel="console",
            target="query",
        )
        context, _meta = adapter.build_context_with_metadata(
            session_id=query_session.id,
            user_message="what did we discuss about deployment and the deploy key rotation?",
        )

        assert "## Continuing from recent sessions" in context
        # Continuity hardening now renders summary-text previews only; semantic
        # decision extraction from prose was retired under the anti-LLM cleanup.
        assert "Deploy planning review and rotation prep" in context
        assert (
            "Summary: Deploy planning review and rotation prep stayed in focus"
            in context
        )
        assert "deploy key rotates every 90 days" in context.lower()

        compressed = memory_service._store.get("summary-old")  # noqa: SLF001
        assert compressed is not None
        assert isinstance(compressed.content, dict)
        assert compressed.content["decisions"] == ["legacy deployment exception"]
        assert compressed.content["corrections"] == ["legacy correction"]
        assert compressed.content["open_questions"] == ["legacy question"]
        assert (
            "Legacy deployment exception details" in compressed.content["summary_text"]
        )
    finally:
        connection.close()
