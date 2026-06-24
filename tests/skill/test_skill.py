from __future__ import annotations

import uuid
from pathlib import Path

from openminion.modules.skill.runtime.parser import normalize_render_purpose
from openminion.modules.skill.runtime.skill import Skill


def _cfg(tmp_path: Path) -> dict:
    return {
        "skill": {
            "sqlite_path": str(tmp_path / "skill.db"),
            "wal": False,
            "default_status_filter": ["draft", "verified", "blessed"],
            "high_risk_status_filter": ["blessed", "verified"],
            "known_tools": ["tool.shell", "tool.ssh", "tool.log"],
        }
    }


DOCKER_SKILL = """
---
name: Restart Docker Services Safely
id: docker_restart_safe
status: draft
tags: [docker, ops]
tools: [tool.shell, tool.log]
risk: medium
applies_to:
  intents: [restart docker, fix docker daemon]
  steps: [ToolStep:tool.shell]
verification:
  - systemctl status docker
rollback:
  - systemctl restart docker
---

## Summary
Safely restart docker and verify daemon health.

## Preconditions
- Ensure host is reachable.
- Ensure caller has sudo access.

## Procedure
- tool.shell run "systemctl restart docker"
- tool.log tail "docker"

## Verification
- tool.shell run "systemctl status docker"

## Rollback
- tool.shell run "systemctl restart docker"

## Pitfalls
- Avoid restarting repeatedly if daemon is flapping.
""".strip()

GIT_SKILL = """
---
name: Sync Git Branch
id: git_sync_branch
status: verified
tags: [git, dev]
tools: [tool.shell]
risk: low
applies_to:
  intents: [sync branch, pull latest]
---

## Summary
Pull latest changes for a git branch.

## Procedure
- tool.shell run "git fetch --all"
- tool.shell run "git pull --ff-only"
""".strip()


HIGH_RISK_NO_VERIFY = """
---
name: Dangerous Disk Format
id: dangerous_disk_format
status: blessed
tools: [tool.shell]
risk: high
---

## Summary
Format a disk.

## Procedure
- tool.shell run "mkfs.ext4 /dev/sda"
""".strip()


def test_ingest_and_get_skill(tmp_path: Path) -> None:
    ctl = Skill(_cfg(tmp_path))
    try:
        skill_id, version_hash, warnings = ctl.ingest_text(
            name="Restart Docker Services Safely", markdown=DOCKER_SKILL
        )

        assert skill_id == "docker_restart_safe"
        assert len(version_hash) == 64
        assert warnings == []

        package = ctl.get_skill(skill_id, version_hash)
        assert package.name == "Restart Docker Services Safely"
        assert package.status == "draft"
        assert package.risk_class == "medium"
        assert package.recipe is not None
        assert package.recipe.verification
    finally:
        ctl.close()


def test_match_prefers_intent_and_tool_alignment(tmp_path: Path) -> None:
    ctl = Skill(_cfg(tmp_path))
    try:
        docker_id, docker_ver, _ = ctl.ingest_text(
            name="Restart Docker Services Safely", markdown=DOCKER_SKILL
        )
        git_id, git_ver, _ = ctl.ingest_text(name="Sync Git Branch", markdown=GIT_SKILL)
        assert docker_id and docker_ver and git_id and git_ver

        matches = ctl.match(
            intent_text="restart docker and inspect daemon logs",
            step_hint={"tool_id": "tool.shell", "risk": "medium", "verify": True},
            agent_id="agent.ops",
            k=3,
        )

        assert matches
        assert matches[0].skill_id == "docker_restart_safe"
        assert matches[0].score >= matches[-1].score
    finally:
        ctl.close()


def test_match_does_not_use_generic_procedure_token_overlap(tmp_path: Path) -> None:
    ctl = Skill(_cfg(tmp_path))
    try:
        docker_id, docker_ver, _ = ctl.ingest_text(
            name="Restart Docker Services Safely",
            markdown=DOCKER_SKILL,
        )
        git_id, git_ver, _ = ctl.ingest_text(
            name="Sync Git Branch",
            markdown=GIT_SKILL,
        )
        assert docker_id and docker_ver and git_id and git_ver

        matches = ctl.match(
            intent_text="run verify status and proceed safely",
            step_hint={"risk": "low", "verify": False},
            agent_id="agent.ops",
            k=5,
        )

        ids = [item.skill_id for item in matches]
        assert "docker_restart_safe" not in ids
        assert "git_sync_branch" not in ids
    finally:
        ctl.close()


