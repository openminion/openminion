from __future__ import annotations

from openminion.modules.skill.runtime.parser import (
    RECOGNIZED_FRONT_MATTER_KEYS,
    _split_sections,
    build_default_snippets,
    front_matter_unknown_key_warnings,
    normalize_section_name,
    parse_markdown,
    purpose_to_section_keys,
)


def test_h1_only_skill_preserves_canonical_sections() -> None:
    body = """# Summary
Address GitHub PR comments using the gh CLI.

# Procedure
Run `gh pr view --comments` first, then apply fixes locally.

# Verification
- All comments addressed
- CI green
"""
    sections, warnings = _split_sections(body)
    assert "summary" in sections
    assert "procedure" in sections
    assert "verification" in sections
    assert "Address GitHub PR comments" in sections["summary"]
    assert "gh pr view" in sections["procedure"]
    assert "CI green" in sections["verification"]
    assert warnings == []


def test_h1_only_skill_routes_through_alias_map() -> None:
    body = "# Checks\nAll tests pass.\n"
    sections, warnings = _split_sections(body)
    assert "verification" in sections
    assert "checks" not in sections
    assert warnings == []


def test_h2_first_skill_promotes_h2_to_canonical_section() -> None:
    body = """## Summary
Address GitHub PR comments using the gh CLI.

## When To Use
When a PR has reviewer comments needing fixes.

## Verification
- All comments addressed
"""
    sections, warnings = _split_sections(body)
    assert "summary" in sections, f"sections keys: {list(sections.keys())}"
    assert "when_to_use" in sections
    assert "verification" in sections
    assert "Address GitHub PR comments" in sections["summary"]
    assert "When a PR has reviewer comments" in sections["when_to_use"]
    assert warnings == []


def test_h2_first_skill_alias_map_applies_to_h2_titles() -> None:
    body = """## Checks
All tests pass.

## Failure Recovery
Run rollback.sh if anything regresses.
"""
    sections, warnings = _split_sections(body)
    assert "verification" in sections
    assert "rollback" in sections
    assert "checks" not in sections
    assert warnings == []


def test_h2_first_skill_supports_h3_flatten_into_h2_section() -> None:
    body = """## Procedure
Top-level steps.

### Sub-step A
Detail about A.

### Sub-step B
Detail about B.
"""
    sections, warnings = _split_sections(body)
    assert "procedure" in sections
    assert "Top-level steps" in sections["procedure"]
    assert "Sub-step A" in sections["procedure"]
    assert "Sub-step B" in sections["procedure"]
    assert warnings == []


def test_mixed_h1_then_h2_emits_flatten_warning() -> None:
    body = """# Procedure
Top-level procedure intro.

## Inspect comments
Run gh pr view --comments.

## Apply fixes
Make code changes.
"""
    sections, warnings = _split_sections(body)
    assert "procedure" in sections
    assert "## Inspect comments" in sections["procedure"]
    assert "## Apply fixes" in sections["procedure"]
    assert "inspect_comments" not in sections
    assert "apply_fixes" not in sections
    flatten_warnings = [
        warning
        for warning in warnings
        if warning.startswith("parse.warning:h2_flattened_into_parent:")
    ]
    assert len(flatten_warnings) == 2, (
        f"expected 2 h2-flatten warnings, got: {flatten_warnings}"
    )
    flatten_slugs = sorted(warning.split(":")[-1] for warning in flatten_warnings)
    assert flatten_slugs == ["apply_fixes", "inspect_comments"]


def test_mixed_h2_flatten_warning_deduplicates_per_slug() -> None:
    body = """# Procedure
First procedure section.

## Step
Step in first procedure.

# Verification
Verify everything.

## Step
Step in verification.
"""
    sections, warnings = _split_sections(body)
    flatten_warnings = [
        warning
        for warning in warnings
        if warning.startswith("parse.warning:h2_flattened_into_parent:")
    ]
    assert len(flatten_warnings) == 1, (
        f"expected 1 deduped warning, got: {flatten_warnings}"
    )
    assert flatten_warnings[0] == "parse.warning:h2_flattened_into_parent:step"
    assert "Step in first procedure" in sections["procedure"]
    assert "Step in verification" in sections["verification"]


