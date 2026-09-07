from pathlib import Path
from types import SimpleNamespace

import pytest

from openminion.cli.interactive.runtime.controls import RuntimeControlsMixin
from openminion.cli.presentation.visible_parity import render_skills_report


class _RuntimeControls(RuntimeControlsMixin):
    agent_id = "agent-1"

    def __init__(self, runtime: object) -> None:
        self._rt = runtime


def _runtime(tmp_path: Path, *, profile: object) -> SimpleNamespace:
    return SimpleNamespace(
        config_path=tmp_path / "openminion.json",
        home_root=tmp_path,
        config=SimpleNamespace(agents={"agent-1": profile}),
    )


def test_skill_rows_merge_catalog_with_config_only_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    instances = []

    class _SkillCatalog:
        def __init__(self, config: object, *, home_root: Path) -> None:
            self.config = config
            self.home_root = home_root
            self.closed = False
            instances.append(self)

        def catalog_summaries(self, *, agent_id: str) -> list[dict[str, object]]:
            assert agent_id == "agent-1"
            return [
                {
                    "id": "persisted-skill",
                    "display_name": "Persisted Skill",
                    "version_hash": "abc123",
                }
            ]

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(
        "openminion.cli.interactive.runtime.controls.Skill", _SkillCatalog
    )
    controls = _RuntimeControls(
        _runtime(
            tmp_path,
            profile=SimpleNamespace(
                skill="persisted-skill",
                skill_catalog=["configured-only", "persisted-skill"],
            ),
        )
    )

    assert controls.list_skill_rows() == [
        {
            "id": "persisted-skill",
            "display_name": "Persisted Skill",
            "version_hash": "abc123",
            "source": "catalog",
        },
        {"id": "configured-only", "source": "config"},
    ]
    assert instances[0].config == tmp_path / "openminion.json"
    assert instances[0].home_root == tmp_path
    assert instances[0].closed is True


def test_skills_report_surfaces_catalog_failure_and_closes_resource(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    instances = []

    class _FailingSkillCatalog:
        def __init__(self, _config: object, *, home_root: Path) -> None:
            self.home_root = home_root
            self.closed = False
            instances.append(self)

        def catalog_summaries(self, *, agent_id: str) -> list[dict[str, object]]:
            raise RuntimeError(f"catalog unavailable for {agent_id}")

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(
        "openminion.cli.interactive.runtime.controls.Skill", _FailingSkillCatalog
    )
    controls = _RuntimeControls(
        _runtime(
            tmp_path,
            profile=SimpleNamespace(skill=None, skill_catalog=[]),
        )
    )

    assert render_skills_report(controls) == (
        "/skills: catalog unavailable for agent-1"
    )
    assert instances[0].closed is True


def test_skills_report_renders_catalog_skill_details_and_closes_resource(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    instances = []
    package = SimpleNamespace(
        display_name="Demo Skill",
        name="demo-skill",
        skill_id="demo_skill",
        status="verified",
        scope="global",
        risk_class="low",
        version_hash="abc123",
        tools=["host.inventory_report"],
        tags=["system"],
        sections={
            "summary": "Collect a local system inventory.",
            "procedure": "Run the inventory tool and verify both artifacts.",
        },
    )

    class _SkillCatalog:
        def __init__(self, config: object, *, home_root: Path) -> None:
            self.config = config
            self.home_root = home_root
            self.closed = False
            instances.append(self)

        def get_skill(self, skill_id: str) -> object:
            assert skill_id == "demo_skill"
            return package

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(
        "openminion.cli.interactive.runtime.controls.Skill", _SkillCatalog
    )
    controls = _RuntimeControls(
        _runtime(
            tmp_path,
            profile=SimpleNamespace(skill=None, skill_catalog=[]),
        )
    )

    report = controls.skills_report("demo_skill")

    assert "Skill: Demo Skill" in report
    assert "id       demo_skill" in report
    assert "tools    host.inventory_report" in report
    assert "Summary:\nCollect a local system inventory." in report
    assert "Procedure:\nRun the inventory tool and verify both artifacts." in report
    assert instances[0].closed is True
