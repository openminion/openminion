from __future__ import annotations

from tests.helpers.memory_e2e_helpers import E2EMemoryHarness
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore


def test_e2e_scope_isolation_and_project_sharing(tmp_path) -> None:
    shared_store = SQLiteMemoryStore(tmp_path / "shared.db")
    agent_a = E2EMemoryHarness(
        tmp_path,
        agent_id="agent-a",
        project_id="proj-a",
        store=shared_store,
    )
    agent_b = E2EMemoryHarness(
        tmp_path,
        agent_id="agent-b",
        project_id="proj-a",
        store=shared_store,
    )

    agent_a.service.upsert_record(
        scope="agent:agent-a",
        record_type="fact",
        key="favorite:color",
        record_patch={
            "title": "Favorite color",
            "content": "My favorite color is blue.",
            "confidence": 0.8,
        },
    )
    agent_a.service.upsert_record(
        scope="project:proj-a",
        record_type="project_convention",
        key="project:pytest",
        record_patch={
            "title": "Project convention",
            "content": "We use pytest for tests.",
            "confidence": 0.85,
        },
    )

    color_capsule_b = agent_b.build_capsule(
        "scope-b-color", "what is my favorite color?"
    ).lower()
    color_capsule_a = agent_a.build_capsule(
        "scope-a-color", "what is my favorite color?"
    ).lower()
    project_capsule_b = agent_b.build_capsule(
        "scope-b-project", "what testing convention should I follow?"
    ).lower()
    project_capsule_a = agent_a.build_capsule(
        "scope-a-project", "what testing convention should I follow?"
    ).lower()

    assert "favorite color" not in color_capsule_b
    assert "favorite color" in color_capsule_a
    assert "project convention" in project_capsule_a
    assert "project convention" in project_capsule_b
