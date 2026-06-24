from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = REPO_ROOT / "Makefile"
SCRIPTS_DIR = REPO_ROOT / "scripts"


def _makefile_text() -> str:
    return MAKEFILE.read_text(encoding="utf-8")


def _extract_validate_pattern_scripts(text: str) -> list[str]:
    match = re.search(
        r"VALIDATE_PATTERN_SCRIPTS\s*:=\s*\\\n((?:\t[^\n]+\n)+)",
        text,
    )
    assert match, "VALIDATE_PATTERN_SCRIPTS list not found in Makefile"
    body = match.group(1)
    names: list[str] = []
    for line in body.splitlines():
        token = line.strip().rstrip("\\").strip()
        if token:
            names.append(token)
    return names


def test_every_listed_script_exists_in_scripts_dir() -> None:
    names = _extract_validate_pattern_scripts(_makefile_text())
    assert len(names) >= 20, (
        f"VALIDATE_PATTERN_SCRIPTS has {len(names)} entries; "
        "expected at least 20 validators"
    )
    missing: list[str] = []
    for name in names:
        script = SCRIPTS_DIR / f"{name}.py"
        if not script.exists():
            missing.append(name)
    assert not missing, (
        f"VALIDATE_PATTERN_SCRIPTS references nonexistent scripts: {sorted(missing)}"
    )


def test_validate_patterns_target_is_purely_dependency_based() -> None:
    text = _makefile_text()
    # Capture the recipe block: `validate-patterns: <deps>` followed by
    # 0+ recipe lines starting with TAB.
    match = re.search(
        r"^validate-patterns:[^\n]*\n((?:\t[^\n]*\n)*)",
        text,
        re.MULTILINE,
    )
    assert match, "validate-patterns target not found in Makefile"
    recipe_body = match.group(1)
    non_empty = [line for line in recipe_body.splitlines() if line.strip()]
    assert not non_empty, (
        "I-17: validate-patterns must not have a recipe body — it should "
        "be purely dependency-based so `make -j` can parallelize. "
        f"Found recipe lines: {non_empty!r}"
    )


def test_lint_target_invokes_validate_patterns_with_jobs_parallelism() -> None:
    text = _makefile_text()
    # The lint target body should reference `-j $(JOBS) validate-patterns`.
    match = re.search(
        r"-j\s+\$\(JOBS\)\s+validate-patterns",
        text,
    )
    assert match, (
        "I-17: `make lint` must invoke `$(MAKE) -j $(JOBS) validate-patterns` "
        "for the parallel speedup; the pattern was not found."
    )


def test_jobs_variable_is_overridable() -> None:
    text = _makefile_text()
    assert re.search(r"^JOBS\s*\?=", text, re.MULTILINE), (
        "I-17: JOBS must be defined with `?=` so it is operator-overridable; "
        "the override form was not found."
    )


def test_special_arg_validator_has_its_own_target() -> None:
    text = _makefile_text()
    assert "_vp-direct-env-calls" in text, (
        "I-17: the `--fail-on-violation` validator must have its own "
        "`_vp-direct-env-calls` target (the pattern rule only handles "
        "argument-less scripts)."
    )
