from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


from openminion.modules.skill.authoring import (
    SkillAuthoringDebugView,
    SkillTestOutcome,
    SkillTestReport,
    SkillTestScenario,
    SkillValidationFinding,
    SkillValidationReport,
    SkillValidationSeverity,
    build_skill_authoring_debug_view,
    build_skill_test_report,
    build_skill_validation_report,
)


@dataclass
class _StubPackage:
    skill_id: str = "skill.demo"
    name: str = "demo skill"
    display_name: str | None = "Demo Skill"
    short_description: str | None = "demo"
    summary: str = "demo summary"
    version_hash: str = "deadbeef"
    tags: list[str] | None = None
    tools: list[str] | None = None
    reference_hints: list[str] | None = None

    def to_catalog_summary(self) -> dict[str, Any]:
        return {
            "id": self.skill_id,
            "name": self.display_name,
            "canonical_name": self.name,
            "version_hash": self.version_hash,
            "tags": list(self.tags or []),
            "tools": list(self.tools or []),
            "reference_hints": list(self.reference_hints or []),
        }


@dataclass
class _StubHarnessResult:
    skill_root: str = "/skills/demo"
    ok: bool = True
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    fixture_input_path: str = "/skills/demo/fixtures/input.json"
    fixture_expected_path: str = "/skills/demo/fixtures/expected.txt"


@dataclass
class _StubHarnessReport:
    ok: bool = True
    total_skills: int = 1
    passed_skills: int = 1
    warning_count: int = 0
    error_count: int = 0
    results: tuple[_StubHarnessResult, ...] = ()
    global_errors: tuple[str, ...] = ()


def test_skill_validation_severity_closed_set() -> None:
    assert set(SkillValidationSeverity.__args__) == {"error", "warning", "info"}


def test_skill_test_outcome_closed_set() -> None:
    assert set(SkillTestOutcome.__args__) == {"passed", "failed", "skipped"}


def test_build_skill_validation_report_maps_lint_and_harness() -> None:
    package = _StubPackage()
    lint_report = {
        "warnings": [
            {
                "code": "missing_tags",
                "message": "tags empty",
                "location_ref": "frontmatter.tags",
            }
        ],
        "errors": [
            {
                "code": "missing_purpose",
                "message": "purpose section missing",
                "location_ref": "section.purpose",
            }
        ],
    }
    harness_result = _StubHarnessResult(
        ok=False,
        warnings=("no fixtures dir",),
        errors=("missing fixtures/input.json",),
    )
    report = build_skill_validation_report(
        package,
        lint_report=lint_report,
        harness_result=harness_result,
        generated_at="2026-05-13T00:00:00Z",
    )
    assert isinstance(report, SkillValidationReport)
    assert report.skill_id == "skill.demo"
    assert report.package_ref == "skill:skill.demo@deadbeef"
    assert report.lint_summary == {"warnings": 1, "errors": 1}
    assert report.harness_summary == {"warnings": 1, "errors": 1, "ok": 0}
    severities = sorted(item.severity for item in report.findings)
    assert severities == ["error", "error", "warning", "warning"]
    codes = {item.code for item in report.findings}
    assert {
        "missing_tags",
        "missing_purpose",
        "harness.warning",
        "harness.error",
    } <= codes
    finding_ids = [item.finding_id for item in report.findings]
    assert all(
        any(prefix in fid for prefix in ("lint:", "harness:")) for fid in finding_ids
    )


def test_build_skill_validation_report_determinism() -> None:
    package = _StubPackage()
    lint_report = {"warnings": [], "errors": []}
    harness_result = _StubHarnessResult()
    a = build_skill_validation_report(
        package,
        lint_report=lint_report,
        harness_result=harness_result,
        generated_at="2026-05-13T00:00:00Z",
    )
    b = build_skill_validation_report(
        package,
        lint_report=lint_report,
        harness_result=harness_result,
        generated_at="2026-05-13T00:00:00Z",
    )
    assert a == b


def test_build_skill_validation_report_handles_missing_inputs() -> None:
    package = _StubPackage(version_hash="")
    report = build_skill_validation_report(
        package,
        lint_report=None,
        harness_result=None,
        generated_at="2026-05-13T00:00:00Z",
    )
    assert report.package_ref == "skill:skill.demo"
    assert report.findings == ()
    assert report.lint_summary == {"warnings": 0, "errors": 0}
    assert report.harness_summary == {"warnings": 0, "errors": 0}


