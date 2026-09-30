from __future__ import annotations

import asyncio
import logging
from dataclasses import replace

from openminion.base.channel import ChannelRegistry
from openminion.base.config import OpenMinionConfig
from openminion.services.agent import AgentService
from openminion.modules.context.slices import build_session_slice_from_runtime_store
from openminion.modules.memory.config import from_base_config
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore
from openminion.modules.storage.runtime.migrations import migrate_database
from openminion.modules.storage.runtime.idempotency_store import IdempotencyStore
from openminion.modules.storage.runtime.session_store import SessionStore
from openminion.modules.storage.runtime.sqlite import connect_database
from openminion.services.agent.memory.gateway_adapter import MemoryServiceGatewayAdapter
from openminion.services.context.session import SessionContextService
from openminion.services.gateway import GatewayService
from openminion.services.runtime.plugins import PluginRegistry
from tests._csc_fixtures import _csc_install_default_agent
from tests.services.gateway._gateway_service_support import (
    _CaptureProvider,
    _SinkChannel,
)


def _memory_runtime(memory_path, tmp_path):
    config = from_base_config(
        base_config=OpenMinionConfig(),
        home_root=tmp_path / "home",
        data_root=tmp_path / "data",
    )
    config = replace(
        config,
        candidate_learning=replace(
            config.candidate_learning,
            auto_extract_enabled=False,
        ),
    )
    service = MemoryService(store=SQLiteMemoryStore(memory_path))
    adapter = MemoryServiceGatewayAdapter(
        service,
        agent_id="continuity-agent",
        project_id="project-1",
        memory_config=config,
        capsule_max_chars=700,
    )
    return service, adapter


def _gateway_runtime(connection, sessions):
    config = OpenMinionConfig()
    _csc_install_default_agent(config, name="main")
    provider = _CaptureProvider()
    gateway = GatewayService(
        agent=AgentService(
            config=config,
            plugins=PluginRegistry([]),
            provider=provider,
            logger=logging.getLogger("openminion.tests.continuity.agent"),
        ),
        channels=ChannelRegistry([_SinkChannel()]),
        logger=logging.getLogger("openminion.tests.continuity.gateway"),
        sessions=sessions,
        idempotency=IdempotencyStore(connection),
        agent_id="main",
        history_limit=1,
        session_context=SessionContextService(
            sessions,
            keep_recent_messages=1,
            archive_enabled=False,
        ),
    )
    return gateway, provider


def test_objective_correction_and_bounded_context_survive_three_processes(
    tmp_path,
) -> None:
    session_path = tmp_path / "runtime.db"
    memory_path = tmp_path / "memory.db"
    migrate_database(session_path)

    connection = connect_database(session_path)
    first_store = SessionStore(connection)
    session = first_store.resolve_session(
        agent_id="continuity-agent",
        channel="console",
        target="project-1",
        session_id="session-1",
        metadata={"project_id": "project-1", "task_id": "task-1"},
    )
    for index in range(12):
        first_store.append_message(
            session_id=session.id,
            role="inbound" if index % 2 == 0 else "outbound",
            body=f"context pressure message {index}",
        )
    context = first_store.ensure_session_context(session_id=session.id)
    first_store.update_session_context(
        session_id=session.id,
        summary_short="Objective: ship service. Criterion: use verified deployment region.",
        rolling_summary="Research and implement the service without losing criteria.",
        version=context.version + 1,
    )
    first_memory, _first_adapter = _memory_runtime(memory_path, tmp_path)
    first_memory.write_record(
        scope="project:project-1",
        record_type="fact",
        title="Deployment region",
        content="The deployment region is us-west-2.",
        tags=["deployment"],
    )
    first_memory.write_record(
        scope="project:project-1",
        record_type="fact",
        title="Distractor",
        content="The documentation theme uses blue headings.",
        tags=["docs"],
    )
    connection.close()

    connection = connect_database(session_path)
    second_store = SessionStore(connection)
    resumed = second_store.resolve_session(
        agent_id="continuity-agent",
        channel="console",
        target="project-1",
        session_id="session-1",
    )
    assert resumed.id == session.id
    assert resumed.metadata == {"project_id": "project-1", "task_id": "task-1"}
    second_store.append_message(
        session_id=resumed.id,
        role="inbound",
        body="Correction: deployment region is us-east-1, not us-west-2.",
    )
    second_store.append_event(
        session_id=resumed.id,
        event_type="session.compaction.archive",
        payload={"relative_path": "archive/session-1-chunk-1.jsonl"},
    )
    second_memory, _second_adapter = _memory_runtime(memory_path, tmp_path)
    second_memory.write_record(
        scope="project:project-1",
        record_type="correction",
        title="Corrected deployment region",
        content="Use us-east-1 for deployment; us-west-2 is obsolete.",
        tags=["deployment", "correction"],
    )
    connection.close()

    connection = connect_database(session_path)
    third_store = SessionStore(connection)
    restored = third_store.get_session("session-1")
    assert restored is not None
    assert restored.metadata["task_id"] == "task-1"
    context_slice = build_session_slice_from_runtime_store(
        store=third_store,
        session_id=restored.id,
        limits={"recent_turn_limit": 2, "tool_events_limit": 2},
    )
    _third_memory, third_adapter = _memory_runtime(memory_path, tmp_path)
    recalled, metadata = third_adapter.build_context_with_metadata(
        session_id=restored.id,
        user_message="Which deployment region should the service use?",
    )
    connection.close()

    assert context_slice.summary_short.startswith("Objective: ship service")
    assert len(context_slice.recent_turns) <= 2
    assert "archive/session-1-chunk-1.jsonl" in context_slice.archive_refs
    assert len(recalled) <= 700
    assert "Use us-east-1 for deployment" in recalled
    if "The deployment region is us-west-2" in recalled:
        assert recalled.index("Use us-east-1") < recalled.index("us-west-2")
    if "blue headings" in recalled:
        assert recalled.index("Use us-east-1") < recalled.index("blue headings")
    assert metadata["memory_envelope_limit_chars"] == "700"


