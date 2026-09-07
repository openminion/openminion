import tempfile
from pathlib import Path

from openminion.modules.skill.diagnostics import run_skill_harness

SKILL_BODY = "# Skill\n\n## Purpose\nDo hello.\n\n## Recipe\n1. Say hello.\n"


def _write_skill(
    root: Path, *, input_text: str | None = None, expected_text: str | None = None
) -> None:
    skill_root = root / "skills" / "hello"
    if input_text is None and expected_text is None:
        skill_root.mkdir(parents=True)
        skill_root.joinpath("SKILL.md").write_text(
            "# Skill\n\n## Goal\nDo hello.\n\n## Procedure\n1. Say hello.\n",
            encoding="utf-8",
        )
        return

    fixtures_root = skill_root / "fixtures"
    fixtures_root.mkdir(parents=True)
    skill_root.joinpath("SKILL.md").write_text(SKILL_BODY, encoding="utf-8")
    fixtures_root.joinpath("input.json").write_text(input_text or "", encoding="utf-8")
    fixtures_root.joinpath("expected.txt").write_text(
        expected_text or "", encoding="utf-8"
    )


def test_skill_harness_passes_for_valid_fixture_skill() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_skill(
            root, input_text='{"name":"world"}\n', expected_text="hello world\n"
        )

        report = run_skill_harness(root)

    assert report.ok
    assert report.total_skills == 1
    assert report.error_count == 0
    assert report.results[0].ok is True


def test_skill_harness_fails_for_invalid_fixture_json() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_skill(root, input_text="{bad json}\n", expected_text="hello world\n")

        report = run_skill_harness(root)

    assert not report.ok
    assert report.total_skills == 1
    assert report.error_count >= 1
    assert not report.results[0].ok
    assert any("invalid json" in error for error in report.results[0].errors)


def test_skill_harness_warns_when_no_fixtures() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_skill(root)

        report = run_skill_harness(root)

    assert report.ok
    assert report.total_skills == 1
    assert report.error_count == 0
    assert report.warning_count >= 1
    assert any("fixtures" in warning for warning in report.results[0].warnings)


def test_skill_harness_accepts_direct_skill_root() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        skill_root = root / "hello"
        fixtures_root = skill_root / "fixtures"
        fixtures_root.mkdir(parents=True)
        skill_root.joinpath("SKILL.md").write_text(SKILL_BODY, encoding="utf-8")
        fixtures_root.joinpath("input.json").write_text(
            '{"name":"world"}\n', encoding="utf-8"
        )
        fixtures_root.joinpath("expected.txt").write_text(
            "hello world\n", encoding="utf-8"
        )

        report = run_skill_harness(skill_root)

    assert report.ok
    assert report.total_skills == 1
    assert report.results[0].skill_root == str(skill_root.resolve())


def test_skill_harness_fails_when_no_skills_discovered() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        report = run_skill_harness(Path(tmp))

    assert not report.ok
    assert report.total_skills == 0
    assert report.error_count == 1
    assert len(report.global_errors) >= 1


def test_skill_harness_reports_external_bundle_facts() -> None:
    fixture_root = (
        Path(__file__).resolve().parent
        / "skill"
        / "fixtures"
        / "external_catalog"
        / "hermes"
        / "system-inventory"
    )

    report = run_skill_harness(fixture_root)
    result = report.results[0]

    assert result.parse_ok is True
    assert result.resource_counts == {"references": 1, "assets": 0, "scripts": 0}
    assert result.unsupported_entries == ()
    assert result.nested_skill_candidates == ("child/SKILL.md",)
    assert result.unknown_front_matter_keys == (
        "author",
        "platforms",
        "prerequisites",
    )


def test_skill_harness_does_not_double_count_nested_skills(tmp_path: Path) -> None:
    parent = tmp_path / "skills" / "parent"
    child = parent / "child"
    child.mkdir(parents=True)
    parent.joinpath("SKILL.md").write_text(SKILL_BODY, encoding="utf-8")
    child.joinpath("SKILL.md").write_text(SKILL_BODY, encoding="utf-8")

    report = run_skill_harness(tmp_path)

    assert report.total_skills == 1
    assert report.results[0].nested_skill_candidates == ("child/SKILL.md",)
