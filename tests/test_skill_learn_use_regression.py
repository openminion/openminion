from pathlib import Path

import pytest

from openminion.modules.skill import Skill
from openminion.modules.skill.errors import SkillError


def _skill_fixture_path() -> Path:
    return (
        Path(__file__).resolve().parents[1]
        / "examples"
        / "skills"
        / "plan-checkpoints"
        / "SKILL.md"
    )


@pytest.fixture
def skill_path() -> Path:
    path = _skill_fixture_path()
    if not path.exists():
        pytest.skip(f"Skill file not found: {path}")
    return path


def test_skill_module_importable() -> None:
    assert callable(Skill)
    assert issubclass(SkillError, Exception)


def test_skill_ingest_valid_skill(skill_path: Path) -> None:
    ctl = Skill({})
    try:
        skill_id, version_hash, warnings = ctl.ingest_file(
            path=str(skill_path),
            name="test-plan-checkpoints",
        )
        assert skill_id is not None
        assert version_hash is not None
        assert skill_id == "plan-checkpoints"
        assert isinstance(warnings, list)
    finally:
        ctl.close()


def test_skill_list_shows_ingested_skills(skill_path: Path) -> None:
    ctl = Skill({})
    try:
        ctl.ingest_file(path=str(skill_path), name="test-plan-checkpoints")
        skills = ctl.list_skills({})
        assert isinstance(skills, list)
        assert len(skills) >= 1
    finally:
        ctl.close()


def test_skill_ingest_invalid_path_fails_gracefully() -> None:
    ctl = Skill({})
    try:
        with pytest.raises(SkillError) as exc_info:
            ctl.ingest_file(path="/bad/path/SKILL.md", name="test")
        error_dict = exc_info.value.to_dict()
        assert error_dict.get("code") == "PATH_NOT_FOUND"
    finally:
        ctl.close()


def test_skill_debug_provider_exposes_state() -> None:
    from openminion.cli.commands.debug import OpenMinionSkillDebugProvider
    from openminion.services.diagnostics.debug import DebugStatus

    provider = OpenMinionSkillDebugProvider()
    payload = provider._probe()
    assert payload.module == "openminion-skill"
    assert payload.status in [DebugStatus.OK, DebugStatus.WARN, DebugStatus.FAIL]
    assert "import_ok" in payload.details
    assert "skill_count" in payload.details


def test_skill_list_returns_skill_fields() -> None:
    ctl = Skill({})
    try:
        skills = ctl.list_skills({})
        for skill in skills:
            assert "skill_id" in skill
            assert "name" in skill
            assert "status" in skill
    finally:
        ctl.close()
