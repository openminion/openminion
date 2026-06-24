from __future__ import annotations

from pathlib import Path

import pytest

from openminion.modules.skill.runtime.skill import Skill

SAMPLES_ROOT = Path(__file__).resolve().parents[2] / "examples" / "skills"


PURE_EXTERNAL_SKILL = """
---
name: gh-address-comments
description: Help address review comments on the open GitHub PR using gh CLI.
metadata:
  short-description: Address comments in a GitHub PR review
---

# PR Comment Handler

Guide to find the open PR and address its comments.

## 1) Inspect comments needing attention
Run gh pr view --comments to fetch all comments.

## 2) Apply fixes
Make code changes, commit, push.
""".strip()


EMPTY_BODY_SKILL = """
---
name: empty-skill
description: A skill with no body content.
---
""".strip()


HYBRID_SKILL = """
---
name: hybrid-deploy
description: Deploy the service safely with health checks and rollback steps.
metadata:
  short-description: Safe service deploy
---

# Summary
Deploy the service using the standard guarded rollout.

# Steps
Run deploy.sh --env prod.

# Checks
- Confirm healthcheck passes.

# Recovery
If deploy fails, run rollback.sh.
""".strip()


FRONTMATTERLESS_SKILL = """
# Deploy Runbook

Run deploy.sh --env prod.

## Recovery
If deploy fails, run rollback.sh.
""".strip()


NATIVE_EQUIVALENT_SKILL = """
---
name: gh-address-comments
id: gh-address-comments-native
---

# Summary
Help address review comments on the open GitHub PR using gh CLI.

# Procedure

Guide to find the open PR and address its comments.

## 1) Inspect comments needing attention
Run gh pr view --comments to fetch all comments.

## 2) Apply fixes
Make code changes, commit, push.
""".strip()


def _cfg(tmp_path: Path) -> dict:
    return {
        "skill": {
            "sqlite_path": str(tmp_path / "skill-external-format.db"),
            "wal": False,
            "default_status_filter": ["draft", "verified", "blessed"],
            "high_risk_status_filter": ["blessed", "verified", "draft"],
            "known_tools": ["file", "run_command", "tool.shell"],
        }
    }


@pytest.fixture
def skill_ctl(tmp_path: Path):
    ctl = Skill(_cfg(tmp_path))
    try:
        yield ctl
    finally:
        ctl.close()


def test_ingest_succeeds_and_render_contains_body(skill_ctl: Skill) -> None:
    skill_id, version_hash, warnings = skill_ctl.ingest_text(
        name="gh-address-comments",
        markdown=PURE_EXTERNAL_SKILL,
        scope="global",
    )

    assert skill_id == "gh_address_comments"
    unexpected = [
        warning
        for warning in warnings
        if not warning.startswith("parse.warning:h2_flattened_into_parent:")
    ]
    assert unexpected == [], f"unexpected non-SPSV-01 warnings: {unexpected}"

    package = skill_ctl.get_skill(skill_id, version_hash)
    assert package.bundle_metadata["trust"] == "untrusted_local"
    assert package.summary == "Address comments in a GitHub PR review"
    assert package.to_catalog_summary()["one_liner"] == (
        "Address comments in a GitHub PR review"
    )
    assert package.sections["summary"] == (
        "Help address review comments on the open GitHub PR using gh CLI."
    )

    snippet, _ = skill_ctl.render_snippet(skill_id, version_hash, "act", 400)
    assert len(snippet) > 100
    assert "gh pr view" in snippet
    assert "inspect comments needing attention" in snippet.lower()

    matches = skill_ctl.match(
        intent_text="address pr review comments",
        step_hint={"risk": "low", "verify": False},
        agent_id="agent.test",
        k=5,
    )
    assert any(item.skill_id == skill_id and item.score > 0 for item in matches)

    lint_report = skill_ctl.lint(skill_id, version_hash)
    assert lint_report == {"warnings": [], "errors": []}