def test_render_snippet_is_budgeted(tmp_path: Path) -> None:
    ctl = Skill(_cfg(tmp_path))
    try:
        skill_id, version_hash, _ = ctl.ingest_text(
            name="Restart Docker Services Safely", markdown=DOCKER_SKILL
        )
        snippet, snippet_hash = ctl.render_snippet(
            skill_id=skill_id,
            version_hash=version_hash,
            purpose="act",
            max_tokens=40,
        )

        assert snippet
        assert "Skill:" in snippet
        assert len(snippet_hash) == 64
        assert len(snippet) <= 220
    finally:
        ctl.close()


def test_render_snippet_accepts_mode_names(tmp_path: Path) -> None:
    ctl = Skill(_cfg(tmp_path))
    try:
        skill_id, version_hash, _ = ctl.ingest_text(
            name="Restart Docker Services Safely", markdown=DOCKER_SKILL
        )

        respond_snippet, _ = ctl.render_snippet(
            skill_id=skill_id,
            version_hash=version_hash,
            purpose="respond",
            max_tokens=160,
        )
        act_snippet, _ = ctl.render_snippet(
            skill_id=skill_id,
            version_hash=version_hash,
            purpose="act",
            max_tokens=220,
        )

        assert "Procedure:" not in respond_snippet
        assert "Safety Notes:" not in respond_snippet
        assert "Safety Notes:" in act_snippet
    finally:
        ctl.close()


def test_render_snippet_accepts_decide_purpose_as_plan_alias(tmp_path: Path) -> None:
    ctl = Skill(_cfg(tmp_path))
    try:
        skill_id, version_hash, _ = ctl.ingest_text(
            name="Restart Docker Services Safely", markdown=DOCKER_SKILL
        )

        assert normalize_render_purpose("decide") == "plan"

        snippet, _ = ctl.render_snippet(
            skill_id=skill_id,
            version_hash=version_hash,
            purpose="decide",
            max_tokens=120,
        )

        assert snippet
        assert "Skill:" in snippet
    finally:
        ctl.close()


def test_render_snippet_mode_name_can_override_generic_purpose(tmp_path: Path) -> None:
    ctl = Skill(_cfg(tmp_path))
    try:
        skill_id, version_hash, _ = ctl.ingest_text(
            name="Restart Docker Services Safely",
            markdown=DOCKER_SKILL,
        )
        act_snippet, _ = ctl.render_snippet(
            skill_id=skill_id,
            version_hash=version_hash,
            purpose="act",
            max_tokens=220,
        )

        snippet, _ = ctl.render_snippet(
            skill_id=skill_id,
            version_hash=version_hash,
            purpose="act",
            mode_name="respond",
            max_tokens=160,
        )

        assert "Safety Notes:" in act_snippet
        assert "Safety Notes:" not in snippet
    finally:
        ctl.close()


def test_workflow_catalog_and_lookup_are_structural(tmp_path: Path) -> None:
    ctl = Skill(_cfg(tmp_path))
    try:
        skill_id, _, _ = ctl.ingest_text(
            name="Restart Docker Services Safely",
            markdown=DOCKER_SKILL,
        )

        catalog = ctl.workflow_catalog()
        entry = ctl.get_workflow(f"workflow.{skill_id}")

        assert entry.workflow.workflow_id == f"workflow.{skill_id}"
        assert entry.skill_id == skill_id
        assert catalog.get(f"workflow.{skill_id}") is not None
    finally:
        ctl.close()