def test_build_skill_test_report_passed_outcome() -> None:
    harness_report = _StubHarnessReport(
        results=(_StubHarnessResult(),),
    )
    report = build_skill_test_report(
        "/skills/demo",
        harness_report=harness_report,
        regression_refs=("tests/test_skill_learn_use_regression.py",),
        generated_at="2026-05-13T00:00:00Z",
    )
    assert isinstance(report, SkillTestReport)
    assert report.outcome == "passed"
    assert report.regression_refs == ("tests/test_skill_learn_use_regression.py",)
    assert report.harness_report_ref == "harness:/skills/demo:1/1"
    assert len(report.scenarios) == 1
    assert report.scenarios[0].expected_outcome == "passed"


def test_build_skill_test_report_failed_outcome() -> None:
    failing = _StubHarnessResult(ok=False, errors=("missing fixtures/input.json",))
    harness_report = _StubHarnessReport(
        ok=False,
        passed_skills=0,
        warning_count=0,
        error_count=1,
        results=(failing,),
    )
    report = build_skill_test_report(
        "/skills/demo",
        harness_report=harness_report,
        regression_refs=(),
        generated_at="2026-05-13T00:00:00Z",
    )
    assert report.outcome == "failed"
    assert report.scenarios[0].expected_outcome == "failed"


def test_build_skill_test_report_skipped_outcome_when_no_skills() -> None:
    harness_report = _StubHarnessReport(
        ok=False,
        total_skills=0,
        passed_skills=0,
    )
    report = build_skill_test_report(
        "/skills/demo",
        harness_report=harness_report,
        regression_refs=(),
        generated_at="2026-05-13T00:00:00Z",
    )
    assert report.outcome == "skipped"
    assert report.scenarios == ()


def test_build_skill_test_report_none_harness_is_skipped() -> None:
    report = build_skill_test_report(
        "/skills/demo",
        harness_report=None,
        regression_refs=(),
        generated_at="2026-05-13T00:00:00Z",
    )
    assert report.outcome == "skipped"
    assert report.scenarios == ()
    assert report.harness_report_ref == ""


def test_build_skill_authoring_debug_view_with_mapping_payload() -> None:
    package = _StubPackage()
    debug_payload = {
        "module": "openminion-skill",
        "status": "ok",
        "last_error": None,
    }
    view = build_skill_authoring_debug_view(
        "skill.demo",
        package=package,
        debug_payload=debug_payload,
        generated_at="2026-05-13T00:00:00Z",
    )
    assert isinstance(view, SkillAuthoringDebugView)
    assert view.skill_id == "skill.demo"
    assert view.debug_payload_ref == "debug:openminion-skill:ok"
    assert view.last_error_ref == ""
    assert view.validation_ref == "validation:skill.demo"
    assert view.test_ref == "test:skill.demo"
    assert view.package_summary["id"] == "skill.demo"


def test_build_skill_authoring_debug_view_with_last_error() -> None:
    package = _StubPackage()
    payload = {"module": "openminion-skill", "status": "fail", "last_error": "x"}
    view = build_skill_authoring_debug_view(
        "skill.demo",
        package=package,
        debug_payload=payload,
        validation_ref="validation:skill.demo:abc",
        test_ref="test:skill.demo:abc",
        generated_at="2026-05-13T00:00:00Z",
    )
    assert view.last_error_ref == "debug:openminion-skill:last_error"
    assert view.validation_ref == "validation:skill.demo:abc"
    assert view.test_ref == "test:skill.demo:abc"


def test_build_skill_authoring_debug_view_determinism() -> None:
    package = _StubPackage()
    payload = {"module": "openminion-skill", "status": "ok", "last_error": None}
    a = build_skill_authoring_debug_view(
        "skill.demo",
        package=package,
        debug_payload=payload,
        generated_at="2026-05-13T00:00:00Z",
    )
    b = build_skill_authoring_debug_view(
        "skill.demo",
        package=package,
        debug_payload=payload,
        generated_at="2026-05-13T00:00:00Z",
    )
    assert a == b


