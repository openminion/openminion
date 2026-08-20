from __future__ import annotations

from pathlib import Path

from openminion.modules.retrieve.runtime.retrieve import RetrieveCtl
from openminion.modules.retrieve.schemas import RetrieveRequest


def _config(tmp_path: Path, *, embeddings_enabled: bool = False) -> dict:
    return {
        "version": 1,
        "retrievectl": {
            "storage": {
                "sqlite_path": str(tmp_path / "retrievectl.db"),
                "blob_root": str(tmp_path / "blob"),
                "wal_mode": False,
            },
            "defaults": {
                "strategy": "contextual",
                "contextual_enabled": True,
                "embeddings_enabled": embeddings_enabled,
                "lexical_candidate_count": 25,
                "snippet_tokens": 120,
                "chunk_target_tokens": 30,
                "chunk_min_tokens": 15,
                "chunk_max_tokens": 35,
                "doc_group_target_tokens": 40,
                "doc_group_min_tokens": 25,
                "doc_group_max_tokens": 60,
                "raptor_internal_k": 2,
                "raptor_leaf_k": 4,
            },
        },
    }


def _service(tmp_path: Path) -> RetrieveCtl:
    return RetrieveCtl(_config(tmp_path))


def test_contextual_retrieve_returns_provenance(tmp_path: Path) -> None:
    service = _service(tmp_path)
    try:
        ingested = service.ingest_source(
            source_type="artifact",
            source_ref="artifact://sha256/" + ("a" * 64),
            text=(
                "Aurora deploy handbook.\n\n"
                "Checklist: run preflight tests, validate migration steps, then deploy.\n\n"
                "Rollback plan: restore previous release artifact and replay idempotent migrations."
            ),
            scope="project",
            tags=["ops", "deploy"],
            title="Aurora deployment handbook",
            unit_kind="chunk",
        )

        rows = service.retrieve(
            query="aurora rollback checklist",
            purpose="act",
            scope={"project": True},
            k=3,
            strategy="contextual",
        )

        assert ingested.unit_count >= 1
        assert rows
        first = rows[0]
        assert first["ref_type"] == "artifact"
        assert first["score"] > 0.0
        assert "relevance=" in first["why"]
        assert isinstance(first["meta"].get("doc_id"), str)
        assert isinstance(first["meta"].get("unit_id"), str)
        assert "score_breakdown" in first["meta"]
    finally:
        service.close()


def test_semantic_strategy_is_public_and_uses_vector_scores(tmp_path: Path) -> None:
    RetrieveRequest(query="alpha", strategy="semantic")
    service = RetrieveCtl(_config(tmp_path, embeddings_enabled=True))
    try:
        service.ingest_source(
            source_type="doc",
            source_ref="doc://semantic/a",
            text="alpha semantic candidate one",
            scope="project",
            tags=["semantic"],
            title="semantic one",
            unit_kind="chunk",
        )
        service.ingest_source(
            source_type="doc",
            source_ref="doc://semantic/b",
            text="alpha semantic candidate two",
            scope="project",
            tags=["semantic"],
            title="semantic two",
            unit_kind="chunk",
        )
        preferred_unit_id = str(
            service.store.execute(
                "SELECT unit_id FROM retrievectl_docs d "
                "JOIN retrievectl_units u ON u.doc_id = d.doc_id "
                "WHERE d.source_ref = ?",
                ("doc://semantic/b",),
            ).fetchone()["unit_id"]
        )

        class _VectorAdapter:
            def search(self, **_kwargs: object) -> list[dict[str, object]]:
                return [{"id": preferred_unit_id, "score": 0.99}]

        service.vector_adapter = _VectorAdapter()

        rows = service.retrieve(
            query="alpha semantic",
            purpose="act",
            scope={"project": True},
            k=2,
            strategy="semantic",
        )

        assert rows
        assert rows[0]["meta"]["unit_id"] == preferred_unit_id
        assert rows[0]["retrieval_strategy"] == "semantic"
    finally:
        service.close()


