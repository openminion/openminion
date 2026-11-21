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
        data_root=tmp_path / "nondefault data",
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
        {"id": "configured-only", "source": "config (unavailable)"},
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


def test_skills_report_explains_configured_only_skill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _SkillCatalog:
        def __init__(self, _config: object, *, home_root: Path) -> None:
            self.home_root = home_root

        def get_skill(self, skill_id: str) -> object:
            from openminion.modules.skill.errors import SkillError

            raise SkillError("NOT_FOUND", "Skill not found", {"skill_id": skill_id})

        def close(self) -> None:
            pass

    monkeypatch.setattr(
        "openminion.cli.interactive.runtime.controls.Skill", _SkillCatalog
    )
    controls = _RuntimeControls(
        _runtime(
            tmp_path,
            profile=SimpleNamespace(skill=None, skill_catalog=["configured-only"]),
        )
    )
    report = controls.skills_report("configured-only")
    assert "status   unavailable" in report
    assert "configured but is not installed" in report


def test_skills_report_discloses_catalog_truncation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _SkillCatalog:
        def __init__(self, _config: object, *, home_root: Path) -> None:
            self.home_root = home_root

        def catalog_summaries(self, *, agent_id: str) -> list[dict[str, object]]:
            return [{"id": f"skill-{index}"} for index in range(21)]

        def close(self) -> None:
            pass

    monkeypatch.setattr(
        "openminion.cli.interactive.runtime.controls.Skill", _SkillCatalog
    )
    controls = _RuntimeControls(
        _runtime(tmp_path, profile=SimpleNamespace(skill=None, skill_catalog=[]))
    )
    assert "showing 20 of 21 skills" in controls.skills_report()


@pytest.mark.parametrize(
    ("observations", "pending"), [(0, 0), (1, 0), (2, 0), (0, 3), (2, 3)]
)
@pytest.mark.parametrize("config_path", ["runtime config.json", None])
def test_skills_report_includes_read_only_learning_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    observations: int,
    pending: int,
    config_path: str | None,
) -> None:
    instances = []

    class _SkillCatalog:
        def __init__(self, _config: object, *, home_root: Path) -> None:
            self.home_root = home_root
            self.store = object()
            self.closed = False
            instances.append(self)

        def catalog_summaries(self, *, agent_id: str) -> list[dict[str, object]]:
            assert agent_id == "agent-1"
            return []

        def list_workflow_observations(
            self, *, agent_id: str
        ) -> list[dict[str, object]]:
            assert agent_id == "agent-1"
            return [{"id": f"obs-{index}"} for index in range(observations)]

        def list_workflow_shapes(self, *, agent_id: str) -> list[object]:
            assert agent_id == "agent-1"
            return (
                [SimpleNamespace(success_count=observations, explicit_save_count=0)]
                if observations
                else []
            )

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(
        "openminion.cli.interactive.runtime.controls.Skill", _SkillCatalog
    )
    monkeypatch.setattr(
        "openminion.cli.interactive.runtime.skill_status.Skill", _SkillCatalog
    )
    monkeypatch.setattr(
        "openminion.cli.interactive.runtime.skill_status.suggestion_status",
        lambda _store: SimpleNamespace(pending_count=pending),
    )
    controls = _RuntimeControls(
        _runtime(
            tmp_path,
            profile=SimpleNamespace(skill=None, skill_catalog=[]),
        )
    )
    controls._rt.config_path = tmp_path / config_path if config_path else None

    report = controls.skills_report()

    if not observations and not pending:
        assert report == "(no skills)"
    else:
        from shlex import split

        assert "Skills:\n  (none)" in report
        assert "Learning:" in report
        assert f"observations       {observations} · active agent" in report
        assert f"authoring ready    {int(observations >= 2)} · active agent" in report
        assert f"pending proposals  {pending} · catalog-wide" in report
        commands = [
            split(line)
            for line in report.splitlines()
            if line.startswith("  skillctl ")
        ]
        prefix = [
            "skillctl",
            "--config",
            str(tmp_path / config_path) if config_path else "SKILL_CONFIG",
            "--home-root",
            str(tmp_path),
            "--data-root",
            str(tmp_path / "nondefault data"),
        ]
        assert commands == [
            [*prefix, "learning-observation-list", "--agent-id", "agent-1"],
            [*prefix, "learning-shape-list", "--agent-id", "agent-1"],
            [*prefix, "proposal-list"],
        ]
        assert "this runtime's config and roots" in report
        assert "docs/guides/verified-learning-and-responsibility.md" in report
    assert all(instance.closed for instance in instances)


def test_skills_report_keeps_catalog_when_learning_data_is_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openminion.modules.skill.learning.runtime import WorkflowObservationError

    class _SkillCatalog:
        def __init__(self, _config: object, *, home_root: Path) -> None:
            self.home_root = home_root
            self.store = object()

        def catalog_summaries(self, *, agent_id: str) -> list[dict[str, object]]:
            assert agent_id == "agent-1"
            return [{"id": "working-skill", "display_name": "Working Skill"}]

        def list_workflow_observations(
            self, *, agent_id: str
        ) -> list[dict[str, object]]:
            raise WorkflowObservationError("stored workflow observation is invalid")

        def close(self) -> None:
            pass

    monkeypatch.setattr(
        "openminion.cli.interactive.runtime.controls.Skill", _SkillCatalog
    )
    monkeypatch.setattr(
        "openminion.cli.interactive.runtime.skill_status.Skill", _SkillCatalog
    )
    controls = _RuntimeControls(
        _runtime(
            tmp_path,
            profile=SimpleNamespace(skill=None, skill_catalog=[]),
        )
    )

    report = controls.skills_report()

    assert "working-skill · catalog" in report
    assert "Learning:" in report
    assert "unavailable         stored workflow observation is invalid" in report
