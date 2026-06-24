from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from openminion.modules.retrieve.runtime import retrieval as retrieval_ops
from openminion.modules.retrieve.runtime.retrieve import RetrieveCtl
from openminion.modules.retrieve.runtime.retrieval import (
    _title_identity_boost,
    generate_candidates,
    select_candidates,
    select_candidates_semantic,
)
from openminion.modules.retrieve.schemas import RetrievalFilters


def _config(tmp_path: Path, *, verify_min_score: float = 0.15) -> dict[str, Any]:
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
                "embeddings_enabled": False,
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
                "verify_min_score": verify_min_score,
            },
        },
    }


def _row(unit_id: str, *, tags: list[str], bm25_score: float) -> dict[str, Any]:
    return {
        "unit_id": unit_id,
        "doc_id": f"doc-{unit_id}",
        "title": f"title {unit_id}",
        "source_type": "doc",
        "source_ref": f"doc://{unit_id}",
        "scope": "project",
        "tags_json": json.dumps(tags),
        "created_at": "2026-05-23T00:00:00+00:00",
        "unit_kind": "chunk",
        "level": "none",
        "node_id": None,
        "group_id": None,
        "text_ref": f"blob://{unit_id}",
        "offsets_json": "{}",
        "bm25_score": bm25_score,
        "hit_count": 0,
        "last_hit_at": None,
        "feedback_score": 0.0,
    }


def test_verify_purpose_excludes_candidates_below_configured_threshold(
    tmp_path: Path,
) -> None:
    ctl = RetrieveCtl(_config(tmp_path, verify_min_score=0.99))
    try:
        ctl.ingest_source(
            source_type="doc",
            source_ref="doc://verify-threshold",
            text="verify threshold candidate text",
            scope="project",
            tags=["verify"],
            title="verify threshold",
        )

        hits = ctl.retrieve(
            query="verify threshold candidate",
            purpose="verify",
            scope={"project": True},
            k=3,
            strategy="contextual",
        )

        assert hits == []
    finally:
        ctl.close()


def test_raptor_expansion_skips_missing_leaf_rows(caplog) -> None:
    class _Defaults:
        raptor_internal_k = 1
        raptor_inheritance_multiplier = 0.92

    class _Config:
        defaults = _Defaults()

    class _Service:
        config = _Config()

        def _leaf_ids_for_node(self, node_id: str) -> list[str]:
            assert node_id == "node-1"
            return ["missing-leaf"]

        def _lookup_unit_rows_batch(
            self, unit_ids: list[str]
        ) -> dict[str, dict[str, Any]]:
            assert unit_ids == ["missing-leaf"]
            return {}

        def _dedupe_candidates(
            self, candidates: list[dict[str, Any]]
        ) -> list[dict[str, Any]]:
            return candidates

    internal = {
        "unit_id": "internal-1",
        "level": "internal",
        "node_id": "node-1",
        "score": 0.7,
    }

    caplog.set_level(logging.WARNING)

    selected = select_candidates(
        _Service(), candidates=[internal], strategy="raptor", k=3
    )

    assert selected == [internal]
    assert "raptor leaf batch lookup returned 0/1 rows" in caplog.text


def test_semantic_search_fallback_logs_when_adapter_raises(caplog) -> None:
    class _Vector:
        def search(self, **_kwargs: Any) -> list[dict[str, Any]]:
            raise ConnectionError("vector down")

    class _Service:
        vector_adapter = _Vector()

    candidates = [
        {"unit_id": "a", "query": "alpha", "score": 0.9},
        {"unit_id": "b", "query": "alpha", "score": 0.8},
    ]
    caplog.set_level(logging.WARNING)

    selected = select_candidates_semantic(_Service(), candidates=candidates, k=2)

    assert [item["unit_id"] for item in selected] == ["a", "b"]
    assert "semantic_search_fallback" in caplog.text


def test_semantic_search_fallback_logs_malformed_adapter_results(caplog) -> None:
    class _Vector:
        def search(self, **_kwargs: Any) -> list[Any]:
            return [None]

    class _Service:
        vector_adapter = _Vector()

    candidates = [
        {"unit_id": "a", "query": "alpha", "score": 0.9},
        {"unit_id": "b", "query": "alpha", "score": 0.8},
    ]
    caplog.set_level(logging.WARNING)

    selected = select_candidates_semantic(_Service(), candidates=candidates, k=2)

    assert [item["unit_id"] for item in selected] == ["a", "b"]
    assert "semantic_search_fallback" in caplog.text


def test_whitespace_only_query_returns_empty_list(tmp_path: Path) -> None:
    ctl = RetrieveCtl(_config(tmp_path))
    try:
        assert (
            ctl.retrieve(
                query="   \n\t  ",
                purpose="act",
                scope={"project": True},
                k=3,
                strategy="contextual",
            )
            == []
        )
    finally:
        ctl.close()


def test_title_identity_boost_zero_overlap_returns_zero() -> None:
    assert (
        _title_identity_boost(
            query_tokens=["alpha", "beta"],
            title="gamma delta",
            max_boost=0.18,
        )
        == 0.0
    )


def test_generate_candidates_overfetches_for_tag_filter(monkeypatch) -> None:
    class _Defaults:
        candidate_overfetch_multiplier = 3
        confidence_memory = 1.0
        confidence_default = 0.6

    class _Config:
        defaults = _Defaults()

    class _Service:
        config = _Config()

        def _allowed_scopes(self, _scope: dict[str, Any]) -> list[str]:
            return []

    observed_limits: list[int] = []

    def _fake_search_rows(*_args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        observed_limits.append(int(kwargs["limit"]))
        return [
            _row("keep-1", tags=["keep"], bm25_score=3.0),
            _row("drop-1", tags=["drop"], bm25_score=2.0),
            _row("keep-2", tags=["keep"], bm25_score=1.0),
            _row("drop-2", tags=["drop"], bm25_score=0.5),
        ]

    monkeypatch.setattr(retrieval_ops, "search_rows", _fake_search_rows)

    candidates = generate_candidates(
        _Service(),
        query="alpha",
        scope={},
        filters=RetrievalFilters(tags=["keep"]),
        limit=2,
    )

    assert observed_limits == [6]
    assert [item["unit_id"] for item in candidates] == ["keep-1", "keep-2"]


def test_generate_candidates_does_not_overfetch_without_post_filters(
    monkeypatch,
) -> None:
    class _Defaults:
        candidate_overfetch_multiplier = 3
        confidence_memory = 1.0
        confidence_default = 0.6

    class _Config:
        defaults = _Defaults()

    class _Service:
        config = _Config()

        def _allowed_scopes(self, _scope: dict[str, Any]) -> list[str]:
            return []

    observed_limits: list[int] = []

    def _fake_search_rows(*_args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        observed_limits.append(int(kwargs["limit"]))
        return []

    monkeypatch.setattr(retrieval_ops, "search_rows", _fake_search_rows)

    assert (
        generate_candidates(
            _Service(),
            query="alpha",
            scope={},
            filters=RetrievalFilters(),
            limit=2,
        )
        == []
    )
    assert observed_limits == [2]
