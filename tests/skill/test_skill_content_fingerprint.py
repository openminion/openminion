from dataclasses import replace

from openminion.modules.skill.models import SkillPackage


def _package() -> SkillPackage:
    return SkillPackage(
        skill_id="deploy",
        name="deploy",
        display_name=None,
        short_description="Deploy safely",
        default_prompt=None,
        dependency_hints={},
        bundle_metadata={"source": "none", "trust": "untrusted_local"},
        status="draft",
        version_hash="v1",
        source_artifact_ref="artifact://one",
        tags=["ops"],
        tools=[],
        reference_hints=[],
        risk_class="low",
        applies_to={"intents": [], "steps": []},
        inputs_schema=[],
        snippets={},
        recipe=None,
        verification_rules=[],
        rollback_hints=[],
        summary="Deploy safely",
        sections={"procedure": "Run deploy"},
        scope="global",
        agent_id=None,
        source_version=None,
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )


def test_content_fingerprint_ignores_revision_and_admission_metadata() -> None:
    package = _package()
    changed = replace(
        package,
        status="verified",
        version_hash="v2",
        source_artifact_ref="artifact://two",
        created_at="2026-02-01T00:00:00Z",
        updated_at="2026-02-01T00:00:00Z",
        bundle_metadata={"source": "openai", "trust": "trusted_local"},
    )

    assert package.to_content_fingerprint() == changed.to_content_fingerprint()
    assert package.to_version_hash() != changed.to_version_hash()


def test_content_fingerprint_retains_scope_and_agent_binding() -> None:
    package = _package()

    assert (
        package.to_content_fingerprint()
        != replace(
            package, scope="agent", agent_id="agent.one"
        ).to_content_fingerprint()
    )