def test_parse_markdown_propagates_flatten_warnings() -> None:
    markdown = """# Procedure
Steps below.

## Sub-step
Detail.
"""
    _front_matter, sections, _summary, warnings = parse_markdown(markdown)
    assert "procedure" in sections
    assert "parse.warning:h2_flattened_into_parent:sub_step" in warnings


def test_parse_markdown_merges_front_matter_and_section_warnings() -> None:
    markdown = """---
name: example
description: unclosed
# Procedure
Body content.

## Sub-step
More content.
"""
    _front_matter, _sections, _summary, warnings = parse_markdown(markdown)
    assert "front_matter.unclosed" in warnings
    flatten = [
        warning
        for warning in warnings
        if warning.startswith("parse.warning:h2_flattened_into_parent:")
    ]
    assert flatten, f"expected at least one flatten warning, got: {warnings}"


def test_parse_markdown_h2_first_emits_no_flatten_warning_when_promoted() -> None:
    markdown = """## Summary
A simple summary.

## Procedure
Steps go here.
"""
    _front_matter, sections, _summary, warnings = parse_markdown(markdown)
    assert "summary" in sections
    assert "procedure" in sections
    flatten = [
        warning
        for warning in warnings
        if warning.startswith("parse.warning:h2_flattened_into_parent:")
    ]
    assert flatten == []


def test_normalize_section_name_is_used_consistently_by_promoted_h2() -> None:
    assert normalize_section_name("Verification") == "verification"
    assert normalize_section_name("Checks") == "verification"
    assert normalize_section_name("Failure Recovery") == "rollback"
    body = "## Checks\ngreen\n"
    sections, _warnings = _split_sections(body)
    assert "verification" in sections


def test_alias_overview_maps_to_summary() -> None:
    assert normalize_section_name("Overview") == "summary"
    assert normalize_section_name("overview") == "summary"


def test_alias_quick_reference_maps_to_summary() -> None:
    assert normalize_section_name("Quick Reference") == "summary"
    assert normalize_section_name("quick reference") == "summary"


def test_alias_quick_start_maps_to_procedure() -> None:
    assert normalize_section_name("Quick Start") == "procedure"
    assert normalize_section_name("Quickstart") != "procedure"


def test_alias_process_maps_to_procedure() -> None:
    assert normalize_section_name("Process") == "procedure"


def test_alias_the_process_maps_to_procedure() -> None:
    assert normalize_section_name("The Process") == "procedure"


def test_h2_first_skill_with_overview_alias_promotes_correctly() -> None:
    body = """## Overview
This skill addresses GitHub PR review comments.

## Process
Run `gh pr view --comments`, then apply fixes.
"""
    sections, warnings = _split_sections(body)
    assert "summary" in sections
    assert "procedure" in sections
    assert "This skill addresses" in sections["summary"]
    assert "gh pr view" in sections["procedure"]
    assert warnings == []


def test_existing_aliases_unchanged_by_spsv_03_additions() -> None:
    assert normalize_section_name("Checks") == "verification"
    assert normalize_section_name("Failure Recovery") == "rollback"
    assert normalize_section_name("Prerequisites") == "preconditions"
    assert normalize_section_name("Usage") == "when_to_use"
    assert normalize_section_name("Skill Card") == "summary"


def test_pitfalls_no_longer_aliased_to_rollback() -> None:
    assert normalize_section_name("Pitfalls") == "pitfalls"
    assert normalize_section_name("pitfalls") == "pitfalls"


def test_rollback_alias_preserved_for_failure_recovery_compounds() -> None:
    assert normalize_section_name("Failure Recovery") == "rollback"
    assert normalize_section_name("Failure and Recovery") == "rollback"
    assert normalize_section_name("Pitfalls and Recovery") == "rollback"


def test_pitfalls_only_skill_renders_pitfalls_in_act_snippet() -> None:
    body = "# Pitfalls\nDo not call the destructive API.\n"
    sections, _warnings = _split_sections(body)
    assert "pitfalls" in sections
    assert "destructive API" in sections["pitfalls"]
    snippets = build_default_snippets(sections)
    assert "destructive API" in snippets["act"]
    assert "pitfalls" in purpose_to_section_keys("act")


def test_rollback_only_skill_renders_rollback_in_act_snippet() -> None:
    body = "# Failure Recovery\nRun rollback.sh and notify oncall.\n"
    sections, _warnings = _split_sections(body)
    assert "rollback" in sections
    assert "pitfalls" not in sections
    assert "rollback.sh" in sections["rollback"]
    snippets = build_default_snippets(sections)
    assert "rollback.sh" in snippets["act"]


