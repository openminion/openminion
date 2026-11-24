from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest

from openminion.modules.skill.runtime import bundle_metadata
from openminion.modules.skill.runtime.bundle_metadata import (
    BUNDLE_METADATA_SOURCE_NONE,
    BUNDLE_METADATA_SOURCE_NOT_ATTEMPTED,
    BUNDLE_METADATA_SOURCE_OPENAI,
    BUNDLE_METADATA_TRUST_TRUSTED_LOCAL,
    BUNDLE_METADATA_TRUST_TRUSTED_REMOTE,
    BUNDLE_METADATA_TRUST_UNTRUSTED_LOCAL,
    BUNDLE_METADATA_TRUST_UNTRUSTED_REMOTE,
    companion_metadata_unavailable_warning,
    invocation_policy_warnings,
    load_companion_metadata,
    resolve_bundle_metadata_trust,
    validate_bundle_metadata_trust,
)


@pytest.mark.parametrize("value", [False, True, None, "false", 0, [], {}])
def test_invocation_policy_warnings_use_exact_field_presence(value: object) -> None:
    companion = {
        "bundle_metadata": {"payload": {"policy": {"allow_implicit_invocation": value}}}
    }
    assert invocation_policy_warnings(
        {"disable-model-invocation": value}, companion
    ) == [
        "parse.warning:unsupported_invocation_policy:openai.policy.allow_implicit_invocation",
        "parse.warning:unsupported_invocation_policy:claude.disable-model-invocation",
    ]
    assert (
        companion["bundle_metadata"]["payload"]["policy"]["allow_implicit_invocation"]
        is value
    )


@pytest.mark.parametrize(
    "policy",
    [
        None,
        False,
        "allow_implicit_invocation",
        [],
        {},
        {"allow-implicit-invocation": False},
    ],
)
def test_invocation_policy_warnings_ignore_absent_and_misplaced_fields(
    policy: object,
) -> None:
    companion = {
        "bundle_metadata": {
            "payload": {"policy": policy, "allow_implicit_invocation": False}
        }
    }
    assert (
        invocation_policy_warnings(
            {"metadata": {"disable-model-invocation": True}}, companion
        )
        == []
    )
    assert invocation_policy_warnings({}, load_companion_metadata(None)) == []


@pytest.mark.parametrize(
    ("case", "reason"),
    [
        ("leaf", "symlink"),
        ("parent", "symlink"),
        ("broken_leaf", "symlink"),
        ("broken_parent", "symlink"),
        ("directory", "non_regular"),
        ("oversize", "file_size_limit"),
    ],
)
def test_companion_boundary_skips_without_reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str, reason: str
) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "openai.yaml"
    target.write_text("policy: {}", encoding="utf-8")
    agents = root / "agents"
    companion = agents / "openai.yaml"
    if case in {"parent", "broken_parent"}:
        agents.symlink_to(outside if case == "parent" else tmp_path / "missing")
    else:
        agents.mkdir()
        if case in {"leaf", "broken_leaf"}:
            companion.symlink_to(target if case == "leaf" else tmp_path / "missing")
        elif case == "directory":
            companion.mkdir()
        else:
            companion.write_bytes(b"x" * 33 if case == "oversize" else b"policy: {}")
    monkeypatch.setattr(bundle_metadata, "SKILL_BUNDLE_MAX_RESOURCE_BYTES", 32)
    monkeypatch.setattr(
        Path,
        "open",
        lambda *args, **kwargs: pytest.fail("skipped companion was opened"),
    )
    monkeypatch.setattr(
        bundle_metadata,
        "_load_yaml_mapping",
        lambda *args: pytest.fail("skipped companion was parsed"),
    )
    warnings: list[str] = []

    result = load_companion_metadata(root, trust="trusted_local", warnings=warnings)

    assert warnings == [f"parse.warning:companion_metadata_skipped:{reason}"]
    assert result == {
        "display_name": None,
        "short_description": None,
        "default_prompt": None,
        "dependency_hints": {},
        "bundle_metadata": {"source": "none", "trust": "trusted_local"},
    }
    assert (
        companion_metadata_unavailable_warning(result)
        == "parse.warning:companion_metadata_unavailable"
    )
    assert invocation_policy_warnings({}, result) == []


def test_companion_accepts_exact_byte_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "agents").mkdir()
    content = b"policy:\n  allow_implicit_invocation: false\n"
    (tmp_path / "agents/openai.yaml").write_bytes(content)
    monkeypatch.setattr(
        bundle_metadata, "SKILL_BUNDLE_MAX_RESOURCE_BYTES", len(content)
    )
    warnings: list[str] = []
    result = load_companion_metadata(tmp_path, warnings=warnings)
    assert warnings == []
    assert result["bundle_metadata"]["payload"] == {
        "policy": {"allow_implicit_invocation": False}
    }


def test_companion_read_growth_is_bounded_before_parsing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "agents").mkdir()
    (tmp_path / "agents/openai.yaml").write_bytes(b"{}")
    monkeypatch.setattr(bundle_metadata, "SKILL_BUNDLE_MAX_RESOURCE_BYTES", 8)
    reads: list[int] = []

    class GrowingFile(BytesIO):
        def read(self, size: int = -1) -> bytes:
            reads.append(size)
            return super().read(size)

    stream = GrowingFile(b"x" * 100)
    monkeypatch.setattr(Path, "open", lambda *args, **kwargs: stream)
    monkeypatch.setattr(
        bundle_metadata,
        "_load_yaml_mapping",
        lambda *args: pytest.fail("oversized companion was parsed"),
    )
    warnings: list[str] = []
    result = load_companion_metadata(tmp_path, warnings=warnings)
    assert reads == [9]
    assert stream.closed
    assert result["bundle_metadata"]["source"] == "none"
    assert warnings == ["parse.warning:companion_metadata_skipped:file_size_limit"]


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