def test_summary_catalog_uses_short_description(skill_ctl: Skill) -> None:
    skill_id, version_hash, _ = skill_ctl.ingest_text(
        name="gh-address-comments",
        markdown=PURE_EXTERNAL_SKILL,
    )
    package = skill_ctl.get_skill(skill_id, version_hash)
    assert package.bundle_metadata["trust"] == "untrusted_local"
    assert package.summary == "Address comments in a GitHub PR review"
    assert package.to_catalog_summary()["one_liner"] == (
        "Address comments in a GitHub PR review"
    )


def test_summary_render_and_plan_contains_description(skill_ctl: Skill) -> None:
    skill_id, version_hash, _ = skill_ctl.ingest_text(
        name="gh-address-comments",
        markdown=PURE_EXTERNAL_SKILL,
    )
    package = skill_ctl.get_skill(skill_id, version_hash)
    assert package.bundle_metadata["trust"] == "untrusted_local"
    assert package.sections["summary"] == (
        "Help address review comments on the open GitHub PR using gh CLI."
    )

    snippet, _ = skill_ctl.render_snippet(skill_id, version_hash, "plan", 400)
    assert "Help address review comments on the open GitHub PR using gh CLI." in (
        snippet
    )


def test_lint_procedure_missing_warning(skill_ctl: Skill) -> None:
    skill_id, version_hash, warnings = skill_ctl.ingest_text(
        name="empty-skill",
        markdown=EMPTY_BODY_SKILL,
    )

    assert any("skill.procedure_missing" in item for item in warnings)

    snippet, _ = skill_ctl.render_snippet(skill_id, version_hash, "act", 200)
    assert len(snippet) <= 80

    lint_report = skill_ctl.lint(skill_id, version_hash)
    assert lint_report["errors"] == []
    assert any(
        item["code"] == "skill.procedure_missing" for item in lint_report["warnings"]
    )


def test_native_regression_semantic_equivalence(skill_ctl: Skill) -> None:
    path = SAMPLES_ROOT / "cli-chat-smoke" / "debug" / "SKILL.md"
    skill_id, version_hash, warnings = skill_ctl.ingest_file(path, name="debug")

    assert not any(item.startswith("lint.error:") for item in warnings)

    package = skill_ctl.get_skill(skill_id, version_hash)
    assert {"summary", "procedure", "verification", "rollback"}.issubset(
        package.sections.keys()
    )

    snippet, _ = skill_ctl.render_snippet(skill_id, version_hash, "act", 400)
    lowered = snippet.lower()
    assert "capture error context" in lowered
    assert "recommend fix" in lowered
    assert len(snippet) > 100


def test_hybrid_summary_preserved_without_canonical_duplication(
    skill_ctl: Skill,
) -> None:
    skill_id, version_hash, warnings = skill_ctl.ingest_text(
        name="hybrid-deploy",
        markdown=HYBRID_SKILL,
    )

    assert not any(item.startswith("lint.error:") for item in warnings)
    assert not any("skill.procedure_missing" in item for item in warnings)

    package = skill_ctl.get_skill(skill_id, version_hash)
    assert package.summary == "Deploy the service using the standard guarded rollout."
    assert (
        package.sections["summary"]
        == "Deploy the service using the standard guarded rollout."
    )
    assert package.sections["verification"] == "- Confirm healthcheck passes."

    procedure = package.sections["procedure"]
    assert "Steps:" in procedure
    assert "Recovery:" in procedure
    assert "deploy.sh --env prod" in procedure
    assert "rollback.sh" in procedure
    assert "standard guarded rollout" not in procedure
    assert "healthcheck passes" not in procedure


def test_frontmatterless_ingest_succeeds(skill_ctl: Skill) -> None:
    skill_id, version_hash, warnings = skill_ctl.ingest_text(
        name="frontmatter-free-deploy",
        markdown=FRONTMATTERLESS_SKILL,
    )

    assert not any(item.startswith("lint.error:") for item in warnings)
    assert not any("skill.procedure_missing" in item for item in warnings)

    package = skill_ctl.get_skill(skill_id, version_hash)
    assert package.sections["procedure"]

    snippet, _ = skill_ctl.render_snippet(skill_id, version_hash, "act", 300)
    assert len(snippet) > 60

    lint_report = skill_ctl.lint(skill_id, version_hash)
    assert lint_report["errors"] == []