def test_authoring_has_no_prose_verdict_fields() -> None:
    forbidden = {
        "verdict",
        "narrative",
        "explanation",
        "judgment",
        "assessment",
        "looks_good",
        "looks_risky",
    }
    for shape in (
        SkillValidationFinding,
        SkillValidationReport,
        SkillTestScenario,
        SkillTestReport,
        SkillAuthoringDebugView,
    ):
        field_names = set(shape.__dataclass_fields__.keys())
        assert field_names & forbidden == set(), (
            f"{shape.__name__} has forbidden prose-verdict field: {field_names & forbidden}"
        )


def test_umbrella_cli_exposes_unified_verbs() -> None:
    from openminion.cli.commands import skill as skill_cmd

    parser = argparse.ArgumentParser(prog="openminion")
    subparsers = parser.add_subparsers(dest="command", required=True)
    skill_cmd.register(subparsers)

    skill_subparser = subparsers.choices["skill"]
    skill_actions = [
        action
        for action in skill_subparser._actions
        if isinstance(action, argparse._SubParsersAction)
    ]
    assert skill_actions, "expected skill subparser to expose subcommands"
    verbs = set(skill_actions[0].choices.keys())
    for verb in (
        "validate",
        "test",
        "debug",
        "ingest",
        "list",
        "show",
        "remove",
        "refresh",
    ):
        assert verb in verbs, f"umbrella CLI missing verb: {verb} (had {sorted(verbs)})"


def test_module_local_cli_exposes_unified_verbs() -> None:
    from openminion.modules.skill import cli as skill_module_cli

    parser = skill_module_cli._build_parser()
    sub_actions = [
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    ]
    assert sub_actions, "expected module-local CLI to expose subcommands"
    verbs = set(sub_actions[0].choices.keys())
    for verb in ("validate", "test", "debug", "lint"):
        assert verb in verbs, (
            f"module-local CLI missing verb: {verb} (had {sorted(verbs)})"
        )


def test_skill_authoring_validation_operator_guide_reference_resolves() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    module_readme = repo_root / "docs/modules/openminion-skill/docs/README.md"
    assert module_readme.exists(), f"missing module README at {module_readme}"
    text = module_readme.read_text(encoding="utf-8")
    assert "skill-authoring-validation.md" in text
    guide = repo_root / "docs/instructions/skill-authoring-validation.md"
    assert guide.exists(), f"missing operator guide at {guide}"


def test_harness_end_to_end_exercises_all_three_builders(tmp_path: Path) -> None:
    from openminion.services.integration.skill_harness import run_skill_harness

    skill_root = tmp_path / "examples" / "demo"
    skill_root.mkdir(parents=True)
    (skill_root / "SKILL.md").write_text(
        "## Purpose\nDemo purpose.\n\n## Recipe\nStep 1.\n",
        encoding="utf-8",
    )
    fixtures = skill_root / "fixtures"
    fixtures.mkdir()
    (fixtures / "input.json").write_text('{"q": "hi"}', encoding="utf-8")
    (fixtures / "expected.txt").write_text("hello", encoding="utf-8")

    harness_report = run_skill_harness(tmp_path)
    assert harness_report.total_skills == 1
    assert harness_report.ok is True
    real_result = harness_report.results[0]

    package = _StubPackage(skill_id="examples.demo", version_hash="abc123")
    validation = build_skill_validation_report(
        package,
        lint_report={"warnings": [], "errors": []},
        harness_result=real_result,
        generated_at="2026-05-13T00:00:00Z",
    )
    test_report = build_skill_test_report(
        str(skill_root),
        harness_report=harness_report,
        regression_refs=("tests/skill/test_authoring.py",),
        generated_at="2026-05-13T00:00:00Z",
    )
    debug_view = build_skill_authoring_debug_view(
        "examples.demo",
        package=package,
        debug_payload={
            "module": "openminion-skill",
            "status": "ok",
            "last_error": None,
        },
        validation_ref=f"validation:examples.demo:{validation.generated_at}",
        test_ref=f"test:examples.demo:{test_report.generated_at}",
        generated_at="2026-05-13T00:00:00Z",
    )

    assert validation.skill_id == "examples.demo"
    assert validation.harness_summary["ok"] == 1
    assert test_report.outcome == "passed"
    assert test_report.scenarios[0].expected_outcome == "passed"
    assert debug_view.skill_id == "examples.demo"
    assert debug_view.debug_payload_ref == "debug:openminion-skill:ok"
    assert debug_view.validation_ref.startswith("validation:examples.demo:")
    assert debug_view.test_ref.startswith("test:examples.demo:")


_ = (Sequence,)
