import json
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Sequence


@dataclass(frozen=True)
class SkillHarnessResult:
    skill_root: str
    ok: bool
    warnings: Sequence[str] = field(default_factory=tuple)
    errors: Sequence[str] = field(default_factory=tuple)
    fixture_input_path: str = ""
    fixture_expected_path: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "skill_root": self.skill_root,
            "ok": self.ok,
            "warnings": list(self.warnings),
            "errors": list(self.errors),
            "fixture_input_path": self.fixture_input_path,
            "fixture_expected_path": self.fixture_expected_path,
        }


@dataclass(frozen=True)
class SkillHarnessReport:
    ok: bool
    total_skills: int
    passed_skills: int
    warning_count: int
    error_count: int
    results: Sequence[SkillHarnessResult] = field(default_factory=tuple)
    global_errors: Sequence[str] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "total_skills": self.total_skills,
            "passed_skills": self.passed_skills,
            "warning_count": self.warning_count,
            "error_count": self.error_count,
            "global_errors": list(self.global_errors),
            "results": [result.to_dict() for result in self.results],
        }


def run_skill_harness(root: str | Path = ".") -> SkillHarnessReport:
    project_root = Path(root).expanduser().resolve()
    skill_roots = discover_skill_roots(project_root)
    if not skill_roots:
        return SkillHarnessReport(
            ok=False,
            total_skills=0,
            passed_skills=0,
            warning_count=0,
            error_count=1,
            results=(),
            global_errors=(
                "no skills discovered under examples/, agents/, or skills/",
            ),
        )

    results = tuple(validate_skill(skill_root) for skill_root in skill_roots)
    warning_count = sum(len(item.warnings) for item in results)
    error_count = sum(len(item.errors) for item in results)
    passed_count = sum(item.ok for item in results)
    return SkillHarnessReport(
        ok=error_count == 0,
        total_skills=len(results),
        passed_skills=passed_count,
        warning_count=warning_count,
        error_count=error_count,
        results=results,
        global_errors=(),
    )


def discover_skill_roots(root: Path) -> tuple[Path, ...]:
    candidates = (root / "examples", root / "agents", root / "skills")
    found: dict[str, Path] = {}
    for base in candidates:
        if not base.exists() or not base.is_dir():
            continue
        for skill_file in sorted(base.rglob("SKILL.md")):
            if not skill_file.is_file():
                continue
            skill_root = skill_file.parent.resolve()
            found[str(skill_root)] = skill_root
    return tuple(found[key] for key in sorted(found))


def validate_skill(skill_root: Path) -> SkillHarnessResult:
    skill_file = skill_root / "SKILL.md"
    warnings: list[str] = []
    errors: list[str] = []
    fixture_input = ""
    fixture_expected = ""

    if not skill_file.exists() or not skill_file.is_file():
        errors.append("missing SKILL.md")
        return SkillHarnessResult(
            skill_root=str(skill_root),
            ok=False,
            warnings=tuple(warnings),
            errors=tuple(errors),
            fixture_input_path=fixture_input,
            fixture_expected_path=fixture_expected,
        )

    content = skill_file.read_text(encoding="utf-8").strip()
    if not content:
        errors.append("SKILL.md is empty")
    has_goal = ("## Purpose" in content) or ("## Goal" in content)
    has_recipe = ("## Recipe" in content) or ("## Procedure" in content)
    if not has_goal:
        warnings.append("missing purpose/goal section (`## Purpose` or `## Goal`)")
    if not has_recipe:
        warnings.append(
            "missing recipe/procedure section (`## Recipe` or `## Procedure`)"
        )

    fixtures_root = skill_root / "fixtures"
    if not fixtures_root.exists():
        warnings.append("no fixtures directory (`fixtures/`) for regression checks")
    elif not fixtures_root.is_dir():
        errors.append("fixtures path exists but is not a directory")
    else:
        input_path = fixtures_root / "input.json"
        expected_path = fixtures_root / "expected.txt"
        fixture_input = str(input_path)
        fixture_expected = str(expected_path)
        if not input_path.exists() or not input_path.is_file():
            errors.append("missing fixtures/input.json")
        else:
            try:
                input_payload = json.loads(input_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                errors.append(f"fixtures/input.json invalid json: {exc}")
            else:
                if not isinstance(input_payload, dict):
                    errors.append("fixtures/input.json must contain a JSON object")
        if not expected_path.exists() or not expected_path.is_file():
            errors.append("missing fixtures/expected.txt")
        else:
            expected_content = expected_path.read_text(encoding="utf-8").strip()
            if not expected_content:
                errors.append("fixtures/expected.txt is empty")

    return SkillHarnessResult(
        skill_root=str(skill_root),
        ok=len(errors) == 0,
        warnings=tuple(warnings),
        errors=tuple(errors),
        fixture_input_path=fixture_input,
        fixture_expected_path=fixture_expected,
    )