def test_diagnose_retrieval_reports_counts_and_no_result_reason(tmp_path: Path) -> None:
    service = _service(tmp_path)
    try:
        empty = service.diagnose_retrieval(
            query="  ",
            purpose="act",
            scope={"project": True},
            k=3,
            strategy="contextual",
        )
        missing = service.diagnose_retrieval(
            query="not indexed",
            purpose="act",
            scope={"project": True},
            k=3,
            strategy="contextual",
        )

        assert empty["no_result_reason"] == "empty_query"
        assert missing["no_result_reason"] == "no_candidates"
        assert missing["counts"] == {
            "scored_candidates": 0,
            "after_verify_filter": 0,
            "selected": 0,
            "returned": 0,
        }
        assert missing["strategy_resolution_reason"] == "explicit"
    finally:
        service.close()


def test_tag_filter_matches_exact_json_value_case_insensitively(tmp_path: Path) -> None:
    service = _service(tmp_path)
    try:
        service.ingest_source(
            source_type="doc",
            source_ref="doc://tags/exact",
            text="shared tag filter marker exact",
            scope="project",
            tags=["Ops"],
            title="Exact tag",
        )
        service.ingest_source(
            source_type="doc",
            source_ref="doc://tags/substring",
            text="shared tag filter marker substring",
            scope="project",
            tags=["devops"],
            title="Substring tag",
        )

        rows = service.retrieve(
            query="shared tag filter marker",
            purpose="act",
            scope={"project": True},
            k=5,
            strategy="contextual",
            filters={"tags": ["ops"]},
        )

        assert [row["ref_id"].split("#u=", 1)[0] for row in rows] == [
            "doc://tags/exact"
        ]
    finally:
        service.close()


def test_raptor_build_and_retrieve_mix_internal_and_leaf(tmp_path: Path) -> None:
    service = _service(tmp_path)
    try:
        text = "\n\n".join(
            [
                "Section one explains prerequisites and environment checks for rollout.",
                "Section two captures migration ordering and timing constraints.",
                "Section three details rollback guardrails and recovery tasks.",
                "Section four lists validation checks and metrics to monitor.",
                "Section five provides ownership and escalation instructions.",
            ]
        )
        ingested = service.ingest_source(
            source_type="doc",
            source_ref="doc://aurora/spec",
            text=text,
            scope="project",
            tags=["spec", "handbook"],
            title="Aurora release spec",
            unit_kind="chunk",
        )

        build = service.build_raptor_tree(ingested.doc_id)
        rows = service.retrieve(
            query="summarize rollback and validation requirements",
            purpose="plan",
            scope={"project": True, "doc_heavy": True},
            k=5,
            strategy="raptor",
        )

        assert build["internal_node_count"] >= 1
        assert build["leaf_count"] >= 1
        assert rows
        levels = {item["level"] for item in rows}
        assert "leaf" in levels
        assert "internal" in levels or "root" in levels
    finally:
        service.close()


def test_longrag_prefers_doc_group_units(tmp_path: Path) -> None:
    service = _service(tmp_path)
    try:
        service.ingest_source(
            source_type="doc",
            source_ref="doc://handbook/ops",
            text="\n\n".join(
                [
                    "Operations handbook chapter one: incident triage and containment.",
                    "Chapter two: response coordination and communication templates.",
                    "Chapter three: recovery flow and postmortem checklist.",
                    "Chapter four: controls, approvals, and verification audit trail.",
                ]
            ),
            scope="project",
            tags=["handbook", "ops", "policies"],
            title="Operations handbook",
            corpus_id="handbook",
            unit_kind="chunk",
        )

        grouped = service.group_long_units(
            "handbook", {"min_tokens": 20, "max_tokens": 60}
        )
        rows = service.retrieve(
            query="handbook policy and audit controls",
            purpose="act",
            scope={"project": True},
            k=4,
            strategy="longrag_doc_group",
        )

        assert grouped["groups_created"] >= 1
        assert rows
        assert any(item["unit_kind"] == "doc_group" for item in rows)
        assert rows[0]["unit_kind"] in {"doc_group", "document"}
    finally:
        service.close()


