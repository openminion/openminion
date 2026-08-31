from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal
from collections.abc import Mapping, Sequence

SkillValidationSeverity = Literal[
    "error",
    "warning",
    "info",
]


SkillTestOutcome = Literal[
    "passed",
    "failed",
    "skipped",
]


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class SkillValidationFinding:
    """One typed finding from the validation report."""

    finding_id: str
    severity: SkillValidationSeverity
    code: str
    message: str
    location_ref: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "location_ref": self.location_ref,
        }


@dataclass(frozen=True)
class SkillValidationReport:
    skill_id: str
    package_ref: str
    findings: tuple[SkillValidationFinding, ...]
    lint_summary: Mapping[str, int]
    harness_summary: Mapping[str, int]
    bundle_summary: Mapping[str, Any]
    recipe_summary: Mapping[str, Any]
    readiness: Mapping[str, Any]
    generated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "package_ref": self.package_ref,
            "findings": [item.to_dict() for item in self.findings],
            "lint_summary": dict(self.lint_summary),
            "harness_summary": dict(self.harness_summary),
            "bundle_summary": dict(self.bundle_summary),
            "recipe_summary": dict(self.recipe_summary),
            "readiness": dict(self.readiness),
            "generated_at": self.generated_at,
        }


@dataclass(frozen=True)
class SkillTestScenario:
    scenario_id: str
    skill_id: str
    fixture_ref: str
    expected_outcome: SkillTestOutcome

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "skill_id": self.skill_id,
            "fixture_ref": self.fixture_ref,
            "expected_outcome": self.expected_outcome,
        }


@dataclass(frozen=True)
class SkillTestReport:
    skill_id: str
    scenarios: tuple[SkillTestScenario, ...]
    harness_report_ref: str
    harness_summary: Mapping[str, Any]
    bundle_results: tuple[Mapping[str, Any], ...]
    regression_refs: tuple[str, ...]
    outcome: SkillTestOutcome
    generated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "scenarios": [item.to_dict() for item in self.scenarios],
            "harness_report_ref": self.harness_report_ref,
            "harness_summary": dict(self.harness_summary),
            "bundle_results": [dict(item) for item in self.bundle_results],
            "regression_refs": list(self.regression_refs),
            "outcome": self.outcome,
            "generated_at": self.generated_at,
        }


@dataclass(frozen=True)
class SkillAuthoringDebugView:
    skill_id: str
    package_summary: Mapping[str, Any]
    validation_ref: str
    test_ref: str
    debug_payload_ref: str
    last_error_ref: str
    generated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "package_summary": dict(self.package_summary),
            "validation_ref": self.validation_ref,
            "test_ref": self.test_ref,
            "debug_payload_ref": self.debug_payload_ref,
            "last_error_ref": self.last_error_ref,
            "generated_at": self.generated_at,
        }


def _lint_findings(
    skill_id: str,
    lint_report: Mapping[str, Any] | None,
) -> tuple[tuple[SkillValidationFinding, ...], dict[str, int]]:
    if not isinstance(lint_report, Mapping):
        return (), {"warnings": 0, "errors": 0}
    findings: list[SkillValidationFinding] = []
    warnings_in = lint_report.get("warnings") or []
    errors_in = lint_report.get("errors") or []
    counts = {
        "warnings": len(warnings_in) if isinstance(warnings_in, Sequence) else 0,
        "errors": len(errors_in) if isinstance(errors_in, Sequence) else 0,
    }
    groups: tuple[tuple[SkillValidationSeverity, Any], ...] = (
        ("warning", warnings_in),
        ("error", errors_in),
    )
    for severity, items in groups:
        if isinstance(items, Sequence):
            for index, item in enumerate(items):
                if not isinstance(item, Mapping):
                    continue
                code = str(item.get("code") or item.get("rule_id") or "").strip()
                message = str(item.get("message") or "").strip()
                location_ref = str(
                    item.get("location_ref") or item.get("location") or ""
                ).strip()
                findings.append(
                    SkillValidationFinding(
                        finding_id=f"lint:{skill_id}:{severity}:{index}",
                        severity=severity,
                        code=code,
                        message=message,
                        location_ref=location_ref,
                    )
                )
    return tuple(findings), counts