def test_match_score_gap_within_threshold(skill_ctl: Skill) -> None:
    external_id, _, _ = skill_ctl.ingest_text(
        name="gh-address-comments",
        markdown=PURE_EXTERNAL_SKILL,
    )
    native_id, _, _ = skill_ctl.ingest_text(
        name="gh-address-comments",
        markdown=NATIVE_EQUIVALENT_SKILL,
    )

    matches = skill_ctl.match(
        intent_text="address pr review comments",
        step_hint={"risk": "low", "verify": False},
        agent_id="agent.test",
        k=5,
    )
    score_by_id = {item.skill_id: item.score for item in matches}

    assert external_id in score_by_id
    assert native_id in score_by_id
    assert abs(score_by_id[external_id] - score_by_id[native_id]) <= 2.0


UNKNOWN_FRONT_MATTER_SKILL = """
---
name: external-with-extras
description: External skill with unrecognized front-matter fields.
authors: [alice]
license: MIT
examples:
  - name: example-one
  - name: example-two
metadata:
  short-description: External skill with unknown fields
  weird-nested: ignored
---

# Procedure
Run the command.
""".strip()


def test_unknown_front_matter_keys_produce_warnings_through_ingest(
    skill_ctl: Skill,
) -> None:
    _skill_id, _version_hash, warnings = skill_ctl.ingest_text(
        name="external-with-extras",
        markdown=UNKNOWN_FRONT_MATTER_SKILL,
        scope="global",
    )
    unknown_warnings = sorted(
        warning
        for warning in warnings
        if warning.startswith("parse.warning:unknown_front_matter_key:")
    )
    # authors, license, and examples are top-level unknown keys.
    assert unknown_warnings == sorted(
        [
            "parse.warning:unknown_front_matter_key:authors",
            "parse.warning:unknown_front_matter_key:license",
            "parse.warning:unknown_front_matter_key:examples",
        ]
    )


_BARE_EXTERNAL_SKILL_MD = """\
---
name: bare-external
description: A skill with no companion yaml.
---

# Procedure
Run the command.
"""


def test_ingest_text_does_not_emit_companion_unavailable_warning(
    skill_ctl: Skill,
) -> None:
    _skill_id, _version_hash, warnings = skill_ctl.ingest_text(
        name="bare-external",
        markdown=_BARE_EXTERNAL_SKILL_MD,
        scope="global",
    )
    assert "parse.warning:companion_metadata_unavailable" not in warnings, (
        "ingest_text uses source='not_attempted' and must NOT emit the "
        "companion-unavailable warning"
    )


def test_ingest_file_emits_companion_unavailable_warning_when_no_yaml(
    tmp_path: Path,
    skill_ctl: Skill,
) -> None:
    skill_path = tmp_path / "bare-external.md"
    skill_path.write_text(_BARE_EXTERNAL_SKILL_MD, encoding="utf-8")
    _skill_id, _version_hash, warnings = skill_ctl.ingest_file(skill_path)
    assert "parse.warning:companion_metadata_unavailable" in warnings, (
        f"ingest_file with no companion yaml should emit the SIPS-02 "
        f"warning. got warnings: {warnings}"
    )


def test_ingest_file_silent_when_companion_yaml_present(
    tmp_path: Path,
    skill_ctl: Skill,
) -> None:
    skill_path = tmp_path / "bare-external.md"
    skill_path.write_text(_BARE_EXTERNAL_SKILL_MD, encoding="utf-8")
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "openai.yaml").write_text(
        "interface:\n  display_name: 'Bare External'\n",
        encoding="utf-8",
    )
    _skill_id, _version_hash, warnings = skill_ctl.ingest_file(skill_path)
    assert "parse.warning:companion_metadata_unavailable" not in warnings, (
        f"ingest_file with a present companion yaml must NOT emit the "
        f"SIPS-02 warning. got warnings: {warnings}"
    )
