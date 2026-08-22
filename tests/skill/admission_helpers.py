from __future__ import annotations

from pathlib import Path
from typing import Any

from openminion.modules.skill.interfaces import SkillIngestAuthority


def operator_authority() -> SkillIngestAuthority:
    return SkillIngestAuthority.local_operator(
        surface="tests.skill.fixture", principal_id="local:test"
    )


def _admit(ctl: Any, result: tuple[str, str, list[str]]) -> tuple[str, str, list[str]]:
    skill_id, version_hash, warnings = result
    expected = ctl.store.get_active_skill_version_hash(skill_id=skill_id)
    assert ctl.store.activate_skill_version(
        skill_id=skill_id,
        version_hash=version_hash,
        expected_active_version_hash=expected,
        target_status="draft",
        authority_class="local_operator",
        reviewer_id="local:test",
        reason="test fixture admission",
        decided_at="2026-08-22T00:00:00Z",
    )
    filtered = [
        warning
        for warning in warnings
        if warning not in {"admission.pending", "frontmatter.status_non_authoritative"}
    ]
    return skill_id, version_hash, filtered


def ingest_text_and_admit(
    ctl: Any,
    name: str,
    markdown: str,
    scope: str = "global",
    agent_id: str | None = None,
    **kwargs: Any,
) -> tuple[str, str, list[str]]:
    return _admit(
        ctl,
        ctl.ingest_text(
            name=name,
            markdown=markdown,
            scope=scope,
            agent_id=agent_id,
            authority=operator_authority(),
            **kwargs,
        ),
    )


def ingest_file_and_admit(
    ctl: Any,
    path: str | Path,
    **kwargs: Any,
) -> tuple[str, str, list[str]]:
    return _admit(
        ctl,
        ctl.ingest_file(path, authority=operator_authority(), **kwargs),
    )


def ingest_artifact_and_admit(
    ctl: Any,
    source_artifact_ref: str,
    **kwargs: Any,
) -> tuple[str, str, list[str]]:
    return _admit(
        ctl,
        ctl.ingest_artifact(
            source_artifact_ref,
            authority=operator_authority(),
            **kwargs,
        ),
    )
