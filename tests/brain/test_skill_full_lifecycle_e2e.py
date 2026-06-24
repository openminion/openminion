from __future__ import annotations

from pathlib import Path
from typing import Any

from openminion.modules.brain.adapters.context import ContextCtlAdapter
from openminion.modules.brain.adapters.a2a import LocalA2AAdapter
from openminion.modules.brain.adapters.memory import LocalMemoryAdapter
from openminion.modules.brain.adapters.policy import LocalPolicyAdapter
from openminion.modules.brain.adapters.session import LocalSessionStore
from openminion.modules.brain.adapters.tool import LocalToolAdapter
from openminion.modules.brain.runner import RunnerOptions, BrainRunner
from openminion.modules.brain.schemas import (
    AgentBudgets,
    AgentDefaults,
    AgentProfile,
    LLMProfiles,
)
from openminion.modules.llm.schemas import LLMRequest, LLMResponse, Message, UsageInfo
from openminion.modules.context.schemas import (
    BuildConstraints,
    BuildPackRequest,
    IdentitySnippet,
    SessionSlice,
)
from openminion.modules.context.service import ContextCtlService
from openminion.modules.skill.runtime.skill import Skill


FIXTURES_ROOT = (
    Path(__file__).resolve().parents[1] / "skill" / "fixtures" / "external_catalog"
)


def _fixture_path(provider: str, name: str, leaf: str = "SKILL.md") -> Path:
    return FIXTURES_ROOT / provider / name / leaf


_COMPLEX_FIXTURES: tuple[tuple[str, str], ...] = (
    ("anthropic", "claude-api"),
    ("anthropic", "news_digest"),
    ("anthropic", "mcp_builder"),
    ("anthropic", "web_artifacts_builder"),
    ("anthropic", "webapp-testing"),
    ("openai", "github_pr"),
    ("openai", "data_export"),
    ("openai", "figma_code_connect_components"),
    ("openai", "figma_generate_design"),
    ("openai", "playwright"),
)


def _skill_cfg(tmp_path: Path, *, known_tools: list[str]) -> dict[str, Any]:
    return {
        "skill": {
            "sqlite_path": str(tmp_path / "skill.db"),
            "wal": False,
            "default_status_filter": ["draft", "verified", "blessed"],
            "high_risk_status_filter": ["draft", "verified", "blessed"],
            "known_tools": known_tools,
        }
    }


def _ingest_fixture_bundle(
    skillctl: Skill,
    fixtures: tuple[tuple[str, str], ...],
) -> dict[str, tuple[str, str, list[str]]]:
    out: dict[str, tuple[str, str, list[str]]] = {}
    for provider, name in fixtures:
        skill_id, version_hash, warnings = skillctl.ingest_file(
            _fixture_path(provider, name, "SKILL.md")
        )
        out[name] = (skill_id, version_hash, warnings)
    return out


class _IdentityClient:
    contract_version = "v1"

    def render(
        self, *, agent_id: str, purpose: str, max_tokens: int, provider_pref=None
    ) -> IdentitySnippet:
        del purpose, max_tokens, provider_pref
        return IdentitySnippet(
            agent_id=agent_id,
            profile_version="prof:v1",
            render_version="rend:v1",
            text=f"Identity for {agent_id}",
        )


class _SessionClient:
    contract_version = "v1"

    def get_slice(self, *, session_id, purpose, limits) -> SessionSlice:
        del purpose, limits
        return SessionSlice(
            session_id=session_id,
            slice_version="slice:v1",
            last_event_id="evt-001",
            summary_short="short summary",
        )


class _MemoryClient:
    contract_version = "v1"

    def query_facts(self, *, session_id, agent_id, query, limit, mode_name=None):
        del session_id, agent_id, query, limit, mode_name
        return []

    def query_memory_cards(self, *, session_id, agent_id, query, limit, mode_name=None):
        del session_id, agent_id, query, limit, mode_name
        return []

    def recall_session_start_memory(
        self, *, session_id, agent_id, query, turn_index, limit, mode_name=None
    ):
        del session_id, agent_id, query, turn_index, limit, mode_name
        return []

    def recall_mid_session_memory(self, **kwargs):
        del kwargs
        return []

    def recall_recent_session_artifacts(self, **kwargs):
        del kwargs
        return []

    def get_procedure(self, *, procedure_id):
        del procedure_id
        return None


class _ArtifactClient:
    contract_version = "v1"

    def query_digests(self, *, session_id, agent_id, query, limit):
        del session_id, agent_id, query, limit
        return []


