from types import SimpleNamespace

from openminion.tools.skill.plugin import _h_skill_ingest, _h_skill_ingest_url


def test_model_text_ingest_rejects_legacy_authority_fields() -> None:
    result = _h_skill_ingest(
        {
            "name": "demo",
            "markdown": "# Procedure\nDo the task safely.",
            "enforce_safety": False,
            "trust": "trusted_local",
        },
        SimpleNamespace(skill_api=object()),
    )

    assert result["error"]["code"] == "SKILL_INGEST_AUTHORITY_OVERRIDE_REJECTED"
    assert result["error"]["details"]["field_names"] == ["enforce_safety", "trust"]


def test_model_url_ingest_rejects_legacy_authority_fields_without_echoing_values() -> (
    None
):
    result = _h_skill_ingest_url(
        {
            "url": "https://example.com/SKILL.md",
            "reviewer_id": "runtime",
        },
        SimpleNamespace(skill_api=object()),
    )

    assert result["error"]["code"] == "SKILL_INGEST_AUTHORITY_OVERRIDE_REJECTED"
    assert result["error"]["details"] == {
        "surface": "model.skill.ingest_url",
        "field_names": ["reviewer_id"],
        "source_kind": "remote",
    }
    assert "runtime" not in str(result)
