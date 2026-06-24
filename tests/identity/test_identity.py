from __future__ import annotations

import time
from pathlib import Path

import pytest

import openminion.modules.identity.runtime.renderer as renderer_module
from openminion.modules.context.builder import BuildOptions, ContextPackBuilder
from openminion.modules.identity.models import AgentProfile, SkillPostureSpec
from openminion.modules.identity.runtime.lockfile import read_identity_lockfile
from openminion.modules.identity.runtime.renderer import normalize_purpose
from openminion.modules.identity.runtime.service import IdentityCtl
from openminion.modules.identity.storage import (
    InMemoryIdentityStore,
    SQLiteIdentityStore,
)
from openminion.modules.session.storage.sqlite_store import SQLiteSessionStore


class _SkillClient:
    contract_version = "v1"

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def render_snippet(
        self,
        skill_id: str,
        version_hash: str | None,
        purpose: str,
        max_tokens: int,
        mode_name: str | None = None,
    ) -> tuple[str, str]:
        del version_hash, mode_name
        self.calls.append(
            {
                "skill_id": skill_id,
                "purpose": purpose,
                "max_tokens": max_tokens,
            }
        )
        return (
            f"Skill snippet for {skill_id} ({purpose})",
            f"{skill_id}-hash-abcdef123456",
        )


class _PrioritySkillClient(_SkillClient):
    def render_snippet(
        self,
        skill_id: str,
        version_hash: str | None,
        purpose: str,
        max_tokens: int,
        mode_name: str | None = None,
    ) -> tuple[str, str]:
        text, version = super().render_snippet(
            skill_id,
            version_hash,
            purpose,
            max_tokens,
            mode_name,
        )
        if skill_id == "git-workflow":
            return (f"{text} " + ("overflow " * 80), version)
        return (text, version)


def _profile(agent_id: str = "router-agent") -> AgentProfile:
    return AgentProfile.model_validate(
        {
            "agent_id": agent_id,
            "display_name": "Router Agent",
            "profile_revision": 3,
            "role": {
                "mission": "Help the user complete tasks safely by planning and executing tool-driven steps.",
                "responsibilities": [
                    "Plan multi-step work and track progress.",
                    "Use tools only when needed.",
                    "Keep outputs auditable.",
                ],
                "hard_constraints": [
                    "MUST NOT run destructive commands without explicit user confirmation.",
                    "MUST keep outputs concise and auditable.",
                    "MUST ask for clarification when target path is ambiguous.",
                    "MUST report assumptions explicitly.",
                ],
                "domain": ["infra", "agents"],
                "escalation_rules": [
                    "If target is ambiguous, ask a clarifying question or stop.",
                ],
            },
            "personality": {
                "tone": "direct, calm, technical",
                "verbosity": "normal",
                "formatting": ["bullets for steps", "code blocks for commands"],
                "interaction_style": [
                    "avoid unnecessary questions",
                    "be explicit about assumptions",
                ],
            },
            "risk": {
                "risk_level": "medium",
                "confirm_before": ["destructive_fs", "network_changes"],
                "auto_proceed_rules": ["If unclear file path, do not proceed."],
            },
            "tool_posture": {
                "tool_use": "restricted",
                "sandbox_root": "~/workspace",
                "allowed_tools": ["tool.shell", "tool.fs", "artifactctl.ingest"],
                "blocked_patterns": ["wipe_disk", "format_fs", "rm_rf_root"],
            },
            "llm_policy_ref": "llm_policy:router-agent",
            "allowed_capabilities": [
                "summarize.session",
                "validate.factcheck",
                "os.shell.safe",
            ],
        }
    )


