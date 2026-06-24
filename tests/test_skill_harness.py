import tempfile
from pathlib import Path

from openminion.services.integration.skill_harness import run_skill_harness

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


def test_skill_harness_fails_when_no_skills_discovered() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        report = run_skill_harness(Path(tmp))

    assert not report.ok
    assert report.total_skills == 0
    assert report.error_count == 1
    assert len(report.global_errors) >= 1
