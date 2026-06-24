from __future__ import annotations

import builtins
from pathlib import Path

import pytest

from openminion.modules.identity.runtime.bundle_importer import (
    BundleTextDocument,
    ParsedBundleContent,
    build_profile_from_bundle_documents,
    parse_bundle_documents,
)

_BUNDLE_IMPORTER = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "openminion"
    / "modules"
    / "identity"
    / "runtime"
    / "bundle_importer.py"
)


def test_bundle_importer_extracts_agent_soul_and_skill_sections() -> None:
    parsed = parse_bundle_documents(
        [
            BundleTextDocument(
                relative_path="AGENT.md",
                content="""
## Mission
Help users safely and clearly.

## Responsibilities
- Plan steps
- Execute tools

## Constraints
- Ask before destructive commands

## Escalation Policy
- Ask clarifying question when target is ambiguous
                """.strip(),
            ),
            BundleTextDocument(
                relative_path="SOUL.md",
                content="""
## Voice
- Direct
- Friendly

## Values
- Accuracy
- Transparency

## Decision Bias
- Prefer deterministic workflows
                """.strip(),
            ),
            BundleTextDocument(
                relative_path="SKILLS/search/SKILL.md",
                content="# Search skill\nUse search APIs safely.",
            ),
        ]
    )

    assert isinstance(parsed, ParsedBundleContent)
    assert parsed.mission == "Help users safely and clearly."
    assert parsed.responsibilities == ("Plan steps", "Execute tools")
    assert parsed.constraints == ("Ask before destructive commands",)
    assert parsed.escalation_rules == (
        "Ask clarifying question when target is ambiguous",
    )
    assert parsed.voice == ("Direct", "Friendly")
    assert parsed.values == ("Accuracy", "Transparency")
    assert parsed.decision_bias == ("Prefer deterministic workflows",)
    assert parsed.skills == (
        "Use search skill: # Search skill Use search APIs safely.",
    )


def test_bundle_importer_ignores_unrelated_files() -> None:
    parsed = parse_bundle_documents(
        [
            BundleTextDocument(relative_path="NOTES/scratch.md", content="noise"),
            BundleTextDocument(relative_path="README.md", content="ignored"),
        ]
    )

    assert parsed == ParsedBundleContent(
        mission="",
        responsibilities=(),
        constraints=(),
        escalation_rules=(),
        voice=(),
        values=(),
        decision_bias=(),
        skills=(),
    )


def test_bundle_importer_has_no_services_dependency() -> None:
    source = _BUNDLE_IMPORTER.read_text(encoding="utf-8")
    assert "openminion.services" not in source


def test_bundle_importer_parse_is_pure_no_file_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _explode(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("unexpected file I/O")

    monkeypatch.setattr(builtins, "open", _explode)
    parsed = parse_bundle_documents(
        [BundleTextDocument(relative_path="AGENT.md", content="## Mission\nPure parse")]
    )
    assert parsed.mission == "Pure parse"


def test_build_profile_from_bundle_documents_applies_required_defaults() -> None:
    profile = build_profile_from_bundle_documents(
        agent_id="ops-agent",
        documents=[BundleTextDocument(relative_path="README.md", content="ignored")],
    )

    assert profile.agent_id == "ops-agent"
    assert profile.role.mission == "I am ops-agent, a pragmatic AI assistant."
    assert profile.personality.tone == "professional"
    assert profile.risk.risk_level == "medium"
    assert profile.risk.confirm_before == ["destructive_actions"]
    assert profile.tool_posture.tool_use == "allowed"


def test_build_profile_from_bundle_documents_mission_uses_prompt_sentence() -> None:
    profile = build_profile_from_bundle_documents(
        agent_id="ops-agent",
        documents=[],
        system_prompt="Handle requests safely. Keep responses concise.",
    )
    assert profile.role.mission == "Handle requests safely."


def test_bundle_import_mapping_from_hello_agent_fixture() -> None:
    docs = _fixture_documents("hello_agent")
    profile = build_profile_from_bundle_documents(
        agent_id="hello-agent", documents=docs
    )

    assert profile.role.mission == "Help users complete tasks safely."
    assert profile.role.responsibilities[:2] == [
        "Plan steps with clear checkpoints",
        "Execute approved tools",
    ]
    assert any("web research" in item for item in profile.role.responsibilities)
    assert profile.role.hard_constraints == [
        "No destructive actions without confirmation",
        "Ask when target is ambiguous",
    ]
    assert profile.role.escalation_rules == [
        "Escalate when privileged access is required",
    ]
    assert profile.personality.tone == "Pragmatic. Calm and supportive."
    assert profile.personality.interaction_style == [
        "Collaborative",
        "Transparent about assumptions",
    ]
    assert profile.personality.formatting == [
        "Prefer deterministic workflows",
        "Keep outputs auditable",
    ]


def test_bundle_import_mapping_from_incomplete_fixture_uses_defaults() -> None:
    docs = _fixture_documents("incomplete_agent")
    profile = build_profile_from_bundle_documents(
        agent_id="incomplete-agent",
        documents=docs,
        system_prompt="Follow user instructions safely. Keep answers concise.",
    )
    assert profile.role.mission == "Follow user instructions safely."
    assert profile.personality.tone == "professional"
    assert any("diagnostics" in item for item in profile.role.responsibilities)
    assert profile.risk.risk_level == "medium"
    assert profile.tool_posture.tool_use == "allowed"


def _fixture_documents(fixture_name: str) -> list[BundleTextDocument]:
    base = (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "identity"
        / "bundles"
        / fixture_name
    )
    docs: list[BundleTextDocument] = []
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        docs.append(
            BundleTextDocument(
                relative_path=path.relative_to(base).as_posix(),
                content=path.read_text(encoding="utf-8"),
            )
        )
    return docs