def _harness_findings(
    skill_id: str,
    harness_result: Any,
) -> tuple[tuple[SkillValidationFinding, ...], dict[str, int]]:
    if harness_result is None:
        return (), {"warnings": 0, "errors": 0}

    warnings = tuple(getattr(harness_result, "warnings", ()) or ())
    errors = tuple(getattr(harness_result, "errors", ()) or ())
    ok = bool(getattr(harness_result, "ok", False))
    skill_root = str(getattr(harness_result, "skill_root", "") or "")
    counts = {
        "warnings": len(warnings),
        "errors": len(errors),
        "ok": 1 if ok else 0,
    }
    findings: list[SkillValidationFinding] = []
    groups: tuple[tuple[SkillValidationSeverity, tuple[Any, ...]], ...] = (
        ("warning", warnings),
        ("error", errors),
    )
    for severity, messages in groups:
        for index, message in enumerate(messages):
            findings.append(
                SkillValidationFinding(
                    finding_id=f"harness:{skill_id}:{severity}:{index}",
                    severity=severity,
                    code=f"harness.{severity}",
                    message=str(message),
                    location_ref=skill_root,
                )
            )
    return tuple(findings), counts


def build_skill_validation_report(
    package: Any,
    *,
    lint_report: Mapping[str, Any] | None,
    verified_lint_report: Mapping[str, Any] | None = None,
    harness_result: Any | None,
    generated_at: str | None = None,
) -> SkillValidationReport:
    skill_id = str(getattr(package, "skill_id", "") or "").strip()
    version_hash = str(getattr(package, "version_hash", "") or "").strip()
    package_ref = (
        f"skill:{skill_id}@{version_hash}" if version_hash else f"skill:{skill_id}"
    )

    lint_findings, lint_counts = _lint_findings(skill_id, lint_report)
    harness_findings, harness_counts = _harness_findings(skill_id, harness_result)
    draft_blockers = _lint_error_codes(lint_report)
    verified_blockers = _lint_error_codes(verified_lint_report)
    recipe = package.recipe
    steps = tuple(recipe.steps) if recipe is not None else ()

    return SkillValidationReport(
        skill_id=skill_id,
        package_ref=package_ref,
        findings=lint_findings + harness_findings,
        lint_summary=dict(lint_counts),
        harness_summary=dict(harness_counts),
        bundle_summary=_bundle_result_summary(harness_result),
        recipe_summary={
            "step_count": len(steps),
            "tool_bindings": [step.tool_id for step in steps if step.tool_id],
        },
        readiness={
            "draft": {"ready": not draft_blockers, "blockers": draft_blockers},
            "verified_admission": {
                "ready": not verified_blockers,
                "blockers": verified_blockers,
            },
        },
        generated_at=generated_at or _iso_now(),
    )


def _lint_error_codes(lint_report: Mapping[str, Any] | None) -> list[str]:
    if not isinstance(lint_report, Mapping):
        return []
    errors = lint_report.get("errors")
    if not isinstance(errors, Sequence):
        return []
    return [
        str(item.get("code") or item.get("rule_id") or "").strip()
        for item in errors
        if isinstance(item, Mapping)
        and str(item.get("code") or item.get("rule_id") or "").strip()
    ]


def _bundle_result_summary(harness_result: Any | None) -> dict[str, Any]:
    if harness_result is None:
        return {}
    return {
        "parse_ok": harness_result.parse_ok,
        "parse_warnings": list(harness_result.parse_warnings),
        "resource_counts": dict(harness_result.resource_counts),
        "unsupported_entries": list(harness_result.unsupported_entries),
        "nested_skill_candidates": list(harness_result.nested_skill_candidates),
        "unknown_front_matter_keys": list(harness_result.unknown_front_matter_keys),
    }


def _scenarios_from_harness(
    harness_report: Any,
) -> tuple[tuple[SkillTestScenario, ...], SkillTestOutcome]:
    if harness_report is None:
        return (), "skipped"

    total_skills = int(getattr(harness_report, "total_skills", 0) or 0)
    ok = bool(getattr(harness_report, "ok", False))
    if total_skills == 0:
        return (), "skipped"

    results = tuple(getattr(harness_report, "results", ()) or ())
    scenarios: list[SkillTestScenario] = []
    for index, result in enumerate(results):
        skill_root = str(getattr(result, "skill_root", "") or "")
        fixture_input = str(getattr(result, "fixture_input_path", "") or "")
        fixture_ref = fixture_input or f"skill_root:{skill_root}"
        scenario_skill_id = skill_root or f"scenario_{index}"
        scenario_id = f"harness:{scenario_skill_id}:{index}"
        result_ok = bool(getattr(result, "ok", False))
        scenarios.append(
            SkillTestScenario(
                scenario_id=scenario_id,
                skill_id=scenario_skill_id,
                fixture_ref=fixture_ref,
                expected_outcome="passed" if result_ok else "failed",
            )
        )
    overall: SkillTestOutcome = "passed" if ok else "failed"
    return tuple(scenarios), overall