class _FakeLLM:
    contract_version = "v1"

    def __init__(self, *, selected_skill_id: str | None) -> None:
        self.selected_skill_id = str(selected_skill_id or "").strip()
        self.calls: list[dict[str, Any]] = []

    def estimate_tokens(self, *, model: str, context: dict[str, Any]) -> int:
        del model, context
        return 12

    def call_structured(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        schema_name = kwargs["schema"].__name__
        if schema_name == "SkillSubsetSelection":
            return {
                "skill_ids": [self.selected_skill_id] if self.selected_skill_id else [],
                "intent": "mock skill selection",
            }
        if schema_name == "Decision":
            return {
                "route": "respond",
                "confidence": 0.9,
                "reason_code": "mock_decide",
                "answer": "ok",
            }
        raise AssertionError(f"Unexpected schema: {schema_name}")

    def call(self, request: LLMRequest) -> LLMResponse:
        self.calls.append(
            {
                "kind": "entry",
                "messages": list(request.messages),
                "metadata": dict(request.metadata),
            }
        )
        return LLMResponse(
            ok=True,
            provider="test",
            model=str(request.model or "test-model"),
            output_text="ok",
            assistant_messages=[Message(role="assistant", content="ok")],
            usage=UsageInfo(input_tokens=1, output_tokens=1, total_tokens=2),
            finish_reason="stop",
            provider_raw={},
            telemetry={},
        )


def _profile() -> AgentProfile:
    return AgentProfile(
        agent_id="router-agent",
        role="general",
        llm_profiles=LLMProfiles(
            decide_model="decide-default",
            plan_model="plan-default",
            act_model=None,
            reflect_model="reflect-default",
            summarize_model="summarize-default",
        ),
        budgets=AgentBudgets(
            max_ticks_per_user_turn=5,
            max_tool_calls=2,
            max_a2a_calls=1,
            max_total_llm_tokens=1000,
            max_elapsed_ms=10_000,
        ),
        defaults=AgentDefaults(),
    )


def _message_contents(messages: list[Any]) -> list[str]:
    out: list[str] = []
    for message in messages:
        if isinstance(message, dict):
            out.append(str(message.get("content", "")))
        else:
            out.append(str(getattr(message, "content", "")))
    return out


def test_context_pack_with_real_skill_includes_snippet(tmp_path: Path) -> None:
    skillctl = Skill(_skill_cfg(tmp_path, known_tools=["http_request"]))
    try:
        skill_id, version_hash, warnings = skillctl.ingest_file(
            _fixture_path("openai", "linear", "SKILL.md")
        )
        assert not any(item.startswith("lint.error:") for item in warnings)

        service = ContextCtlService(
            identityctl=_IdentityClient(),
            sessctl=_SessionClient(),
            memctl=_MemoryClient(),
            artifactctl=_ArtifactClient(),
            skillctl=skillctl,
        )
        pack = service.build_pack(
            BuildPackRequest(
                session_id="sess-sfle-pack",
                agent_id="hello-agent",
                purpose="decide",
                query="help triage a linear issue",
                constraints=BuildConstraints(
                    skill_id=skill_id,
                    skill_version_hash=version_hash,
                ),
            )
        )

        assert any(
            "[SKILL SNIPPET]" in content for content in _message_contents(pack.messages)
        )
        assert any(
            "Skill: Linear" in content for content in _message_contents(pack.messages)
        )
        assert any(
            getattr(segment, "bucket", None) == "retrieval"
            and "[SKILL SNIPPET]" in str(getattr(segment, "content", ""))
            for segment in pack.segments
        )
    finally:
        skillctl.close()


def test_runner_step_with_real_skill_selects_skill_at_bootstrap_and_hydrates_entry_context_linear(
    tmp_path: Path,
) -> None:
    skillctl = Skill(_skill_cfg(tmp_path, known_tools=["http_request"]))
    try:
        skill_id, version_hash, warnings = skillctl.ingest_file(
            _fixture_path("openai", "linear", "SKILL.md")
        )
        assert not any(item.startswith("lint.error:") for item in warnings)

        context_api = ContextCtlAdapter(
            ContextCtlService(
                identityctl=_IdentityClient(),
                sessctl=_SessionClient(),
                memctl=_MemoryClient(),
                artifactctl=_ArtifactClient(),
                skillctl=skillctl,
            )
        )
        session = LocalSessionStore(tmp_path / "sessions")
        llm = _FakeLLM(selected_skill_id=skill_id)
        runner = BrainRunner(
            profile=_profile(),
            session_api=session,
            context_api=context_api,
            llm_api=llm,
            tool_api=LocalToolAdapter(),
            a2a_api=LocalA2AAdapter(),
            memory_api=LocalMemoryAdapter(tmp_path / "memory"),
            policy_api=LocalPolicyAdapter(),
            skill_api=skillctl,
            options=RunnerOptions(
                reflection_enabled=False,
                metactl_enabled=False,
                skill_selection_strategy="llm",
            ),
        )

        runner.step(
            session_id="s-sfle-linear",
            user_input="I need to triage a Linear issue ENG-123.",
            trace_id="trace-sfle-linear",
        )

        skill_selection_calls = [
            call
            for call in llm.calls
            if call.get("schema") is not None
            and call["schema"].__name__ == "SkillSubsetSelection"
        ]
        assert not skill_selection_calls

        entry_calls = [call for call in llm.calls if call.get("kind") == "entry"]
        assert entry_calls
        entry_message_text = "\n".join(_message_contents(entry_calls[0]["messages"]))
        assert "[SKILL SNIPPET]" in entry_message_text
        assert "Skill: Linear" in entry_message_text
        manifest_events = [
            event
            for event in session.list_events("s-sfle-linear")
            if event["type"] == "context.manifest.created"
        ]
        assert manifest_events
        included_segment_ids = (
            manifest_events[-1]["payload"].get("included_segment_ids") or []
        )
        assert "retrieval:skill:linear" in included_segment_ids
    finally:
        skillctl.close()


def test_runner_step_with_real_skill_selects_skill_at_bootstrap_and_hydrates_entry_context_mcp_builder(
    tmp_path: Path,
) -> None:
    skillctl = Skill(
        _skill_cfg(tmp_path, known_tools=["file", "browser", "http_request"])
    )
    try:
        skill_id, version_hash, warnings = skillctl.ingest_file(
            _fixture_path("anthropic", "mcp_builder", "SKILL.md")
        )
        assert not any(item.startswith("lint.error:") for item in warnings)

        context_api = ContextCtlAdapter(
            ContextCtlService(
                identityctl=_IdentityClient(),
                sessctl=_SessionClient(),
                memctl=_MemoryClient(),
                artifactctl=_ArtifactClient(),
                skillctl=skillctl,
            )
        )
        session = LocalSessionStore(tmp_path / "sessions")
        llm = _FakeLLM(selected_skill_id=skill_id)
        runner = BrainRunner(
            profile=_profile(),
            session_api=session,
            context_api=context_api,
            llm_api=llm,
            tool_api=LocalToolAdapter(),
            a2a_api=LocalA2AAdapter(),
            memory_api=LocalMemoryAdapter(tmp_path / "memory"),
            policy_api=LocalPolicyAdapter(),
            skill_api=skillctl,
            options=RunnerOptions(
                reflection_enabled=False,
                metactl_enabled=False,
                skill_selection_strategy="llm",
            ),
        )

        runner.step(
            session_id="s-sfle-mcp-builder",
            user_input="Help me build an MCP server skill.",
            trace_id="trace-sfle-mcp-builder",
        )

        skill_selection_calls = [
            call
            for call in llm.calls
            if call.get("schema") is not None
            and call["schema"].__name__ == "SkillSubsetSelection"
        ]
        assert not skill_selection_calls

        entry_calls = [call for call in llm.calls if call.get("kind") == "entry"]
        assert entry_calls
        entry_message_text = "\n".join(_message_contents(entry_calls[0]["messages"]))
        assert "[SKILL SNIPPET]" in entry_message_text
        assert "Skill: mcp_builder" in entry_message_text
        manifest_events = [
            event
            for event in session.list_events("s-sfle-mcp-builder")
            if event["type"] == "context.manifest.created"
        ]
        assert manifest_events
        included_segment_ids = (
            manifest_events[-1]["payload"].get("included_segment_ids") or []
        )
        assert "retrieval:skill:mcp_builder" in included_segment_ids
    finally:
        skillctl.close()


def test_runner_step_with_real_skill_dense_catalog_selects_requested_skill_directly(
    tmp_path: Path,
) -> None:
    skillctl = Skill(
        _skill_cfg(
            tmp_path,
            known_tools=[
                "file",
                "browser",
                "http_request",
                "web.search",
                "web.fetch",
                "playwright",
                "slack",
                "github",
            ],
        )
    )
    try:
        ingested = _ingest_fixture_bundle(skillctl, _COMPLEX_FIXTURES)
        for _provider, name in _COMPLEX_FIXTURES:
            _skill_id, _version_hash, warnings = ingested[name]
            assert not any(item.startswith("lint.error:") for item in warnings)

        expected_skill_id, _version_hash, _warnings = ingested["news_digest"]
        context_api = ContextCtlAdapter(
            ContextCtlService(
                identityctl=_IdentityClient(),
                sessctl=_SessionClient(),
                memctl=_MemoryClient(),
                artifactctl=_ArtifactClient(),
                skillctl=skillctl,
            )
        )
        session = LocalSessionStore(tmp_path / "sessions")
        llm = _FakeLLM(selected_skill_id=expected_skill_id)
        runner = BrainRunner(
            profile=_profile(),
            session_api=session,
            context_api=context_api,
            llm_api=llm,
            tool_api=LocalToolAdapter(),
            a2a_api=LocalA2AAdapter(),
            memory_api=LocalMemoryAdapter(tmp_path / "memory"),
            policy_api=LocalPolicyAdapter(),
            skill_api=skillctl,
            options=RunnerOptions(
                reflection_enabled=False,
                metactl_enabled=False,
                skill_selection_strategy="llm",
            ),
        )

        runner.step(
            session_id="s-sfle-news-digest",
            user_input=(
                "Use the news_digest skill for a Slack-ready digest of "
                "the most important AI policy news from this week."
            ),
            trace_id="trace-sfle-news-digest",
        )

        skill_selection_calls = [
            call
            for call in llm.calls
            if call.get("schema") is not None
            and call["schema"].__name__ == "SkillSubsetSelection"
        ]
        assert not skill_selection_calls

        entry_calls = [call for call in llm.calls if call.get("kind") == "entry"]
        assert entry_calls
        entry_message_text = "\n".join(_message_contents(entry_calls[0]["messages"]))
        assert "[SKILL SNIPPET]" in entry_message_text
        assert "Skill: news_digest" in entry_message_text

        skill_events = [
            event
            for event in session.list_events("s-sfle-news-digest")
            if event["type"] == "skill.selected"
        ]
        assert skill_events
        payload = dict(skill_events[-1].get("payload", {}))
        skill_ref = dict(payload.get("skill_ref", {}))
        assert skill_ref.get("id") == expected_skill_id
        assert payload.get("primary_skill_id") == expected_skill_id
        assert payload.get("selected_skill_ids") == [expected_skill_id]
        assert payload.get("selected_skill_count") == 1
        assert payload.get("selection_mode") == "direct"
    finally:
        skillctl.close()


def test_runner_step_with_real_skill_dense_catalog_empty_selection_stays_unloaded(
    tmp_path: Path,
) -> None:
    skillctl = Skill(
        _skill_cfg(
            tmp_path,
            known_tools=[
                "file",
                "browser",
                "http_request",
                "web.search",
                "web.fetch",
                "playwright",
                "slack",
                "github",
            ],
        )
    )
    try:
        ingested = _ingest_fixture_bundle(skillctl, _COMPLEX_FIXTURES)
        for _provider, name in _COMPLEX_FIXTURES:
            _skill_id, _version_hash, warnings = ingested[name]
            assert not any(item.startswith("lint.error:") for item in warnings)

        context_api = ContextCtlAdapter(
            ContextCtlService(
                identityctl=_IdentityClient(),
                sessctl=_SessionClient(),
                memctl=_MemoryClient(),
                artifactctl=_ArtifactClient(),
                skillctl=skillctl,
            )
        )
        session = LocalSessionStore(tmp_path / "sessions")
        llm = _FakeLLM(selected_skill_id=None)
        runner = BrainRunner(
            profile=_profile(),
            session_api=session,
            context_api=context_api,
            llm_api=llm,
            tool_api=LocalToolAdapter(),
            a2a_api=LocalA2AAdapter(),
            memory_api=LocalMemoryAdapter(tmp_path / "memory"),
            policy_api=LocalPolicyAdapter(),
            skill_api=skillctl,
            options=RunnerOptions(
                reflection_enabled=False,
                metactl_enabled=False,
                skill_selection_strategy="llm",
            ),
        )

        runner.step(
            session_id="s-sfle-missing-skill",
            user_input=(
                "I need the first four steps only for a totally_missing_skill workflow."
            ),
            trace_id="trace-sfle-missing-skill",
        )

        skill_selection_calls = [
            call
            for call in llm.calls
            if call.get("schema") is not None
            and call["schema"].__name__ == "SkillSubsetSelection"
        ]
        assert skill_selection_calls

        entry_calls = [call for call in llm.calls if call.get("kind") == "entry"]
        assert entry_calls
        entry_message_text = "\n".join(_message_contents(entry_calls[0]["messages"]))
        assert "[SKILL SNIPPET]" not in entry_message_text

        selected_events = [
            event
            for event in session.list_events("s-sfle-missing-skill")
            if event["type"] == "skill.selected"
        ]
        assert not selected_events

        prerouting_events = [
            event
            for event in session.list_events("s-sfle-missing-skill")
            if event["type"] == "skill.prerouting"
        ]
        assert prerouting_events
        payload = dict(prerouting_events[-1].get("payload", {}))
        assert payload.get("fail_closed_reason") is None
        assert payload.get("selected_skill_ids") == []
        assert payload.get("selected_skill_count") == 0
    finally:
        skillctl.close()