def test_expand_node_returns_leaf_items(tmp_path: Path) -> None:
    service = _service(tmp_path)
    try:
        ingested = service.ingest_source(
            source_type="doc",
            source_ref="doc://expand/raptor",
            text="\n\n".join(
                [
                    "Leaf one includes setup details.",
                    "Leaf two includes deployment details.",
                    "Leaf three includes rollback details.",
                    "Leaf four includes verification details.",
                ]
            ),
            scope="project",
            tags=["spec", "expand"],
            title="Expand test document",
            unit_kind="chunk",
        )
        service.build_raptor_tree(ingested.doc_id)
        rows = service.retrieve(
            query="expand rollback details",
            purpose="plan",
            scope={"project": True, "doc_heavy": True},
            k=3,
            strategy="raptor",
        )
        node_ref = next(
            (
                item["ref_id"]
                for item in rows
                if str(item["ref_id"]).startswith("node://")
            ),
            None,
        )
        assert node_ref is not None

        expanded = service.expand(ref=node_ref, mode="leaves", k=3)
        assert expanded
        assert all(item["raptor_level"] == "leaf" for item in expanded)
    finally:
        service.close()


def test_rlm_compatibility_fields_are_present(tmp_path: Path) -> None:
    service = _service(tmp_path)
    try:
        service.ingest_source(
            source_type="mem",
            source_ref="mem:fact-1",
            text="Deployment window starts at 02:00 UTC and requires a rollback checkpoint.",
            scope="agent",
            tags=["fact", "ops"],
            title="Deploy window",
            unit_kind="chunk",
        )

        rows = service.retrieve(
            query="deployment window",
            purpose="verify",
            scope={"agent": True},
            k=2,
            strategy="contextual",
        )

        assert rows
        row = rows[0]
        assert row["source"] in {"sm", "em", "skill", "session", "wm"}
        assert isinstance(row["text"], str) and row["text"]
        assert row["retrieval_strategy"] in {
            "contextual",
            "raptor",
            "longrag_doc_group",
        }
        assert row["raptor_level"] in {"none", "internal", "leaf"}
        assert row["unit_kind"] in {"chunk", "doc_group", "document"}
    finally:
        service.close()


def test_scope_prefix_normalizes_to_scope_lane(tmp_path: Path) -> None:
    service = _service(tmp_path)
    try:
        service.ingest_source(
            source_type="episode",
            source_ref="session:abc#rowid:1",
            text="inbound: launch codename zephyr for release",
            scope="session:abc",
            tags=["session", "compact"],
            title="Episode row 1",
            unit_kind="chunk",
        )

        rows = service.retrieve(
            query="codename zephyr",
            purpose="act",
            scope={"session": True},
            k=3,
            strategy="contextual",
        )

        assert rows
        assert rows[0]["source"] == "session"
        assert rows[0]["ref_type"] == "episode"
    finally:
        service.close()


def test_skill_ingested_event_is_idempotent_for_same_source_ref(tmp_path: Path) -> None:
    service = _service(tmp_path)
    try:
        first = service.ingest_event(
            "skill.ingested",
            {
                "skill_id": "skill-demo",
                "version_hash": "v1",
                "source_ref": "blob://skill-demo",
                "scope": "agent:demo",
                "text": "Skill can search the web and summarize results.",
                "title": "skill-demo",
            },
        )
        second = service.ingest_event(
            "skill.ingested",
            {
                "skill_id": "skill-demo",
                "version_hash": "v1",
                "source_ref": "blob://skill-demo",
                "scope": "agent:demo",
                "text": "Skill can search the web and summarize results quickly.",
                "title": "skill-demo",
            },
        )

        assert isinstance(first, dict)
        assert isinstance(second, dict)
        assert first["doc_id"] == second["doc_id"]

        row = service.store.execute(
            "SELECT COUNT(*) AS count FROM retrievectl_docs WHERE source_type = 'skill' AND source_ref = ?",
            ("blob://skill-demo",),
        ).fetchone()
        assert row is not None
        assert int(row["count"]) == 1

        rows = service.retrieve(
            query="summarize results quickly",
            purpose="act",
            scope={"agent": True},
            k=3,
            strategy="contextual",
        )
        assert rows
        assert any(item["ref_type"] == "skill" for item in rows)
    finally:
        service.close()
