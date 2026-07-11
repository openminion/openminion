from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.base import ListQueryOptions
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore
from openminion.services.agent.memory.gateway_adapter import MemoryServiceGatewayAdapter


def _make_sqlite_adapter(
    db_path: Path, agent_id: str = "long-term-agent"
) -> MemoryServiceGatewayAdapter:
    return MemoryServiceGatewayAdapter(
        MemoryService(store=SQLiteMemoryStore(db_path)),
        agent_id=agent_id,
    )


def _make_memory_service(db_path: Path) -> MemoryService:
    return MemoryService(store=SQLiteMemoryStore(db_path))


def _profile(
    *,
    revision: int,
    mission: str,
    responsibilities: list[str],
    hard_constraints: list[str],
    domain: list[str],
) -> SimpleNamespace:
    return SimpleNamespace(
        profile_revision=revision,
        role=SimpleNamespace(
            mission=mission,
            responsibilities=responsibilities,
            hard_constraints=hard_constraints,
            domain=domain,
        ),
    )


def test_facts_persist_across_restarts(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    adapter1 = _make_sqlite_adapter(db_path)
    adapter1.record_turn(
        session_id="session-a",
        run_id="run1",
        request_id="req1",
        channel="test",
        target="user",
        user_message="remember: my favorite color is blue",
        assistant_message="",
    )
    context, _ = _make_sqlite_adapter(db_path).build_context_with_metadata(
        session_id="session-a",
        user_message="",
    )
    assert "blue" in context.lower()


def test_tasks_persist_across_restarts(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    adapter1 = _make_sqlite_adapter(db_path)
    adapter1.record_turn(
        session_id="sess-task",
        run_id="r1",
        request_id="req1",
        channel="c",
        target="t",
        user_message="todo: write unit tests",
        assistant_message="",
    )
    context, _ = _make_sqlite_adapter(db_path).build_context_with_metadata(
        session_id="sess-task",
        user_message="",
    )
    assert "unit tests" in context.lower()


def test_multiple_facts_persist(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    adapter1 = _make_sqlite_adapter(db_path)
    adapter1.record_turn(
        session_id="s-multi",
        run_id="r1",
        request_id="req1",
        channel="c",
        target="t",
        user_message=(
            "fact: the team uses Python\n"
            "fact: meetings are on Monday\n"
            "fact: the project is called Phoenix"
        ),
        assistant_message="",
    )
    context, _ = _make_sqlite_adapter(db_path).build_context_with_metadata(
        session_id="s-multi",
        user_message="",
    )
    found = sum(
        keyword in context.lower() for keyword in ["python", "monday", "phoenix"]
    )
    assert found > 0, "Expected at least one persisted fact in context"


def test_agent_scope_persists_across_sessions(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    adapter1 = _make_sqlite_adapter(db_path, agent_id="cross-session-agent")
    adapter1.record_turn(
        session_id="session-A",
        run_id="r1",
        request_id="req1",
        channel="c",
        target="t",
        user_message="remember: I work at TechCorp",
        assistant_message="",
    )
    context, _ = _make_sqlite_adapter(
        db_path,
        agent_id="cross-session-agent",
    ).build_context_with_metadata(
        session_id="session-B",
        user_message="",
    )
    assert "TechCorp" in context


def test_search_retrieves_relevant_facts(tmp_path: Path) -> None:
    adapter = _make_sqlite_adapter(tmp_path / "memory.db")
    adapter.record_turn(
        session_id="s-search",
        run_id="r1",
        request_id="req1",
        channel="c",
        target="t",
        user_message=(
            "fact: Jupiter is the largest planet\n"
            "fact: the coffee machine is on the 3rd floor"
        ),
        assistant_message="",
    )
    context = adapter.build_retrieval_context(
        session_id="s-search",
        user_message="tell me about planets",
    )
    assert isinstance(context, str)


def test_record_multiple_sessions_isolated(tmp_path: Path) -> None:
    adapter = _make_sqlite_adapter(tmp_path / "memory.db")
    adapter.record_turn(
        session_id="sess-private",
        run_id="r1",
        request_id="req1",
        channel="c",
        target="t",
        user_message="fact: private session fact XYZ123",
        assistant_message="",
    )
    context, _ = adapter.build_context_with_metadata(
        session_id="sess-other",
        user_message="",
    )
    assert "XYZ123" not in context


def test_generation_counts_persist(tmp_path: Path) -> None:
    adapter = _make_sqlite_adapter(tmp_path / "memory.db")
    first = adapter.record_turn(
        session_id="s-gen",
        run_id="r1",
        request_id="req1",
        channel="c",
        target="t",
        user_message="fact: first",
        assistant_message="",
    )
    second = adapter.record_turn(
        session_id="s-gen",
        run_id="r2",
        request_id="req2",
        channel="c",
        target="t",
        user_message="fact: second",
        assistant_message="",
    )
    assert second.generation > first.generation


def test_direct_sqlite_store_list(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    adapter = MemoryServiceGatewayAdapter(
        _make_memory_service(db_path),
        agent_id="store-test",
    )
    adapter.record_turn(
        session_id="s-store",
        run_id="r1",
        request_id="req1",
        channel="c",
        target="t",
        user_message="fact: stored in SQLite",
        assistant_message="",
    )
    records = SQLiteMemoryStore(db_path).list(
        ListQueryOptions(scopes=["session:s-store"], limit=50)
    )
    texts = " ".join(
        str(getattr(record, "content", "") or "")
        + " "
        + str(getattr(record, "title", "") or "")
        for record in records
    )
    assert "SQLite" in texts


def test_identity_seeder_integration(tmp_path: Path) -> None:
    from openminion.modules.memory.runtime.identity_seeder import seed_identity_pins

    service = _make_memory_service(tmp_path / "memory.db")
    count = seed_identity_pins(
        profile=_profile(
            revision=1,
            mission="Help users with Python code",
            responsibilities=["Write tests", "Review PRs"],
            hard_constraints=["Never break production", "Always write tests"],
            domain=["Python", "testing"],
        ),
        memory_service=service,
        agent_id="identity-test-agent",
    )
    assert count > 0

    agent_records = service.list(
        ListQueryOptions(scopes=["agent:identity-test-agent"], limit=50)
    )
    all_content = " ".join(
        str(getattr(record, "content", "") or "") for record in agent_records
    )
    assert "Python code" in all_content


def test_identity_seeder_idempotent(tmp_path: Path) -> None:
    from openminion.modules.memory.runtime.identity_seeder import seed_identity_pins

    service = _make_memory_service(tmp_path / "memory.db")
    profile = _profile(
        revision=3,
        mission="Build great software",
        responsibilities=["Code quality"],
        hard_constraints=["No hacks"],
        domain=["engineering"],
    )
    count1 = seed_identity_pins(
        profile=profile,
        memory_service=service,
        agent_id="idempotent-agent",
    )
    assert count1 > 0

    count2 = seed_identity_pins(
        profile=profile,
        memory_service=service,
        agent_id="idempotent-agent",
    )
    assert count2 == 0, "Second call with same revision should return 0 (skipped)"
