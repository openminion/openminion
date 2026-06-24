from __future__ import annotations

from typing import Any

from openminion.modules.skill.runtime.selection_rag import narrow_catalog_by_bm25


def _entry(
    skill_id: str,
    name: str,
    description: str = "",
    when_to_use: str = "",
) -> dict[str, Any]:
    return {
        "id": skill_id,
        "name": name,
        "description": description,
        "when_to_use": when_to_use,
    }


def test_narrow_catalog_by_bm25_empty_catalog_returns_empty_list() -> None:
    assert narrow_catalog_by_bm25([], "anything at all", top_k=5) == []
    assert narrow_catalog_by_bm25([], "", top_k=5) == []


def test_narrow_catalog_by_bm25_empty_query_returns_first_top_k() -> None:
    catalog = [_entry(f"id_{i}", f"name_{i}") for i in range(20)]

    result = narrow_catalog_by_bm25(catalog, "", top_k=5)

    assert len(result) == 5
    assert [item["id"] for item in result] == [f"id_{i}" for i in range(5)]


def test_narrow_catalog_by_bm25_top_k_zero_returns_empty() -> None:
    catalog = [_entry(f"id_{i}", f"name_{i}", description="x") for i in range(5)]
    assert narrow_catalog_by_bm25(catalog, "anything", top_k=0) == []
    assert narrow_catalog_by_bm25(catalog, "anything", top_k=-1) == []


def test_narrow_catalog_by_bm25_relevant_query_returns_matching_skills_first() -> None:
    catalog = [
        _entry(f"unrelated_{i}", f"unrelated_skill_{i}", description="cooking pasta")
        for i in range(9)
    ]
    catalog.append(
        _entry(
            "linear_sync",
            "Linear issue sync",
            description="Synchronise Linear issues across projects",
            when_to_use="When the user mentions Linear tickets",
        )
    )

    result = narrow_catalog_by_bm25(catalog, "sync linear issues", top_k=3)

    assert len(result) == 3
    assert result[0]["id"] == "linear_sync"


def test_narrow_catalog_by_bm25_returns_top_k_by_score() -> None:
    catalog = [
        _entry(f"id_{i}", f"name_{i}", description=f"deterministic body {i}")
        for i in range(20)
    ]
    # Boost a specific entry with extra query-term mentions.
    catalog[7] = _entry(
        "id_7",
        "deterministic body 7 deterministic deterministic",
        description="body deterministic deterministic",
    )

    result = narrow_catalog_by_bm25(catalog, "deterministic body", top_k=5)

    assert len(result) == 5
    # The hand-boosted entry must be first.
    assert result[0]["id"] == "id_7"


def test_narrow_catalog_by_bm25_idempotent() -> None:
    catalog = [
        _entry(f"id_{i}", f"name_{i}", description=f"body text payload {i}")
        for i in range(15)
    ]
    catalog[3] = _entry("id_3", "important payload", description="payload payload")

    first = narrow_catalog_by_bm25(catalog, "payload text", top_k=4)
    second = narrow_catalog_by_bm25(catalog, "payload text", top_k=4)

    assert [item["id"] for item in first] == [item["id"] for item in second]


def _build_50_skill_fixture() -> list[dict[str, Any]]:
    base = [
        _entry(
            f"util_{i:02d}",
            f"Utility skill {i}",
            description=f"Generic utility for category {i % 7}",
            when_to_use=f"When you need utility behavior {i}",
        )
        for i in range(48)
    ]
    base.append(
        _entry(
            "git_branch_sync",
            "Sync git branch",
            description="Pull latest changes for a git branch with --ff-only",
            when_to_use="When the user asks to sync a git branch",
        )
    )
    base.append(
        _entry(
            "docker_restart",
            "Restart docker daemon",
            description="Restart docker safely with verification",
            when_to_use="When the user reports docker is misbehaving",
        )
    )
    return base


def test_narrow_catalog_by_bm25_50_skill_fixture() -> None:
    catalog = _build_50_skill_fixture()
    assert len(catalog) == 50

    result = narrow_catalog_by_bm25(catalog, "sync git branch with latest", top_k=5)

    assert len(result) == 5
    assert "git_branch_sync" in {item["id"] for item in result}

    # Idempotency across two calls on the same fixture.
    again = narrow_catalog_by_bm25(catalog, "sync git branch with latest", top_k=5)
    assert [item["id"] for item in result] == [item["id"] for item in again]


def test_narrow_catalog_by_bm25_no_extractable_text_scores_last() -> None:
    catalog = [
        _entry("blank", "", description="", when_to_use=""),
        _entry(
            "matching",
            "matching skill",
            description="this matches the query directly",
        ),
    ]

    result = narrow_catalog_by_bm25(catalog, "matches query", top_k=2)

    assert [item["id"] for item in result] == ["matching", "blank"]


def test_narrow_catalog_by_bm25_returns_full_catalog_when_top_k_exceeds_size() -> None:
    catalog = [_entry(f"id_{i}", f"name_{i}", description="body") for i in range(3)]

    result = narrow_catalog_by_bm25(catalog, "body", top_k=10)

    assert len(result) == 3
    assert {item["id"] for item in result} == {"id_0", "id_1", "id_2"}