def test_load_profiles_from_directory_and_store_sqlite(tmp_path: Path) -> None:
    db_path = tmp_path / "identity.db"
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir(parents=True)

    (profiles_dir / "base.yaml").write_text(
        """
agent_id: base-agent
display_name: Base Agent
profile_revision: 1
role:
  mission: "Base mission"
  responsibilities:
    - "Own planning"
    - "Track progress"
    - "Protect safety"
  hard_constraints:
    - "MUST NOT perform destructive changes without confirmation."
    - "MUST stay concise."
    - "MUST expose assumptions."
  domain: ["infra"]
  escalation_rules:
    - "Ask if target is ambiguous."
personality:
  tone: "direct"
  verbosity: normal
  formatting: ["bullets"]
  interaction_style: ["be explicit"]
risk:
  risk_level: medium
  confirm_before: ["destructive_fs"]
  auto_proceed_rules: ["Do not proceed on ambiguous path."]
tool_posture:
  tool_use: restricted
  sandbox_root: "~/workspace"
  blocked_patterns: ["rm_rf_root"]
  allowed_tools: ["tool.shell"]
        """.strip()
    )

    (profiles_dir / "child.yaml").write_text(
        """
agent_id: child-agent
display_name: Child Agent
profile_revision: 2
inherits: base-agent
role:
  mission: "Child mission"
  hard_constraints:
    - "MUST NOT run irreversible ops without confirmation."
    - "MUST stay concise."
    - "MUST keep outputs auditable."
risk:
  confirm_before: ["destructive_fs", "network_changes"]
        """.strip()
    )

    store = SQLiteIdentityStore(db_path)
    identity = IdentityCtl(store=store)
    loaded = identity.load_profiles_from_path(profiles_dir)

    assert sorted(loaded) == ["base-agent", "child-agent"]

    child = identity.get_profile("child-agent")
    assert child is not None
    base = identity.get_profile("base-agent")
    assert base is not None
    assert base.profile_revision == 1
    assert child.profile_revision == 2
    assert dict(base.meta or {}).get("source") == "yaml"
    assert dict(child.meta or {}).get("source") == "yaml"
    assert child.role.mission == "Child mission"
    assert child.tool_posture.sandbox_root == "~/workspace"
    assert child.risk.confirm_before == ["destructive_fs", "network_changes"]

    summaries = identity.list_profiles()
    assert {item.agent_id for item in summaries} == {"base-agent", "child-agent"}

    identity.close()


