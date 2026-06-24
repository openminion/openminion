from __future__ import annotations

from pathlib import Path

import pytest

from openminion.modules.skill.runtime.bundle_metadata import (
    BUNDLE_METADATA_SOURCE_NONE,
    BUNDLE_METADATA_SOURCE_NOT_ATTEMPTED,
    BUNDLE_METADATA_SOURCE_OPENAI,
    BUNDLE_METADATA_TRUST_TRUSTED_LOCAL,
    BUNDLE_METADATA_TRUST_TRUSTED_REMOTE,
    BUNDLE_METADATA_TRUST_UNTRUSTED_LOCAL,
    BUNDLE_METADATA_TRUST_UNTRUSTED_REMOTE,
    companion_metadata_unavailable_warning,
    load_companion_metadata,
    resolve_bundle_metadata_trust,
    validate_bundle_metadata_trust,
)


def test_load_companion_metadata_returns_not_attempted_when_bundle_root_is_none() -> (
    None
):
    result = load_companion_metadata(None)
    assert result["bundle_metadata"]["source"] == BUNDLE_METADATA_SOURCE_NOT_ATTEMPTED
    assert result["bundle_metadata"]["trust"] == BUNDLE_METADATA_TRUST_UNTRUSTED_LOCAL
    assert result["display_name"] is None
    assert result["short_description"] is None
    assert result["default_prompt"] is None
    assert result["dependency_hints"] == {}


def test_load_companion_metadata_returns_none_when_bundle_root_has_no_companion(
    tmp_path: Path,
) -> None:
    result = load_companion_metadata(tmp_path)
    assert result["bundle_metadata"]["source"] == BUNDLE_METADATA_SOURCE_NONE
    assert result["bundle_metadata"]["trust"] == BUNDLE_METADATA_TRUST_UNTRUSTED_LOCAL
    assert result["display_name"] is None
    assert result["dependency_hints"] == {}


def test_load_companion_metadata_returns_openai_when_yaml_present(
    tmp_path: Path,
) -> None:
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    yaml_path = agents_dir / "openai.yaml"
    yaml_path.write_text(
        "interface:\n"
        "  display_name: 'Example Skill'\n"
        "  short_description: 'A test skill'\n"
        "  default_prompt: 'Use the example skill.'\n"
        "dependencies:\n"
        "  tools:\n"
        "    - file.read\n",
        encoding="utf-8",
    )
    result = load_companion_metadata(tmp_path)
    assert result["bundle_metadata"]["source"] == BUNDLE_METADATA_SOURCE_OPENAI
    assert result["bundle_metadata"]["trust"] == BUNDLE_METADATA_TRUST_UNTRUSTED_LOCAL
    assert result["display_name"] == "Example Skill"
    assert result["short_description"] == "A test skill"
    assert result["default_prompt"] == "Use the example skill."
    assert result["dependency_hints"]["tools"] == ["file.read"]
    assert result["bundle_metadata"]["path"] == "agents/openai.yaml"
    assert result["bundle_metadata"]["payload"]["interface"]["display_name"] == (
        "Example Skill"
    )


def test_load_companion_metadata_handles_invalid_yaml_gracefully(
    tmp_path: Path,
) -> None:
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "openai.yaml").write_text("not a mapping", encoding="utf-8")
    result = load_companion_metadata(tmp_path)
    assert result["bundle_metadata"]["source"] == BUNDLE_METADATA_SOURCE_OPENAI
    assert result["bundle_metadata"]["trust"] == BUNDLE_METADATA_TRUST_UNTRUSTED_LOCAL
    assert result["display_name"] is None


def test_unavailable_warning_fires_on_none_state() -> None:
    metadata = {
        "bundle_metadata": {
            "source": BUNDLE_METADATA_SOURCE_NONE,
            "trust": BUNDLE_METADATA_TRUST_UNTRUSTED_LOCAL,
        }
    }
    assert (
        companion_metadata_unavailable_warning(metadata)
        == "parse.warning:companion_metadata_unavailable"
    )


def test_unavailable_warning_silent_on_not_attempted_state() -> None:
    metadata = {
        "bundle_metadata": {
            "source": BUNDLE_METADATA_SOURCE_NOT_ATTEMPTED,
            "trust": BUNDLE_METADATA_TRUST_UNTRUSTED_LOCAL,
        }
    }
    assert companion_metadata_unavailable_warning(metadata) is None


def test_unavailable_warning_silent_on_openai_state() -> None:
    metadata = {
        "bundle_metadata": {
            "source": BUNDLE_METADATA_SOURCE_OPENAI,
            "trust": BUNDLE_METADATA_TRUST_UNTRUSTED_LOCAL,
            "path": "agents/openai.yaml",
        }
    }
    assert companion_metadata_unavailable_warning(metadata) is None


def test_unavailable_warning_handles_missing_or_malformed_bundle_block() -> None:
    assert companion_metadata_unavailable_warning({}) is None
    assert companion_metadata_unavailable_warning({"bundle_metadata": None}) is None
    assert companion_metadata_unavailable_warning({"bundle_metadata": "weird"}) is None
    assert companion_metadata_unavailable_warning({"bundle_metadata": {}}) is None


def test_load_companion_metadata_rejects_unknown_source_value(
    tmp_path: Path,
) -> None:
    from openminion.modules.skill.runtime.bundle_metadata import (
        _empty_companion_metadata,
    )

    with pytest.raises(ValueError, match="bundle_metadata.source must be one of"):
        _empty_companion_metadata(
            "invented_state",
            trust=BUNDLE_METADATA_TRUST_UNTRUSTED_LOCAL,
        )


def test_validate_bundle_metadata_trust_accepts_four_values() -> None:
    assert validate_bundle_metadata_trust("trusted_local") == "trusted_local"
    assert validate_bundle_metadata_trust("trusted_remote") == "trusted_remote"
    assert validate_bundle_metadata_trust("untrusted_local") == "untrusted_local"
    assert validate_bundle_metadata_trust("untrusted_remote") == "untrusted_remote"


def test_validate_bundle_metadata_trust_rejects_unknown_value() -> None:
    with pytest.raises(ValueError, match="bundle_metadata.trust must be one of"):
        validate_bundle_metadata_trust("mystery")


def test_resolve_bundle_metadata_trust_defaults_by_source_kind() -> None:
    assert (
        resolve_bundle_metadata_trust(None, remote=False)
        == BUNDLE_METADATA_TRUST_UNTRUSTED_LOCAL
    )
    assert (
        resolve_bundle_metadata_trust(None, remote=True)
        == BUNDLE_METADATA_TRUST_UNTRUSTED_REMOTE
    )


def test_resolve_bundle_metadata_trust_accepts_explicit_overrides() -> None:
    assert (
        resolve_bundle_metadata_trust("trusted_local", remote=False)
        == BUNDLE_METADATA_TRUST_TRUSTED_LOCAL
    )
    assert (
        resolve_bundle_metadata_trust("trusted_remote", remote=True)
        == BUNDLE_METADATA_TRUST_TRUSTED_REMOTE
    )