def test_lint_forces_draft_when_high_risk_missing_verification(tmp_path: Path) -> None:
    ctl = Skill(_cfg(tmp_path))
    try:
        skill_id, version_hash, warnings = ctl.ingest_text(
            name="Dangerous Disk Format",
            markdown=HIGH_RISK_NO_VERIFY,
        )

        assert skill_id == "dangerous_disk_format"
        assert any("lint.forced_status_draft" in item for item in warnings)

        package = ctl.get_skill(skill_id, version_hash)
        assert package.status == "draft"

        lint_report = ctl.lint(skill_id, version_hash)
        assert lint_report["errors"]
        assert any(
            item["code"] == "verification.required" for item in lint_report["errors"]
        )
    finally:
        ctl.close()


def test_log_run_records_outcome(tmp_path: Path) -> None:
    ctl = Skill(_cfg(tmp_path))
    try:
        skill_id, version_hash, _ = ctl.ingest_text(
            name="Restart Docker Services Safely", markdown=DOCKER_SKILL
        )
        run_id = ctl.log_run(
            session_id="session-1",
            agent_id="agent.ops",
            skill_id=skill_id,
            version_hash=version_hash,
            used_for="act",
            outcome="success",
            evidence_refs=["artifact://sha256/abc123"],
        )
        assert str(uuid.UUID(run_id)) == run_id
    finally:
        ctl.close()


# M4: Negative-path / hardening tests

DANGEROUS_SKILL = """
---
name: Dangerous Wipe Skill
id: dangerous_wipe
status: verified
tools: [tool.shell]
risk: low
verification:
  - echo ok
---

## Summary
Wipe a disk.

## Procedure
- tool.shell run "rm -rf /mnt/data"
- tool.shell run "dd if=/dev/zero of=/dev/sda"
""".strip()

SAFE_RECIPE_SKILL = """
---
name: Safe Recipe Skill
id: safe_recipe
status: draft
tools: [tool.shell]
risk: low
---

## Summary
Echo hello.

## Procedure
- tool.shell run "echo hello"
""".strip()


def test_ingest_missing_name_raises(tmp_path: Path) -> None:
    import pytest
    from openminion.modules.skill.errors import SkillError

    ctl = Skill(_cfg(tmp_path))
    try:
        with pytest.raises(SkillError) as exc_info:
            ctl.ingest_text(name="   ", markdown="## Summary\nHello.")
        assert exc_info.value.code == "INVALID_ARGUMENT"
    finally:
        ctl.close()


def test_ingest_dangerous_command_warns(tmp_path: Path) -> None:
    ctl = Skill(_cfg(tmp_path))
    try:
        _, _, warnings = ctl.ingest_text(
            name="Dangerous Wipe Skill",
            markdown=DANGEROUS_SKILL,
        )
        codes = " ".join(warnings)
        assert "command.dangerous_detected" in codes
    finally:
        ctl.close()


def test_render_snippet_invalid_purpose_raises(tmp_path: Path) -> None:
    import pytest
    from openminion.modules.skill.errors import SkillError

    ctl = Skill(_cfg(tmp_path))
    try:
        skill_id, version_hash, _ = ctl.ingest_text(
            name="Safe Recipe Skill", markdown=SAFE_RECIPE_SKILL
        )
        with pytest.raises(SkillError) as exc_info:
            ctl.render_snippet(
                skill_id=skill_id,
                version_hash=version_hash,
                purpose="chat",
                max_tokens=100,
            )
        assert exc_info.value.code == "INVALID_ARGUMENT"
    finally:
        ctl.close()


def test_log_run_invalid_outcome_raises(tmp_path: Path) -> None:
    import pytest
    from openminion.modules.skill.errors import SkillError

    ctl = Skill(_cfg(tmp_path))
    try:
        skill_id, version_hash, _ = ctl.ingest_text(
            name="Safe Recipe Skill", markdown=SAFE_RECIPE_SKILL
        )
        with pytest.raises(SkillError) as exc_info:
            ctl.log_run(
                session_id="sess-1",
                agent_id="agent.ops",
                skill_id=skill_id,
                version_hash=version_hash,
                used_for="act",
                outcome="maybe",
            )
        assert exc_info.value.code == "INVALID_ARGUMENT"
    finally:
        ctl.close()


# SMLF: Skill Markdown Learn Flow E2E tests