def build_skill_test_report(
    skill_root: str,
    *,
    harness_report: Any | None,
    regression_refs: Sequence[str] | None,
    generated_at: str | None = None,
) -> SkillTestReport:
    skill_id = str(skill_root or "").strip()
    scenarios, outcome = _scenarios_from_harness(harness_report)

    harness_report_ref = ""
    if harness_report is not None:
        passed = int(getattr(harness_report, "passed_skills", 0) or 0)
        total = int(getattr(harness_report, "total_skills", 0) or 0)
        harness_report_ref = f"harness:{skill_id}:{passed}/{total}"

    refs = tuple(
        str(item).strip() for item in (regression_refs or ()) if str(item).strip()
    )
    harness_summary: dict[str, Any] = {}
    bundle_results: tuple[Mapping[str, Any], ...] = ()
    if harness_report is not None:
        harness_summary = {
            "ok": harness_report.ok,
            "total_skills": harness_report.total_skills,
            "passed_skills": harness_report.passed_skills,
            "warning_count": harness_report.warning_count,
            "error_count": harness_report.error_count,
        }
        bundle_results = tuple(item.to_dict() for item in harness_report.results)

    return SkillTestReport(
        skill_id=skill_id,
        scenarios=scenarios,
        harness_report_ref=harness_report_ref,
        harness_summary=harness_summary,
        bundle_results=bundle_results,
        regression_refs=refs,
        outcome=outcome,
        generated_at=generated_at or _iso_now(),
    )


def _package_summary(package: Any) -> dict[str, Any]:
    if package is None:
        return {}
    summary_builder = getattr(package, "to_catalog_summary", None)
    if callable(summary_builder):
        summary = summary_builder()
        if isinstance(summary, Mapping):
            return dict(summary)
    skill_id = str(getattr(package, "skill_id", "") or "").strip()
    name = str(getattr(package, "name", "") or "").strip()
    version_hash = str(getattr(package, "version_hash", "") or "").strip()
    return {
        "id": skill_id,
        "name": name,
        "version_hash": version_hash,
    }


def build_skill_authoring_debug_view(
    skill_id: str,
    *,
    package: Any | None,
    debug_payload: Any | None,
    validation_ref: str | None = None,
    test_ref: str | None = None,
    generated_at: str | None = None,
) -> SkillAuthoringDebugView:
    skill_id_clean = str(skill_id or "").strip()
    package_summary = _package_summary(package)

    debug_payload_ref = ""
    last_error_ref = ""
    if debug_payload is not None:
        module_name = ""
        status = ""
        if isinstance(debug_payload, Mapping):
            module_name = str(debug_payload.get("module") or "").strip()
            status = str(debug_payload.get("status") or "").strip()
            last_error = debug_payload.get("last_error")
        else:
            module_name = str(getattr(debug_payload, "module", "") or "").strip()
            status_attr = getattr(debug_payload, "status", "")
            status_value = getattr(status_attr, "value", status_attr)
            status = str(status_value or "").strip()
            last_error = getattr(debug_payload, "last_error", None)
        debug_payload_ref = (
            f"debug:{module_name or 'openminion-skill'}:{status or 'unknown'}"
        )
        if last_error:
            last_error_ref = f"debug:{module_name or 'openminion-skill'}:last_error"

    return SkillAuthoringDebugView(
        skill_id=skill_id_clean,
        package_summary=package_summary,
        validation_ref=str(validation_ref or "").strip()
        or f"validation:{skill_id_clean}",
        test_ref=str(test_ref or "").strip() or f"test:{skill_id_clean}",
        debug_payload_ref=debug_payload_ref,
        last_error_ref=last_error_ref,
        generated_at=generated_at or _iso_now(),
    )


__all__ = (
    "SkillValidationSeverity",
    "SkillTestOutcome",
    "SkillValidationFinding",
    "SkillValidationReport",
    "SkillTestScenario",
    "SkillTestReport",
    "SkillAuthoringDebugView",
    "build_skill_validation_report",
    "build_skill_test_report",
    "build_skill_authoring_debug_view",
)