def test_load_profiles_from_path_stamps_yaml_source_provenance(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "identity.db"
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir(parents=True)
    (profiles_dir / "router.yaml").write_text(
        """
agent_id: router
display_name: Router
profile_revision: 1
role:
  mission: "Route safely"
personality:
  tone: "direct"
risk:
  risk_level: medium
tool_posture:
  tool_use: restricted
        """.strip(),
        encoding="utf-8",
    )

    identity = IdentityCtl(store=SQLiteIdentityStore(db_path))
    loaded = identity.load_profiles_from_path(profiles_dir)
    assert loaded == ["router"]

    profile = identity.get_profile("router")
    assert profile is not None
    meta = dict(profile.meta or {})
    assert meta.get("source") == "yaml"
    identity.close()


def test_load_profiles_from_single_yaml_file_input(tmp_path: Path) -> None:
    profile_path = tmp_path / "single.yaml"
    profile_path.write_text(
        """
agent_id: single-agent
display_name: Single Agent
profile_revision: 3
role:
  mission: "Single file mission"
personality:
  tone: "precise"
risk:
  risk_level: medium
tool_posture:
  tool_use: restricted
        """.strip(),
        encoding="utf-8",
    )

    identity = IdentityCtl(store=InMemoryIdentityStore())
    loaded = identity.load_profiles_from_path(profile_path)

    assert loaded == ["single-agent"]
    profile = identity.get_profile("single-agent")
    assert profile is not None
    assert profile.display_name == "Single Agent"
    assert profile.profile_revision == 3
    assert dict(profile.meta or {}).get("source") == "yaml"
    assert profile.role.mission == "Single file mission"
    identity.close()


def test_load_profiles_from_profile_yaml_generates_local_markdown_sidecars(
    tmp_path: Path,
) -> None:
    agent_root = tmp_path / "router-agent"
    agent_root.mkdir(parents=True)
    profile_path = agent_root / "profile.yaml"
    profile_path.write_text(
        """
agent_id: router-agent
display_name: Router Agent
profile_revision: 2
role:
  mission: "Generated sidecar mission"
personality:
  tone: "direct"
risk:
  risk_level: medium
tool_posture:
  tool_use: restricted
        """.strip(),
        encoding="utf-8",
    )

    identity = IdentityCtl(store=InMemoryIdentityStore())
    loaded = identity.load_profiles_from_path(profile_path)

    assert loaded == ["router-agent"]
    assert (agent_root / "AGENT.md").is_file()
    assert (agent_root / "SOUL.md").is_file()
    assert (agent_root / "README.md").is_file()
    assert "Generated sidecar mission" in (agent_root / "AGENT.md").read_text(
        encoding="utf-8"
    )
    readme = (agent_root / "README.md").read_text(encoding="utf-8")
    assert "profile.yaml" in readme
    assert "does not change the runtime profile" in readme

    lockfile = read_identity_lockfile(agent_root / ".identity-lock.json")
    assert [item.relative_path for item in lockfile.files] == [
        "AGENT.md",
        "README.md",
        "SOUL.md",
    ]
    identity.close()


def test_load_profiles_from_single_yaml_file_restamps_existing_profile_to_yaml_source(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "identity.db"
    profile_path = tmp_path / "router.yaml"
    profile_path.write_text(
        """
agent_id: router-agent
display_name: Router Agent
profile_revision: 4
role:
  mission: "YAML source of truth mission"
personality:
  tone: "focused"
risk:
  risk_level: medium
tool_posture:
  tool_use: restricted
        """.strip(),
        encoding="utf-8",
    )

    identity = IdentityCtl(store=SQLiteIdentityStore(db_path))
    seeded = _profile(agent_id="router-agent")
    seeded.meta = {
        "source": "bundle",
        "bundle_imported": True,
        "bundle_fingerprint": "stale-fingerprint",
    }
    identity.upsert_profile(seeded)

    loaded = identity.load_profiles_from_path(profile_path)
    assert loaded == ["router-agent"]
    profile = identity.get_profile("router-agent")
    assert profile is not None
    assert profile.role.mission == "YAML source of truth mission"
    assert dict(profile.meta or {}).get("source") == "yaml"
    identity.close()


def test_load_profiles_from_path_skip_unchanged_avoids_updated_at_churn(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "identity.db"
    agent_root = tmp_path / "router-agent"
    agent_root.mkdir(parents=True)
    profile_path = agent_root / "profile.yaml"
    profile_path.write_text(
        """
agent_id: router-agent
display_name: Router Agent
profile_revision: 4
role:
  mission: "Stable YAML mission"
personality:
  tone: "focused"
risk:
  risk_level: medium
tool_posture:
  tool_use: restricted
        """.strip(),
        encoding="utf-8",
    )

    store = SQLiteIdentityStore(db_path)
    identity = IdentityCtl(store=store)
    loaded_first = identity.load_profiles_from_path(profile_path, skip_unchanged=True)
    assert loaded_first == ["router-agent"]

    first_row = store.get_profile("router-agent")
    assert first_row is not None
    if first_row is None:  # pragma: no cover
        raise AssertionError("expected profile after first load")

    (agent_root / "AGENT.md").write_text("drifted agent doc\n", encoding="utf-8")
    (agent_root / "README.md").unlink()

    time.sleep(0.01)
    loaded_second = identity.load_profiles_from_path(profile_path, skip_unchanged=True)
    assert loaded_second == []

    second_row = store.get_profile("router-agent")
    assert second_row is not None
    if second_row is None:  # pragma: no cover
        raise AssertionError("expected profile after second load")

    assert second_row.updated_at == first_row.updated_at
    assert second_row.profile_version == first_row.profile_version
    assert "Stable YAML mission" in (agent_root / "AGENT.md").read_text(
        encoding="utf-8"
    )
    assert (agent_root / "README.md").is_file()
    identity.close()


def test_load_profiles_from_multi_profile_yaml_file_with_inheritance(
    tmp_path: Path,
) -> None:
    profile_path = tmp_path / "multi.yaml"
    profile_path.write_text(
        """
profiles:
  base-agent:
    display_name: Base Agent
    profile_revision: 1
    role:
      mission: "Base mission"
      responsibilities:
        - "Plan work"
      hard_constraints:
        - "Do not fabricate"
    personality:
      tone: "direct"
    risk:
      risk_level: medium
    tool_posture:
      tool_use: restricted
      sandbox_root: "~/workspace"
  child-agent:
    display_name: Child Agent
    profile_revision: 2
    inherits: base-agent
    role:
      mission: "Child mission"
        """.strip(),
        encoding="utf-8",
    )

    identity = IdentityCtl(store=InMemoryIdentityStore())
    loaded = identity.load_profiles_from_path(profile_path)

    assert loaded == ["base-agent", "child-agent"]
    child = identity.get_profile("child-agent")
    assert child is not None
    assert child.profile_revision == 2
    assert dict(child.meta or {}).get("source") == "yaml"
    assert child.role.mission == "Child mission"
    assert child.role.responsibilities == ["Plan work"]
    assert child.tool_posture.sandbox_root == "~/workspace"
    identity.close()


@pytest.mark.parametrize(
    ("profile_yaml", "error_fragments"),
    [
        (
            """
agent_id: mission-missing
display_name: Mission Missing
profile_revision: 1
role:
  responsibilities: ["Own planning"]
personality:
  tone: "direct"
risk:
  risk_level: medium
tool_posture:
  tool_use: restricted
            """.strip(),
            ("role", "mission"),
        ),
        (
            """
agent_id: tone-missing
display_name: Tone Missing
profile_revision: 1
role:
  mission: "Help users safely."
personality:
  verbosity: normal
risk:
  risk_level: medium
tool_posture:
  tool_use: restricted
            """.strip(),
            ("personality", "tone"),
        ),
    ],
)
def test_load_profiles_requires_mission_and_tone_defaults(
    tmp_path: Path,
    profile_yaml: str,
    error_fragments: tuple[str, str],
) -> None:
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir(parents=True)
    (profiles_dir / "invalid.yaml").write_text(profile_yaml, encoding="utf-8")

    identity = IdentityCtl(store=InMemoryIdentityStore())
    with pytest.raises(ValueError) as exc:
        identity.load_profiles_from_path(profiles_dir)

    message = str(exc.value).lower()
    assert error_fragments[0] in message
    assert error_fragments[1] in message
    identity.close()


def test_render_under_budget_and_deterministic() -> None:
    store = InMemoryIdentityStore()
    identity = IdentityCtl(store=store)
    profile = _profile()
    version = identity.upsert_profile(profile)
    assert version

    for purpose in ["decide", "plan", "act", "reflect"]:
        snippet = identity.render(profile.agent_id, purpose=purpose, max_tokens=220)
        assert snippet.budget.used_tokens <= snippet.budget.max_tokens
        assert snippet.text.strip()
        assert snippet.profile_version == version

        second = identity.render(profile.agent_id, purpose=purpose, max_tokens=220)
        assert second.text == snippet.text
        assert second.profile_version == snippet.profile_version
        assert second.render_version == snippet.render_version

    assert len(store._cache) == 4  # type: ignore[attr-defined]
    identity.close()


def test_skill_posture_changes_profile_version_and_validates() -> None:
    store = InMemoryIdentityStore()
    identity = IdentityCtl(store=store)
    profile = _profile(agent_id="skill-posture-agent")
    first_version = identity.upsert_profile(profile)

    profile_with_skills = profile.model_copy(
        update={
            "skill_posture": SkillPostureSpec(
                always_active=["python-debugging"],
                query_activated=["git-workflow"],
                excluded=["legacy-bash"],
                max_skill_tokens=180,
            )
        }
    )
    second_version = identity.upsert_profile(profile_with_skills)
    resolved = identity.get_profile("skill-posture-agent")

    assert resolved is not None
    assert second_version != first_version
    assert resolved.skill_posture is not None
    assert resolved.skill_posture.always_active == ["python-debugging"]
    assert resolved.skill_posture.query_activated == ["git-workflow"]
    assert resolved.skill_posture.excluded == ["legacy-bash"]
    assert resolved.skill_posture.max_skill_tokens == 180
    identity.close()


def test_render_includes_skill_posture_snippets_with_provenance() -> None:
    skillctl = _SkillClient()
    identity = IdentityCtl(store=InMemoryIdentityStore(), skillctl=skillctl)
    profile = _profile(agent_id="skill-render-agent").model_copy(
        update={
            "skill_posture": SkillPostureSpec(
                always_active=["python-debugging"],
                max_skill_tokens=120,
            )
        }
    )
    identity.upsert_profile(profile)

    snippet = identity.render(
        "skill-render-agent",
        purpose="act",
        max_tokens=220,
    )

    assert "python-debugging" in snippet.text
    assert "Skill snippet for python-debugging" in snippet.text
    assert "skills" in (snippet.sections or {})
    assert "skill:python-debugging@" in " ".join(snippet.included_fields)
    assert skillctl.calls == [
        {
            "skill_id": "python-debugging",
            "purpose": "act",
            "max_tokens": 120,
        }
    ]
    identity.close()


def test_render_query_activated_skill_uses_query_text() -> None:
    skillctl = _SkillClient()
    identity = IdentityCtl(store=InMemoryIdentityStore(), skillctl=skillctl)
    profile = _profile(agent_id="query-activated-agent").model_copy(
        update={
            "skill_posture": SkillPostureSpec(
                query_activated=["git-workflow", "sql-query"],
                max_skill_tokens=160,
            )
        }
    )
    identity.upsert_profile(profile)

    unmatched = identity.render(
        "query-activated-agent",
        purpose="act",
        max_tokens=220,
        query_text="help me review this pull request",
    )
    matched = identity.render(
        "query-activated-agent",
        purpose="act",
        max_tokens=220,
        query_text="help me with my git branch workflow",
    )

    assert "Skill snippet for git-workflow" not in unmatched.text
    assert "Skill snippet for git-workflow" in matched.text
    assert "Skill snippet for sql-query" not in matched.text
    identity.close()


def test_render_skill_posture_order_is_declared_and_query_last() -> None:
    skillctl = _SkillClient()
    identity = IdentityCtl(store=InMemoryIdentityStore(), skillctl=skillctl)
    profile = _profile(agent_id="skill-order-agent").model_copy(
        update={
            "skill_posture": SkillPostureSpec(
                always_active=["python-debugging", "code-review"],
                query_activated=["git-workflow"],
                max_skill_tokens=180,
            )
        }
    )
    identity.upsert_profile(profile)

    snippet = identity.render(
        "skill-order-agent",
        purpose="act",
        max_tokens=260,
        query_text="help me with my git workflow",
    )

    python_idx = snippet.text.index("python-debugging")
    review_idx = snippet.text.index("code-review")
    git_idx = snippet.text.index("git-workflow")
    assert python_idx < review_idx < git_idx
    identity.close()


def test_render_skill_posture_prefers_always_active_over_query_under_budget() -> None:
    skillctl = _PrioritySkillClient()
    identity = IdentityCtl(store=InMemoryIdentityStore(), skillctl=skillctl)
    profile = AgentProfile.model_validate(
        {
            "agent_id": "skill-budget-agent",
            "display_name": "Skill Budget Agent",
            "profile_revision": 1,
            "role": {"mission": "Debug safely."},
            "personality": {"tone": "direct"},
            "risk": {"risk_level": "medium"},
            "tool_posture": {"tool_use": "restricted"},
            "skill_posture": {
                "always_active": ["python-debugging"],
                "query_activated": ["git-workflow"],
                "max_skill_tokens": 220,
            },
        }
    )
    identity.upsert_profile(profile)

    snippet = identity.render(
        "skill-budget-agent",
        purpose="act",
        max_tokens=80,
        query_text="help me with my git workflow",
    )

    assert "python-debugging" in snippet.text
    assert any(
        field.startswith("skill:git-workflow") for field in snippet.omitted_fields
    )
    identity.close()


def test_render_skill_posture_excluded_wins_and_is_recorded() -> None:
    skillctl = _SkillClient()
    identity = IdentityCtl(store=InMemoryIdentityStore(), skillctl=skillctl)
    profile = _profile(agent_id="excluded-skill-agent").model_copy(
        update={
            "skill_posture": SkillPostureSpec(
                always_active=["bash-scripts"],
                excluded=["bash-scripts"],
            )
        }
    )
    identity.upsert_profile(profile)

    snippet = identity.render(
        "excluded-skill-agent",
        purpose="act",
        max_tokens=220,
    )

    assert "Skill snippet for bash-scripts" not in snippet.text
    assert "skill:bash-scripts" in snippet.omitted_fields
    assert skillctl.calls == []
    identity.close()


def test_context_builder_identity_render_includes_skill_posture_snippet(
    tmp_path: Path,
) -> None:
    skillctl = _SkillClient()
    identity = IdentityCtl(store=InMemoryIdentityStore(), skillctl=skillctl)
    profile = _profile(agent_id="ctx-skill-agent").model_copy(
        update={
            "skill_posture": SkillPostureSpec(
                always_active=["python-debugging"],
                query_activated=["git-workflow"],
                max_skill_tokens=120,
            )
        }
    )
    identity.upsert_profile(profile)

    sess_db = tmp_path / "ctx-skill-agent.db"
    session_store = SQLiteSessionStore(sess_db)
    session_id = session_store.create_session(title="ctx skill posture")
    session_store.append_turn(
        session_id,
        role="user",
        content="help me debug this python traceback",
    )

    builder = ContextPackBuilder(
        sess_db,
        identity_client=ContextPackBuilder.identity_client_from_service(identity),
        log_identity_events=False,
    )
    pack = builder.build(
        BuildOptions(
            session_id=session_id,
            agent_id="ctx-skill-agent",
            purpose="act",
            user_input="help me debug this python traceback",
        )
    )

    first_message = pack["messages"][0]["content"]
    assert "Skill snippet for python-debugging" in first_message
    assert "git-workflow" not in first_message
    identity.close()
    session_store.close()


def test_act_render_uses_stored_constraint_order_not_keyword_priority() -> None:
    store = InMemoryIdentityStore()
    identity = IdentityCtl(store=store)
    profile = _profile(agent_id="constraint-order-agent")
    profile.role.hard_constraints = [
        "First stored constraint: keep answers concise.",
        "Second stored constraint: show assumptions.",
        "Third stored constraint: cite unknowns.",
        "Fourth stored constraint: keep logs auditable.",
        "Fifth stored constraint: preserve user wording.",
        "MUST NOT promote this later keyword item ahead of stored order.",
    ]
    identity.upsert_profile(profile)

    snippet = identity.render(profile.agent_id, purpose="act", max_tokens=220)

    assert "First stored constraint" in snippet.text
    assert "Fifth stored constraint" in snippet.text
    assert "MUST NOT promote this later keyword item" not in snippet.text

    identity.close()


def test_identity_renderer_no_longer_classifies_constraint_text_by_keyword() -> None:
    source = Path(renderer_module.__file__).read_text(encoding="utf-8")

    assert "_is_safety_constraint" not in source
    assert "_is_must_not" not in source
    assert "constraint_must_not" not in source
    assert "constraint_must" not in source


@pytest.mark.parametrize(
    ("raw_purpose", "expected"),
    [
        ("decide", "decide"),
        ("decision", "decide"),
        ("plan", "plan"),
        ("planning", "plan"),
        ("reflect", "reflect"),
        ("reflection", "reflect"),
        ("summarize", "summarize"),
        ("summary", "summarize"),
        ("summarization", "summarize"),
        ("judge", "judge"),
        ("validate", "judge"),
        ("verify", "judge"),
        ("validation", "judge"),
        ("chat", "act"),
        ("respond_followup", "act"),
        ("follow_up", "act"),
        ("follow-up", "act"),
        ("followup", "act"),
        ("reply", "act"),
        ("response", "act"),
        ("unknown-phase", "act"),
        ("", "act"),
    ],
)
def test_normalize_purpose_uses_renderer_owned_alias_inventory(
    raw_purpose: str,
    expected: str,
) -> None:
    assert normalize_purpose(raw_purpose) == expected


def test_upsert_profile_does_not_auto_increment_profile_revision() -> None:
    store = InMemoryIdentityStore()
    identity = IdentityCtl(store=store)

    original_payload = _profile(agent_id="revision-agent").model_dump(mode="python")
    original_payload["profile_revision"] = 1
    original = AgentProfile.model_validate(original_payload)

    first_version = identity.upsert_profile(original)
    first = identity.get_profile("revision-agent")
    assert first is not None
    assert first.profile_revision == 1

    updated_payload = original.model_dump(mode="python")
    updated_payload["role"]["mission"] = "Updated mission text for characterization."
    updated_payload["profile_revision"] = 1
    updated = AgentProfile.model_validate(updated_payload)

    second_version = identity.upsert_profile(updated)
    second = identity.get_profile("revision-agent")
    assert second is not None
    assert second.profile_revision == 1
    assert second_version != first_version

    identity.close()


def test_context_identity_client_preserves_sections() -> None:
    store = InMemoryIdentityStore()
    identity = IdentityCtl(store=store)
    profile = _profile(agent_id="section-agent")
    identity.upsert_profile(profile)

    identity_client = ContextPackBuilder.identity_client_from_service(identity)
    snippet = identity_client.render(
        agent_id="section-agent",
        purpose="plan",
        max_tokens=220,
    )
    assert snippet.sections is not None
    assert "mission" in snippet.sections
    assert "constraints" in snippet.sections

    identity.close()


def test_ctxctl_integration_logs_identity_versions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENMINION_STRICT_CONTEXT_CONTRACTS", "0")
    sess_db = tmp_path / "sessctl.db"
    sess = SQLiteSessionStore(sess_db)
    session_id = sess.create_session(title="identity-integration")
    sess.append_turn(session_id, role="user", content="hello")

    identity_store = SQLiteIdentityStore(tmp_path / "identity.db")
    identity = IdentityCtl(store=identity_store)
    profile = _profile(agent_id="router-agent")
    profile_version = identity.upsert_profile(profile)

    builder = ContextPackBuilder(
        sess_db,
        identity_client=ContextPackBuilder.identity_client_from_service(identity),
        log_identity_events=True,
    )
    pack = builder.build(
        BuildOptions(
            session_id=session_id,
            agent_id="router-agent",
            purpose="act",
            user_input="continue",
        )
    )

    assert pack["profile_version"] == profile_version
    assert "[IDENTITY]" in pack["messages"][0]["content"]

    events = sess.list_events(session_id, limit=50)
    event_types = [item["type"] for item in events]
    assert "agent.bound" in event_types

    bound = [item for item in events if item["type"] == "agent.bound"][-1]

    assert bound["payload"]["profile_version"] == profile_version
    if "llm.request.started" in event_types:
        started = [item for item in events if item["type"] == "llm.request.started"][-1]
        assert started["payload"]["profile_version"] == profile_version
        assert started["payload"]["render_version"] == pack["render_version"]

    identity.close()
    sess.close()


def test_render_validation_catches_budget_violation() -> None:
    identity = IdentityCtl(store=InMemoryIdentityStore())
    profile = _profile("validator")
    identity.upsert_profile(profile)
    snippet = identity.render("validator", purpose="decide", max_tokens=80)

    payload = snippet.model_dump(mode="python")
    payload["budget"]["used_tokens"] = payload["budget"]["max_tokens"] + 1
    result = identity.validate_render(payload)

    assert result.ok is False
    assert any("used_tokens" in error for error in result.errors)