def test_path_validation_rejects_traversal(tmp_path: Path) -> None:
    import pytest
    from openminion.modules.skill.errors import SkillError

    ctl = Skill(_cfg(tmp_path))
    try:
        with pytest.raises(SkillError) as exc_info:
            ctl.ingest_file(path="../etc/passwd")
        assert exc_info.value.code == "PATH_TRAVERSAL"
    finally:
        ctl.close()


def test_path_validation_rejects_nonexistent_file(tmp_path: Path) -> None:
    import pytest
    from openminion.modules.skill.errors import SkillError

    ctl = Skill(_cfg(tmp_path))
    try:
        with pytest.raises(SkillError) as exc_info:
            ctl.ingest_file(path="/nonexistent/path/SKILL.md")
        assert exc_info.value.code == "PATH_NOT_FOUND"
    finally:
        ctl.close()


def test_path_validation_rejects_non_md_file(tmp_path: Path) -> None:
    import pytest
    from openminion.modules.skill.errors import SkillError

    test_file = tmp_path / "test.txt"
    test_file.write_text("not a skill")

    ctl = Skill(_cfg(tmp_path))
    try:
        with pytest.raises(SkillError) as exc_info:
            ctl.ingest_file(path=str(test_file))
        assert exc_info.value.code == "INVALID_FILE_TYPE"
    finally:
        ctl.close()


def test_path_validation_respects_allowed_roots(tmp_path: Path) -> None:
    import pytest
    from openminion.modules.skill.errors import SkillError

    allowed_dir = tmp_path / "allowed"
    allowed_dir.mkdir()
    skill_file = allowed_dir / "SKILL.md"
    skill_file.write_text("## Summary\nTest skill")

    disallowed_dir = tmp_path / "disallowed"
    disallowed_dir.mkdir()

    cfg = _cfg(tmp_path)
    cfg["skill"]["allowed_roots"] = [str(allowed_dir)]
    ctl = Skill(cfg)
    try:
        with pytest.raises(SkillError) as exc_info:
            ctl.ingest_file(path=str(disallowed_dir / "SKILL.md"))
        assert exc_info.value.code == "PATH_NOT_ALLOWED"
    finally:
        ctl.close()


def test_ingest_file_with_valid_path_succeeds(tmp_path: Path) -> None:
    skill_file = tmp_path / "test_skill" / "SKILL.md"
    skill_file.parent.mkdir(parents=True, exist_ok=True)
    skill_file.write_text("""---
name: Test Skill
id: test_skill
status: draft
---

## Summary
A test skill.
""")

    ctl = Skill(_cfg(tmp_path))
    try:
        skill_id, version_hash, warnings = ctl.ingest_file(path=str(skill_file))
        assert skill_id == "test_skill"
        assert len(version_hash) == 64
    finally:
        ctl.close()


def test_event_callback_receives_ingest_events(tmp_path: Path) -> None:

    events_received = []

    def event_callback(event_type: str, data: dict) -> None:
        events_received.append((event_type, data))

    skill_file = tmp_path / "test_skill" / "SKILL.md"
    skill_file.parent.mkdir(parents=True, exist_ok=True)
    skill_file.write_text("""---
name: Event Test Skill
id: event_test_skill
status: draft
tools: [tool.shell]
risk: low
---

## Summary
A test skill.
""")

    cfg = _cfg(tmp_path)
    ctl = Skill(cfg, event_callback=event_callback)
    try:
        skill_id, version_hash, _ = ctl.ingest_file(path=str(skill_file))
        assert len(events_received) >= 1
        assert events_received[0][0] == "skill.ingested"
        assert events_received[0][1]["skill_id"] == skill_id
        assert events_received[0][1]["version_hash"] == version_hash
    finally:
        ctl.close()


def test_event_callback_receives_failed_events(tmp_path: Path) -> None:
    import pytest

    events_received = []

    def event_callback(event_type: str, data: dict) -> None:
        events_received.append((event_type, data))

    cfg = _cfg(tmp_path)
    cfg["skill"]["allowed_roots"] = ["/nonexistent"]
    ctl = Skill(cfg, event_callback=event_callback)
    try:
        with pytest.raises(Exception):
            ctl.ingest_file(path="/some/path/SKILL.md")
        assert len(events_received) >= 1
        assert events_received[0][0] == "skill.ingest_failed"
    finally:
        ctl.close()