def test_focus_compacted_history_survives_restart_for_brain_request(tmp_path) -> None:
    session_path = tmp_path / "runtime.db"
    migrate_database(session_path)
    focus_id = "focus-session-1"

    connection = connect_database(session_path)
    store = SessionStore(connection)
    session = store.resolve_session(
        agent_id="main",
        channel="console",
        target="focus",
        session_id="session-1",
    )
    for role, body in (
        ("inbound", "Objective: finish the deployment migration."),
        ("outbound", "Constraint: preserve the public API."),
        ("inbound", "Use the linked tool result before continuing."),
        ("outbound", "Tool result artifact: deployment-plan.json"),
        ("inbound", "Continue after restart."),
    ):
        store.append_message(
            session_id=session.id,
            conversation_id=focus_id,
            role=role,
            body=body,
        )
    SessionContextService(
        store,
        keep_recent_messages=1,
        max_compact_per_turn=20,
        archive_enabled=False,
    ).compact_session(session_id=session.id)
    connection.close()

    connection = connect_database(session_path)
    restored_store = SessionStore(connection)
    gateway, provider = _gateway_runtime(connection, restored_store)
    asyncio.run(
        gateway.run_once(
            channel="console",
            target="focus",
            message="Continue after restart.",
            session_id=session.id,
            inbound_metadata={"conversation_id": focus_id},
            deliver=False,
        )
    )
    connection.close()

    request_context = "\n".join(
        message.content for message in provider.requests[-1].history
    )
    assert request_context.count("finish the deployment migration") == 1
    assert request_context.count("preserve the public API") == 1
    assert request_context.count("deployment-plan.json") == 1


def test_gateway_rejects_mixed_focus_compaction_summary(tmp_path) -> None:
    session_path = tmp_path / "runtime.db"
    migrate_database(session_path)
    connection = connect_database(session_path)
    store = SessionStore(connection)
    session = store.resolve_session(
        agent_id="main",
        channel="console",
        target="focus",
        session_id="mixed-session",
    )
    store.append_message(
        session_id=session.id,
        conversation_id="focus-mixed-session",
        role="inbound",
        body="Canonical objective must not escape a mixed summary.",
    )
    store.append_message(
        session_id=session.id,
        conversation_id="foreign-conversation",
        role="outbound",
        body="Foreign room fact must stay isolated.",
    )
    store.append_message(
        session_id=session.id,
        conversation_id="focus-mixed-session",
        role="inbound",
        body="Canonical recent tail.",
    )
    SessionContextService(
        store,
        keep_recent_messages=1,
        max_compact_per_turn=20,
        archive_enabled=False,
    ).compact_session(session_id=session.id)
    gateway, provider = _gateway_runtime(connection, store)

    asyncio.run(
        gateway.run_once(
            channel="console",
            target="focus",
            message="Continue safely.",
            session_id=session.id,
            inbound_metadata={"conversation_id": "focus-mixed-session"},
            deliver=False,
        )
    )
    request_context = "\n".join(
        message.content for message in provider.requests[-1].history
    )
    connection.close()

    assert "Canonical objective must not escape" not in request_context
    assert "Foreign room fact" not in request_context
