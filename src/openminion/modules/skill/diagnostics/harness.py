import json
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Sequence

from openminion.modules.skill.runtime.parser import (
    build_recipe,
    front_matter_unknown_key_warnings,
    parse_markdown,
)

_SUPPORTED_RESOURCE_ROOTS = ("references", "assets", "scripts")
_KNOWN_TOP_LEVEL_ENTRIES = {
    "SKILL.md",
    "agents",
    "assets",
    "fixtures",
    "references",
    "scripts",
}
_FATAL_PARSE_WARNINGS = {
    "front_matter.invalid_mapping",
    "front_matter.unclosed",
    "parse.warning:invalid_recipe",
}


@dataclass(frozen=True)
class SkillHarnessResult:
    skill_root: str
    ok: bool
    warnings: Sequence[str] = field(default_factory=tuple)
    errors: Sequence[str] = field(default_factory=tuple)
    fixture_input_path: str = ""
    fixture_expected_path: str = ""
    parse_ok: bool = True
    parse_warnings: Sequence[str] = field(default_factory=tuple)
    resource_counts: dict[str, int] = field(default_factory=dict)
    unsupported_entries: Sequence[str] = field(default_factory=tuple)
    nested_skill_candidates: Sequence[str] = field(default_factory=tuple)
    unknown_front_matter_keys: Sequence[str] = field(default_factory=tuple)
    recipe_step_count: int = 0
    recipe_tool_bindings: Sequence[str] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        return {
            "skill_root": self.skill_root,
            "ok": self.ok,
            "warnings": list(self.warnings),
            "errors": list(self.errors),
            "fixture_input_path": self.fixture_input_path,
            "fixture_expected_path": self.fixture_expected_path,
            "parse_ok": self.parse_ok,
            "parse_warnings": list(self.parse_warnings),
            "resource_counts": dict(self.resource_counts),
            "unsupported_entries": list(self.unsupported_entries),
            "nested_skill_candidates": list(self.nested_skill_candidates),
            "unknown_front_matter_keys": list(self.unknown_front_matter_keys),
            "recipe_step_count": self.recipe_step_count,
            "recipe_tool_bindings": list(self.recipe_tool_bindings),
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
    if (root / "SKILL.md").is_file():
        return (root.resolve(),)

    candidates = (root / "examples", root / "agents", root / "skills")
    found: dict[str, Path] = {}
    for base in candidates:
        if not base.exists() or not base.is_dir():
            continue
        for skill_file in sorted(base.rglob("SKILL.md")):
            if not skill_file.is_file():
                continue
            if _has_skill_ancestor(skill_file.parent, base):
                continue
            skill_root = skill_file.parent.resolve()
            found[str(skill_root)] = skill_root
    return tuple(found[key] for key in sorted(found))


def validate_skill(skill_root: Path) -> SkillHarnessResult:
    skill_file = skill_root / "SKILL.md"
    if not skill_file.is_file():
        return SkillHarnessResult(
            skill_root=str(skill_root),
            ok=False,
            errors=("missing SKILL.md",),
        )

    warnings: list[str] = []
    errors: list[str] = []
    fixture_input = ""
    fixture_expected = ""
    content = skill_file.read_text(encoding="utf-8").strip()
    if not content:
        errors.append("SKILL.md is empty")
    front_matter, sections, _summary, parser_warnings = parse_markdown(content)
    unknown_warnings = front_matter_unknown_key_warnings(front_matter)
    recipe, recipe_warnings = build_recipe(
        front_matter=front_matter,
        skill_name=str(front_matter.get("name", "")).strip() or skill_root.name,
        risk_class=str(front_matter.get("risk", "low")).strip().lower() or "low",
        known_tools=[],
    )
    parse_warnings = tuple(parser_warnings + unknown_warnings + recipe_warnings)
    parse_ok = not any(item in _FATAL_PARSE_WARNINGS for item in parse_warnings)
    if not parse_ok:
        errors.append("SKILL.md has invalid parse structure")
    has_goal = "summary" in sections
    has_recipe = "procedure" in sections
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
        elif not expected_path.read_text(encoding="utf-8").strip():
            errors.append("fixtures/expected.txt is empty")

    resource_counts = {
        root_name: _count_supported_resources(skill_root / root_name)
        for root_name in _SUPPORTED_RESOURCE_ROOTS
    }
    unsupported_entries = tuple(
        path.name
        for path in sorted(skill_root.iterdir(), key=lambda item: item.name)
        if path.name not in _KNOWN_TOP_LEVEL_ENTRIES
    )
    nested_candidates = _nested_skill_candidates(skill_root)
    unknown_keys = tuple(
        warning.rsplit(":", 1)[-1]
        for warning in unknown_warnings
        if warning.startswith("parse.warning:unknown_front_matter_key:")
    )

    return SkillHarnessResult(
        skill_root=str(skill_root),
        ok=not errors,
        warnings=tuple(warnings),
        errors=tuple(errors),
        fixture_input_path=fixture_input,
        fixture_expected_path=fixture_expected,
        parse_ok=parse_ok,
        parse_warnings=parse_warnings,
        resource_counts=resource_counts,
        unsupported_entries=unsupported_entries,
        nested_skill_candidates=nested_candidates,
        unknown_front_matter_keys=unknown_keys,
        recipe_step_count=len(recipe.steps) if recipe else 0,
        recipe_tool_bindings=tuple(
            step.tool_id for step in (recipe.steps if recipe else ()) if step.tool_id
        ),
    )


def _has_skill_ancestor(path: Path, boundary: Path) -> bool:
    current = path.parent
    while current != boundary:
        if (current / "SKILL.md").is_file():
            return True
        if boundary not in current.parents:
            break
        current = current.parent
    return False


def _nested_skill_candidates(skill_root: Path) -> tuple[str, ...]:
    candidates: list[str] = []
    for skill_file in sorted(skill_root.rglob("SKILL.md")):
        if skill_file == skill_root / "SKILL.md":
            continue
        if _has_skill_ancestor(skill_file.parent, skill_root):
            continue
        candidates.append(skill_file.relative_to(skill_root).as_posix())
    return tuple(candidates)


def _count_supported_resources(root: Path) -> int:
    if not root.is_dir():
        return 0
    return sum(path.is_file() and not path.is_symlink() for path in root.rglob("*"))
