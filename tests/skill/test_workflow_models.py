from __future__ import annotations

from openminion.modules.skill.models import (
    RecipeStep,
    SkillPackage,
    ToolRecipe,
    WorkflowCatalog,
)


def test_skill_recipe_can_project_to_workflow() -> None:
    package = SkillPackage(
        skill_id="skill.deploy",
        name="deploy",
        display_name="Deploy",
        short_description="Ship the build",
        default_prompt=None,
        dependency_hints={},
        bundle_metadata={},
        status="draft",
        version_hash="v1",
        source_artifact_ref="artifact://skill",
        tags=[],
        tools=[],
        reference_hints=[],
        risk_class="low",
        applies_to={"intents": [], "steps": []},
        inputs_schema=[],
        snippets={},
        recipe=ToolRecipe(
            objective="Deploy safely",
            steps=[
                RecipeStep(
                    step_id="s1",
                    instruction="Check status",
                    tool_id="exec.run",
                ),
                RecipeStep(
                    step_id="s2",
                    instruction="Roll forward",
                    tool_id="exec.run",
                ),
            ],
        ),
        verification_rules=[],
        rollback_hints=[],
        summary="Deploy safely.",
        sections={},
        scope="global",
        agent_id=None,
        source_version=None,
        created_at="2026-05-11T00:00:00+00:00",
        updated_at="2026-05-11T00:00:00+00:00",
    )

    workflow = package.to_workflow()

    assert workflow is not None
    assert workflow.workflow_id == "workflow.skill.deploy"
    assert workflow.source_skill_id == "skill.deploy"
    assert [step.step_id for step in workflow.steps] == ["s1", "s2"]


def test_skill_package_projects_to_workflow_catalog_entry() -> None:
    package = SkillPackage(
        skill_id="skill.deploy",
        name="deploy",
        display_name="Deploy",
        short_description="Ship the build",
        default_prompt=None,
        dependency_hints={},
        bundle_metadata={},
        status="verified",
        version_hash="v1",
        source_artifact_ref="artifact://skill",
        tags=[],
        tools=[],
        reference_hints=[],
        risk_class="low",
        applies_to={"intents": [], "steps": []},
        inputs_schema=[],
        snippets={},
        recipe=ToolRecipe(
            objective="Deploy safely",
            steps=[
                RecipeStep(
                    step_id="s1",
                    instruction="Check status",
                    tool_id="exec.run",
                )
            ],
        ),
        verification_rules=[],
        rollback_hints=[],
        summary="Deploy safely.",
        sections={},
        scope="global",
        agent_id="agent.deploy",
        source_version=None,
        created_at="2026-05-11T00:00:00+00:00",
        updated_at="2026-05-11T00:00:00+00:00",
    )

    entry = package.to_workflow_catalog_entry()

    assert entry is not None
    assert entry.workflow.workflow_id == "workflow.skill.deploy"
    assert entry.skill_id == "skill.deploy"
    assert entry.version_hash == "v1"
    assert entry.agent_id == "agent.deploy"


def test_workflow_catalog_resolves_structural_workflow_id() -> None:
    package = SkillPackage(
        skill_id="skill.deploy",
        name="deploy",
        display_name="Deploy",
        short_description="Ship the build",
        default_prompt=None,
        dependency_hints={},
        bundle_metadata={},
        status="verified",
        version_hash="v1",
        source_artifact_ref="artifact://skill",
        tags=[],
        tools=[],
        reference_hints=[],
        risk_class="low",
        applies_to={"intents": [], "steps": []},
        inputs_schema=[],
        snippets={},
        recipe=ToolRecipe(
            objective="Deploy safely",
            steps=[
                RecipeStep(
                    step_id="s1",
                    instruction="Check status",
                    tool_id="exec.run",
                )
            ],
        ),
        verification_rules=[],
        rollback_hints=[],
        summary="Deploy safely.",
        sections={},
        scope="global",
        agent_id=None,
        source_version=None,
        created_at="2026-05-11T00:00:00+00:00",
        updated_at="2026-05-11T00:00:00+00:00",
    )

    catalog = WorkflowCatalog.from_skill_packages([package])

    entry = catalog.get("workflow.skill.deploy")

    assert entry is not None
    assert entry.workflow.source_skill_id == "skill.deploy"
    assert catalog.get("workflow.missing") is None