def test_skill_with_both_pitfalls_and_rollback_renders_both_distinctly() -> None:
    body = """# Pitfalls
Never call the destructive API.

# Rollback
Run rollback.sh and notify oncall.
"""
    sections, _warnings = _split_sections(body)
    assert "pitfalls" in sections
    assert "rollback" in sections
    assert "destructive API" in sections["pitfalls"]
    assert "rollback.sh" in sections["rollback"]
    snippets = build_default_snippets(sections)
    assert "destructive API" in snippets["act"]
    assert "rollback.sh" in snippets["act"]
    assert "Pitfalls" in snippets["act"]
    assert "Rollback" in snippets["act"]


def test_recognized_front_matter_keys_includes_all_consumed_fields() -> None:
    must_be_recognized = {
        "name",
        "id",
        "status",
        "risk",
        "tools",
        "tags",
        "applies_to",
        "inputs",
        "version",
        "verification",
        "rollback",
        "references",
        "description",
        "metadata",
        "objective",
        "preflight",
        "stop_conditions",
        "safety_notes",
        "idempotency_notes",
    }
    missing = must_be_recognized - RECOGNIZED_FRONT_MATTER_KEYS
    assert not missing, f"recognized set is missing consumed keys: {missing}"


def test_unknown_front_matter_key_warning_fires_once_per_unique_key() -> None:
    warnings = front_matter_unknown_key_warnings(
        {
            "name": "ok",
            "authors": ["alice"],
            "license": "MIT",
            "examples": [],
        }
    )
    assert sorted(warnings) == sorted(
        [
            "parse.warning:unknown_front_matter_key:authors",
            "parse.warning:unknown_front_matter_key:license",
            "parse.warning:unknown_front_matter_key:examples",
        ]
    )


def test_unknown_front_matter_warning_skips_recognized_keys() -> None:
    warnings = front_matter_unknown_key_warnings(
        {
            "name": "ok",
            "id": "ok",
            "status": "verified",
            "tags": ["alpha"],
            "metadata": {"short-description": "x"},
        }
    )
    assert warnings == []


def test_unknown_front_matter_warning_ignores_nested_keys() -> None:
    warnings = front_matter_unknown_key_warnings(
        {
            "name": "ok",
            "metadata": {
                "short-description": "x",
                "weird-nested": "y",
            },
        }
    )
    assert warnings == []


def test_unknown_front_matter_warning_handles_non_dict_input() -> None:
    assert front_matter_unknown_key_warnings(None) == []  # type: ignore[arg-type]
    assert front_matter_unknown_key_warnings([]) == []  # type: ignore[arg-type]


def test_parse_markdown_does_not_emit_unknown_key_warning() -> None:
    markdown = """---
name: ok
authors: [alice]
license: MIT
---

# Procedure
Steps.
"""
    _front_matter, _sections, _summary, warnings = parse_markdown(markdown)
    unknown = [
        warning
        for warning in warnings
        if warning.startswith("parse.warning:unknown_front_matter_key:")
    ]
    assert unknown == [], (
        "parse_markdown should not emit unknown_front_matter_key warnings; "
        "those fire at _build_package time"
    )


def test_references_heading_maps_to_canonical_references() -> None:
    assert normalize_section_name("References") == "references"


def test_reference_files_heading_maps_to_canonical_references() -> None:
    assert normalize_section_name("Reference Files") == "references"


def test_references_front_matter_key_is_recognized() -> None:
    warnings = front_matter_unknown_key_warnings(
        {
            "name": "ok",
            "references": [
                "docs/runbook.md",
                "docs/spec.md",
            ],
        }
    )
    assert warnings == []


def test_references_section_parses_but_is_not_in_default_snippets() -> None:
    body = """# Summary
Use the deployment workflow.

# References
- docs/deploy-runbook.md
- docs/release-checklist.md
"""
    sections, _warnings = _split_sections(body)
    assert "references" in sections
    assert "deploy-runbook" in sections["references"]
    snippets = build_default_snippets(sections)
    assert "deploy-runbook" not in snippets["plan"]
    assert "deploy-runbook" not in snippets["act"]
    assert "deploy-runbook" not in snippets["verify"]
