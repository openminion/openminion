"""Repository bindings constrain runtime skill reads without changing admission."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

import pytest

from openminion.modules.brain.adapters.context.runtime import ContextCtlAdapter
from openminion.modules.brain.adapters.tool.runtime import (
    ToolAdapter,
    current_repository_root,
)
from openminion.modules.brain.bootstrap.skill.pipeline import (
    _effective_catalog,
    _load_catalog,
)
from openminion.modules.brain.execution.worktree_children import (
    bind_runner_tool_workspace,
)
from openminion.modules.brain.loop.recursive.retrieval import _retrieve_skills
from openminion.modules.context.retrieval.materials import (
    RetrievedContextMaterialsCollector,
)
from openminion.modules.context.schemas import BuildConstraints, SkillSnippetRef
from openminion.modules.context.service import ContextCtlService
from openminion.modules.identity.models import AgentProfile
from openminion.modules.identity.runtime.service import IdentityCtl
from openminion.modules.identity.storage.memory import InMemoryIdentityStore
from openminion.modules.skill import Skill
from openminion.modules.skill.interfaces import (
    SkillIngestAuthority,
    SkillVerificationEvidence,
)
from openminion.services.brain.post_execution.mixin import _bind_tool_workspace_root
from openminion.tools.skill.plugin import _h_skill_get, _h_skill_ingest, _h_skill_list
from tests.context.test_decide_skill_snippet_packing import (
    _DecideArtifactClient,
    _DecideIdentityClient,
    _DecideMemoryClient,
    _DecideSessionClient,
)


@pytest.fixture
def catalog(tmp_path):
    roots = [tmp_path / name for name in ("a", "b")]
    for root in roots:
        root.mkdir()
        (root / ".git").mkdir()
    skill = Skill(
        {
            "skill": {
                "sqlite_path": str(tmp_path / ".openminion" / "skill.db"),
                "blob_root": str(tmp_path / ".openminion" / "blobs"),
                "fallback_root": str(tmp_path / ".openminion" / "fallback"),
                "wal": False,
            }
        }
    )
    authority = SkillIngestAuthority.local_operator(surface="test", principal_id="test")
    versions = {}
    for name, scope, agent_id in (
        ("local", "project", None),
        ("global", "global", None),
        ("agent", "agent", "agent-a"),
    ):
        bundle = roots[0] / name
        bundle.mkdir()
        (bundle / "SKILL.md").write_text(
            f"---\nname: {name}\nid: {name}\n---\n"
            f"## Summary\n{name} reference guide.\n\n## Procedure\nRead reference.txt.\n"
        )
        (bundle / "reference.txt").write_text(f"{name} retained fact")
        skill_id, version, _ = skill.ingest_file(
            bundle / "SKILL.md",
            scope=scope,
            agent_id=agent_id,
            repository_root=str(roots[0]) if scope == "project" else None,
            authority=authority,
        )
        skill.admit_skill_version(
            skill_id=skill_id,
            version_hash=version,
            expected_active_version_hash=None,
            target_status="verified",
            reason="fixture review",
            authority=authority,
            verification_evidence=SkillVerificationEvidence(
                check="fixture bytes",
                result="passed",
                evidence_ref=(bundle / "SKILL.md").as_uri(),
            ),
        )
        versions[name] = version
    try:
        yield skill, roots, versions
    finally:
        skill.close()


@pytest.mark.parametrize("root_index", [0, 1, None])
def test_catalog_and_stale_refs_obey_current_binding(catalog, root_index):
    skill, roots, _ = catalog
    root = str(roots[root_index]) if root_index is not None else None
    adapter = ToolAdapter(workspace_root=roots[0], artifactctl=SimpleNamespace())
    try:
        with adapter.workspace_override(roots[0], repository_root=root):
            rows = _load_catalog(skill_api=skill, agent_id="agent-a")
        expected = (
            {"global", "agent", "local"} if root_index == 0 else {"global", "agent"}
        )
        assert {row["id"] for row in rows} == expected
        for configured in (None, ["local"]):
            effective, _, _ = _effective_catalog(
                profile=SimpleNamespace(skill=configured, skill_catalog=[]),
                state=SimpleNamespace(
                    session_skill_loaded=["local"],
                    session_skill_unloaded=[],
                    skill_selection_mode="",
                ),
                catalog=rows,
            )
            assert ("local" in {row["id"] for row in effective}) == (root_index == 0)
    finally:
        adapter.close()


@pytest.mark.parametrize("root_index", [0, 1, None])
def test_direct_and_materialized_reads_share_scope(catalog, root_index):
    skill, roots, versions = catalog
    root = str(roots[root_index]) if root_index is not None else None
    ctx = SimpleNamespace(skill_api=skill, agent_id="agent-a", repository_root=root)
    listing = _h_skill_list({"repository_root": str(roots[0])}, ctx)
    assert listing["ok"]
    serialized = json.dumps(listing)
    assert "repository_root" not in serialized
    assert all(str(repository) not in serialized for repository in roots)
    assert ("local" in {row["skill_id"] for row in listing["skills"]}) == (
        root_index == 0
    )
    for resource_path in (None, "reference.txt"):
        result = _h_skill_get(
            {
                "skill_id": "local",
                "version_hash": versions["local"],
                "resource_path": resource_path,
                "repository_root": str(roots[0]),
            },
            ctx,
        )
        assert result["ok"] == (root_index == 0)
        if root_index != 0:
            assert result["error"]["code"] == "NOT_FOUND"
    collector = RetrievedContextMaterialsCollector(SimpleNamespace(_skillctl=skill))
    for version in (None, versions["local"]):
        text, _ = collector.resolve_skill_snippet(
            constraints=BuildConstraints(
                skill_refs=[SkillSnippetRef(skill_id="local", version_hash=version)]
            ),
            purpose="act",
            mode_name=None,
            skills_tokens=500,
            agent_id="agent-a",
            repository_root=root,
        )
        assert bool(text) == (root_index == 0)
    for skill_id in ("global", "agent"):
        assert _h_skill_get({"skill_id": skill_id}, ctx)["ok"]


def test_model_ingest_cannot_claim_repository(catalog):
    skill, roots, _ = catalog
    args = {
        "name": "new",
        "markdown": "---\nname: new\n---\n## Procedure\nRead the guide.",
        "scope": "project",
        "repository_root": str(roots[0]),
    }
    denied = _h_skill_ingest(
        args, SimpleNamespace(skill_api=skill, agent_id="agent-a", repository_root=None)
    )
    assert denied["error"]["code"] == "INVALID_ARGS"
    args["scope"] = "agent"
    allowed = _h_skill_ingest(
        args,
        SimpleNamespace(
            skill_api=skill, agent_id="agent-a", repository_root=str(roots[1])
        ),
    )
    assert allowed["ok"]
    assert skill.get_skill_owner(skill_id=allowed["skill_id"]) == {
        "scope": "agent",
        "agent_id": "agent-a",
        "repository_root": None,
    }
    assert allowed["skill_id"] not in {
        row["id"]
        for row in skill.catalog_summaries(
            agent_id="agent-a", repository_root=str(roots[1])
        )
    }


def test_identity_refs_without_repository_binding_fail_closed(catalog):
    skill, _, _ = catalog
    identity = IdentityCtl(store=InMemoryIdentityStore(), skillctl=skill)
    profile = AgentProfile.model_validate(
        {
            "agent_id": "agent-a",
            "display_name": "Agent",
            "profile_revision": 1,
            "role": {"mission": "Read approved guidance."},
            "personality": {"tone": "direct"},
            "risk": {},
            "tool_posture": {},
            "skill_posture": {"always_active": ["local", "global", "agent"]},
        }
    )
    try:
        identity.upsert_profile(profile)
        for snippet in (
            identity.render("agent-a", "act", 1000),
            identity.render("agent-a", "act", 1000),
            identity.render_from_profile(profile, "act", 1000),
        ):
            assert "Skill ID: local" not in snippet.text
            assert "skill:local" in snippet.omitted_fields
            assert "Skill ID: global" in snippet.text
            assert "Skill ID: agent" in snippet.text
    finally:
        identity.close()


def test_workspace_context_is_local_and_never_inferred(tmp_path):
    roots = [tmp_path / name for name in ("a", "b")]
    for root in roots:
        root.mkdir()
        (root / ".git").mkdir()
    adapter = ToolAdapter(workspace_root=roots[0], artifactctl=SimpleNamespace())
    barrier = Barrier(2)

    def read(root):
        runtime = SimpleNamespace(
            sessions=SimpleNamespace(
                get_session=lambda _: SimpleNamespace(
                    metadata={"repository_root": str(root)}
                )
            )
        )
        with _bind_tool_workspace_root(
            adapter,
            {"workspace_root": str(roots[0])},
            (),
            runtime=runtime,
            runtime_session_id="session",
        ):
            barrier.wait()
            return current_repository_root()

    try:
        with _bind_tool_workspace_root(adapter, {"workspace_root": str(roots[0])}, ()):
            assert current_repository_root() is None
        with ThreadPoolExecutor(max_workers=2) as pool:
            assert set(pool.map(read, roots)) == {str(root) for root in roots}
        with adapter.workspace_override(roots[0], repository_root=str(roots[0])):
            with pytest.raises(RuntimeError):
                with bind_runner_tool_workspace(
                    SimpleNamespace(tool_api=adapter),
                    lease=SimpleNamespace(worktree=roots[1]),
                ):
                    assert asyncio.run(
                        asyncio.to_thread(current_repository_root)
                    ) == str(roots[1])
                    raise RuntimeError("child failed")
            assert current_repository_root() == str(roots[0])
        assert current_repository_root() is None
    finally:
        adapter.close()


def test_context_adapter_does_not_trust_repository_hint(tmp_path):
    captured = []
    service = SimpleNamespace(
        build_pack=lambda request: (
            captured.append(request) or SimpleNamespace(model_dump=lambda: {})
        )
    )
    adapter = ContextCtlAdapter(service)
    adapter.build(
        session_id="session",
        agent_id="agent",
        purpose="act",
        budget={},
        hints={"repository_root": str(tmp_path)},
    )
    assert captured[0].repository_root is None


def test_context_cache_does_not_reuse_foreign_repository_snippets(catalog):
    skill, roots, versions = catalog
    service = ContextCtlService(
        identityctl=_DecideIdentityClient(),
        sessctl=_DecideSessionClient(),
        memctl=_DecideMemoryClient(),
        artifactctl=_DecideArtifactClient(),
        skillctl=skill,
    )
    context = ContextCtlAdapter(service)
    tools = ToolAdapter(workspace_root=roots[0], artifactctl=SimpleNamespace())
    try:
        for root, visible in (
            (roots[0], True),
            (roots[1], False),
            (None, False),
            (roots[0], True),
        ):
            with tools.workspace_override(
                roots[0], repository_root=str(root) if root else None
            ):
                pack = context.build(
                    session_id="same-session",
                    agent_id="agent-a",
                    purpose="act",
                    budget={},
                    hints={
                        "query": "read the reference",
                        "skill_id": "local",
                        "skill_version_hash": versions["local"],
                    },
                )
            assert (
                any(
                    "Skill ID: local" in message["content"]
                    for message in pack["messages"]
                )
                == visible
            )
    finally:
        tools.close()
        service.close()


def test_recursive_match_and_render_receive_same_scope():
    calls = []
    skill = SimpleNamespace(
        match=lambda *args, **kwargs: (
            calls.append(kwargs) or [{"skill_id": "local", "version_hash": "v1"}]
        ),
        render_snippet=lambda *args, **kwargs: (
            calls.append(kwargs) or ("guide", "hash")
        ),
    )
    service = SimpleNamespace(
        _skillctl=skill,
        config=SimpleNamespace(
            retrieval=SimpleNamespace(k_skill=1, text_snippet_chars=100)
        ),
        _to_plain=lambda row: row,
    )
    result = _retrieve_skills(
        service,
        agent_id="agent-a",
        query="local",
        strategy="auto",
        repository_root="/approved/repo",
    )
    assert result and all(call["repository_root"] == "/approved/repo" for call in calls)
    assert calls[-1]["agent_id"] == "agent-a"
