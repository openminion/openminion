from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from openminion.modules.retrieve.config import load_config


def _config(
    tmp_path: Path, defaults: dict[str, object] | None = None
) -> dict[str, object]:
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
                "decay_halflife_days": 30,
                "recency_weight": 0.3,
                "k_conversational": 3,
                "k_knowledge": 3,
                "mmr_lambda": 0.6,
                "mmr_enabled": True,
                "feedback_decay_halflife_days": 60,
                "decay_min_feedback_score": 0.0,
                **(defaults or {}),
            },
        },
    }


def test_phase_c_defaults_present_and_typed(tmp_path: Path) -> None:
    cfg = load_config(_config(tmp_path))
    defaults = cfg.defaults
    assert defaults.k_conversational == 3
    assert defaults.k_knowledge == 3
    for field in (
        "expansion_enabled",
        "expansion_score_discount",
        "expansion_max_terms",
        "synonym_map_path",
    ):
        assert not hasattr(defaults, field)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("expansion_enabled", False),
        ("expansion_score_discount", 0.85),
        ("expansion_max_terms", 12),
        ("synonym_map_path", "config/retrieve_synonyms.yaml"),
    ],
)
def test_phase_c_rejects_stale_synonym_expansion_fields(
    tmp_path: Path, field: str, value: object
) -> None:
    with pytest.raises(ValidationError):
        load_config(_config(tmp_path, defaults={field: value}))
