from __future__ import annotations
from tests._csc_fixtures import _csc_install_default_agent

import asyncio
import json
import logging
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from openminion.base.config import OpenMinionConfig
from openminion.base.types import Message
from openminion.modules.identity.models import AgentProfile
from openminion.modules.identity.runtime.service import IdentityCtl
from openminion.modules.identity.storage.store import SQLiteIdentityStore
from openminion.services.runtime.plugins import PluginRegistry
from openminion.modules.llm.providers.base import ProviderRequest
from openminion.services.brain.service import BrainBridgeService
from openminion.modules.tool import build_default_tool_registry


class _DummySessionApi:
    def __init__(self) -> None:
        self.state = {}
        self.events: dict[str, list[dict[str, object]]] = {}

    def get_latest_working_state(self, session_id: str):
        return self.state.get(session_id, {"status": "waiting_user"})

    def put_working_state(self, session_id: str, state_inline=None):
        self.state[session_id] = state_inline or {}

    def append_event(
        self,
        session_id: str,
        type: str,
        payload: dict[str, object],
        *,
        agent_id=None,
        trace_id=None,
        task_id=None,
        parent_id=None,
        artifact_refs=None,
        memory_refs=None,
        status=None,
        error=None,
    ):
        del agent_id, task_id, parent_id, artifact_refs, memory_refs, status, error
        self.events.setdefault(session_id, []).append(
            {"type": type, "payload": payload, "trace_id": trace_id}
        )
        return f"{session_id}-event-{len(self.events[session_id])}"

    def list_events(self, session_id: str):
        return list(self.events.get(session_id, []))


class _CaptureSessionApi(_DummySessionApi):
    def __init__(self) -> None:
        super().__init__()
        self.turns: dict[str, list[dict[str, str]]] = {}

    def append_turn(
        self,
        session_id: str,
        role: str,
        content: str,
        attachments=None,
        meta=None,
    ):
        del attachments, meta
        self.turns.setdefault(session_id, []).append({"role": role, "content": content})
        return f"{session_id}-{len(self.turns[session_id])}"

    def list_turns(self, session_id: str):
        return list(self.turns.get(session_id, []))


class _DummyRunner:
    def __init__(self, *, step_out: SimpleNamespace):
        self.step_out = step_out
        self.session_api = _DummySessionApi()
        self.last_run: dict[str, object] | None = None
        self.profile = SimpleNamespace(
            budgets=SimpleNamespace(
                max_ticks_per_user_turn=40,
                max_tool_calls=16,
                max_a2a_calls=5,
                max_total_llm_tokens=100000,
                max_elapsed_ms=120000,
            )
        )

    def run(
        self,
        *,
        session_id: str,
        user_input: str,
        trace_id=None,
        forced_tools=None,
        capability_category=None,
        trigger=None,
        progress_callback=None,
        approval_callback=None,
    ):
        self.last_run = {
            "session_id": session_id,
            "user_input": user_input,
            "trace_id": trace_id,
            "forced_tools": forced_tools,
            "capability_category": capability_category,
            "trigger": trigger,
            "progress_callback": progress_callback,
            "approval_callback": approval_callback,
        }
        return self.step_out


class _FakeProvider:
    name = "fake-provider"

    def __init__(
        self, *, follow_text: str = "follow-up", follow_model: str = "fake-model"
    ) -> None:
        self.follow_text = follow_text
        self.follow_model = follow_model

    async def generate(self, req: ProviderRequest):
        return SimpleNamespace(
            text=self.follow_text,
            model=self.follow_model,
            tool_calls=[],
            usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            finish_reason="stop",
        )


class _CaptureProvider:
    name = "capture-provider"

    def __init__(self) -> None:
        self.requests: list[ProviderRequest] = []

    async def generate(self, req: ProviderRequest):
        self.requests.append(req)
        return SimpleNamespace(
            text="ok",
            model="capture-model",
            tool_calls=[],
            usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            finish_reason="stop",
        )


def _make_step_out(
    *,
    status: str = "running",
    ok: bool = True,
    tool_name: str = "dummy_tool",
    summary: str = "tool summary",
    message: str = "Tool result ready.",
):
    action_status = "success" if ok else "error"
    action_result = SimpleNamespace(
        status=action_status,
        outputs={"value": 1},
        summary=summary,
        error=None,
        command_id="cmd-1",
    )

    class _Step:
        def __init__(self, command_id: str) -> None:
            self.command_id = command_id

        def model_dump(self, mode: str = "json"):
            return {
                "command_id": self.command_id,
                "kind": "tool",
                "tool_name": tool_name,
            }

    plan = SimpleNamespace(steps=[_Step("cmd-1")])
    working_state = SimpleNamespace(plan=plan, llm_calls_used=1)
    return SimpleNamespace(
        message=message,
        status=status,
        action_result=action_result,
        working_state=working_state,
    )


def _build_service(
    step_out: SimpleNamespace,
    provider: _FakeProvider,
    *,
    session_api: _DummySessionApi | None = None,
    config: OpenMinionConfig | None = None,
) -> BrainBridgeService:
    config = config or OpenMinionConfig()
    if not config.agents:
        _csc_install_default_agent(config, provider="echo")
    plugins = PluginRegistry()

    log = logging.getLogger("brain-bridge-test")
    session_adapter = session_api or _DummySessionApi()

    with (
        patch(
            "openminion.services.brain.service.create_session_adapter",
            return_value=session_adapter,
        ),
        patch(
            "openminion.services.brain.service.create_context_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_tool_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_a2a_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_memory_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_policy_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_safety_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_rlm_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.BrainRunner",
            return_value=_DummyRunner(step_out=step_out),
        ),
    ):
        service = BrainBridgeService(
            config=config,
            plugins=plugins,
            provider=provider,
            logger=log,
            tools=None,
            security_policy=None,
            self_improvement=None,
            mode="auto",
            db_path="/tmp/brain-tests.db",
        )

    # Force runner to dummy for test consistency
    service._runner = _DummyRunner(step_out=step_out)
    if session_api is not None:
        service._runner.session_api = session_api
    return service


def test_brain_tool_metadata_success():
    step_out = _make_step_out(ok=True)
    provider = _FakeProvider(follow_text="final", follow_model="follow-model")
    service = _build_service(step_out, provider)

    response = asyncio.run(
        service.run_turn(
            Message(
                channel="console", target="me", body="hi", metadata={"session_id": "s1"}
            )
        )
    )

    metadata = response.metadata
    assert metadata["tool_execution_count"] == "1"
    assert metadata["tool_loop_termination_reason"] == "tool_final"
    tool_results = json.loads(metadata["tool_results"])
    assert len(tool_results) == 1
    assert tool_results[0]["ok"] is True
    assert metadata["tool_verified"] == "true"


def test_brain_bridge_forwards_progress_callback_to_runner():
    step_out = _make_step_out(ok=True)
    provider = _FakeProvider(follow_text="final", follow_model="follow-model")
    service = _build_service(step_out, provider)
    captured = []
    progress_callback = captured.append

    asyncio.run(
        service.run_turn(
            Message(
                channel="console",
                target="me",
                body="hi",
                metadata={"session_id": "s-progress"},
            ),
            progress_callback=progress_callback,
        )
    )

    assert service._runner.last_run is not None
    assert service._runner.last_run["progress_callback"] is progress_callback


def test_brain_identity_metadata_uses_explicit_fallback_sentinels():
    step_out = _make_step_out(ok=True)
    provider = _FakeProvider(follow_text="final", follow_model="follow-model")
    service = _build_service(step_out, provider)

    response = asyncio.run(
        service.run_turn(
            Message(
                channel="console",
                target="me",
                body="hi",
                metadata={"session_id": "s-identity-fallback"},
            )
        )
    )

    metadata = response.metadata
    assert metadata.get("identity_profile_version") == "none"
    assert metadata.get("identity_render_version") == "none"
    assert metadata.get("identity_purpose") == "none"
    assert metadata.get("identity_budget_used_tokens") == "0"
    assert metadata.get("identity_budget_max_tokens") == "0"


def test_brain_bridge_emits_identity_metadata_when_identity_runtime_is_configured():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "identity.db"
        ctl = IdentityCtl(store=SQLiteIdentityStore(sqlite_path=str(db_path)))
        ctl.upsert_profile(
            AgentProfile.model_validate(
                {
                    "agent_id": "openminion",
                    "display_name": "OpenMinion",
                    "profile_revision": 1,
                    "role": {
                        "mission": "Help with pragmatic task execution.",
                        "responsibilities": ["Answer directly."],
                        "hard_constraints": ["Ask before destructive actions."],
                    },
                    "personality": {
                        "tone": "clear",
                        "verbosity": "normal",
                    },
                    "risk": {
                        "risk_level": "medium",
                        "confirm_before": ["destructive_actions"],
                    },
                    "tool_posture": {
                        "tool_use": "restricted",
                    },
                    "meta": {"source": "yaml"},
                }
            )
        )
        ctl.close()

        step_out = _make_step_out(ok=True)
        provider = _FakeProvider(follow_text="final", follow_model="follow-model")
        config = OpenMinionConfig()
        _csc_install_default_agent(config)  # type: ignore[attr-defined]
        config.identity.root = str(db_path)
        service = _build_service(step_out, provider, config=config)

        response = asyncio.run(
            service.run_turn(
                Message(
                    channel="console",
                    target="me",
                    body="hi",
                    metadata={"session_id": "s-identity-present"},
                )
            )
        )

        metadata = response.metadata
        assert metadata.get("identity_profile_version") not in {"", "none"}
        assert metadata.get("identity_render_version") not in {"", "none"}
        assert metadata.get("identity_purpose") == "act"


def test_brain_tool_success_skips_follow_up_inference_and_events():
    step_out = _make_step_out(ok=True)
    provider = _FakeProvider(follow_text="final", follow_model="follow-model")
    session_api = _DummySessionApi()
    service = _build_service(step_out, provider, session_api=session_api)

    response = asyncio.run(
        service.run_turn(
            Message(
                channel="console",
                target="me",
                body="hi",
                metadata={"session_id": "s-follow", "request_id": "trace-follow"},
            )
        )
    )

    llm_counts = json.loads(response.metadata["llm_call_counts_by_purpose"])
    assert llm_counts["follow_up"] == 0
    assert int(response.metadata["llm_calls_count"]) >= 1

    events = session_api.list_events("s-follow")
    follow_up_started = [
        item
        for item in events
        if item["type"] == "llm.call.started"
        and item["payload"].get("purpose") == "respond_followup"
    ]
    follow_up_completed = [
        item
        for item in events
        if item["type"] == "llm.call.completed"
        and item["payload"].get("purpose") == "respond_followup"
    ]
    assert follow_up_started == []
    assert follow_up_completed == []
    assert response.metadata["tool_loop_termination_reason"] == "tool_final"
    assert response.text.endswith("Tool result ready.")


def test_brain_tool_success_empty_message_uses_tool_fallback_text():
    step_out = _make_step_out(ok=True, message="")
    provider = _FakeProvider(follow_text="ignored", follow_model="follow-model")
    service = _build_service(step_out, provider)

    response = asyncio.run(
        service.run_turn(
            Message(
                channel="console",
                target="me",
                body="hi",
                metadata={
                    "session_id": "s-follow-empty",
                    "request_id": "trace-follow-empty",
                },
            )
        )
    )

    assert response.text.endswith("tool summary")
    assert response.metadata["tool_loop_termination_reason"] == "tool_final"


def test_brain_weather_tool_success_preserves_brain_close_message():
    step_out = _make_step_out(
        ok=True,
        tool_name="weather",
        message="The weather in San Francisco is 16C with light clouds.",
    )
    provider = _FakeProvider(
        follow_text="hallucinated follow-up", follow_model="follow-model"
    )
    session_api = _DummySessionApi()
    service = _build_service(step_out, provider, session_api=session_api)

    response = asyncio.run(
        service.run_turn(
            Message(
                channel="console",
                target="me",
                body="weather in san francisco",
                metadata={"session_id": "s-weather", "request_id": "trace-weather"},
            )
        )
    )

    llm_counts = json.loads(response.metadata["llm_call_counts_by_purpose"])
    assert llm_counts["follow_up"] == 0
    assert response.metadata["tool_loop_termination_reason"] == "tool_final"
    assert response.text.endswith(
        "The weather in San Francisco is 16C with light clouds."
    )

    events = session_api.list_events("s-weather")
    follow_up_events = [
        item
        for item in events
        if item["type"] in {"llm.call.started", "llm.call.completed"}
        and item["payload"].get("purpose") == "respond_followup"
    ]
    assert follow_up_events == []


def test_brain_weather_tool_no_longer_runtime_preserves_distinct_close_message() -> (
    None
):
    step_out = _make_step_out(
        ok=True,
        tool_name="weather",
        summary="Los Angeles, United States: 24.7C.",
        message="The weather in Los Angeles is 24.7C with light wind.",
    )
    provider = _FakeProvider(
        follow_text="hallucinated follow-up", follow_model="follow-model"
    )
    service = _build_service(step_out, provider, session_api=_DummySessionApi())

    response = asyncio.run(
        service.run_turn(
            Message(
                channel="console",
                target="me",
                body="weather in los angeles",
                metadata={
                    "session_id": "s-weather-final",
                    "request_id": "trace-weather-final",
                },
            )
        )
    )

    llm_counts = json.loads(response.metadata["llm_call_counts_by_purpose"])
    assert llm_counts["follow_up"] == 0
    assert response.metadata["tool_loop_termination_reason"] == "tool_final"
    assert response.text.endswith(
        "The weather in Los Angeles is 24.7C with light wind."
    )


def test_brain_tool_metadata_failure_sets_no_success():
    step_out = _make_step_out(ok=False)
    provider = _FakeProvider(follow_text="", follow_model="model")
    service = _build_service(step_out, provider)

    response = asyncio.run(
        service.run_turn(
            Message(
                channel="console", target="me", body="hi", metadata={"session_id": "s2"}
            )
        )
    )

    metadata = response.metadata
    assert metadata["tool_execution_count"] == "1"
    assert metadata["tool_loop_termination_reason"] == "tool_no_success"
    tool_results = json.loads(metadata["tool_results"])
    assert len(tool_results) == 1
    assert tool_results[0]["ok"] is False
    assert metadata["tool_verified"] == "false"


def test_turn_reset_resume_intent_preserves_existing_plan():
    step_out = _make_step_out(ok=True)
    provider = _FakeProvider()
    service = _build_service(step_out, provider)
    runner = service._runner
    assert runner is not None

    runner.session_api.put_working_state(
        "resume-s",
        state_inline={
            "status": "waiting_user",
            "plan": {"steps": [{"command_id": "cmd-1"}, {"command_id": "cmd-2"}]},
            "cursor": 1,
            "llm_calls_max": 8,
        },
    )
    service._reset_state_for_new_input(
        runner=runner,
        session_id="resume-s",
        user_input="continue with previous plan",
    )

    updated = runner.session_api.get_latest_working_state("resume-s")
    assert isinstance(updated, dict)
    assert updated.get("plan") is not None
    assert int(updated.get("cursor", -1)) == 1


def test_turn_reset_new_goal_clears_existing_plan():
    step_out = _make_step_out(ok=True)
    provider = _FakeProvider()
    service = _build_service(step_out, provider)
    runner = service._runner
    assert runner is not None

    runner.session_api.put_working_state(
        "new-goal-s",
        state_inline={
            "status": "waiting_user",
            "plan": {"steps": [{"command_id": "cmd-1"}, {"command_id": "cmd-2"}]},
            "cursor": 1,
            "llm_calls_max": 8,
        },
    )
    service._reset_state_for_new_input(
        runner=runner,
        session_id="new-goal-s",
        user_input="start a different task from scratch",
    )

    updated = runner.session_api.get_latest_working_state("new-goal-s")
    assert isinstance(updated, dict)
    assert updated.get("plan") is None
    assert int(updated.get("cursor", -1)) == 0


def test_brain_tool_policy_denied_emits_security_event():
    action_error = SimpleNamespace(
        code="tool_budget_cost_exceeded",
        message="tool budget exceeded",
        details={"max_budget_cost_per_run": 3, "budget_cost_total": 4},
    )
    action_result = SimpleNamespace(
        status="error",
        outputs={"error": "tool_budget_cost_exceeded"},
        summary="policy denied",
        error=action_error,
        command_id="cmd-2",
    )

    class _Step:
        def __init__(self, command_id: str) -> None:
            self.command_id = command_id

        def model_dump(self, mode: str = "json"):
            return {
                "command_id": self.command_id,
                "kind": "tool",
                "tool_name": "dummy_tool",
            }

    plan = SimpleNamespace(steps=[_Step("cmd-2")])
    working_state = SimpleNamespace(plan=plan, llm_calls_used=1)
    step_out = SimpleNamespace(
        message="",
        status="running",
        action_result=action_result,
        working_state=working_state,
    )
    provider = _FakeProvider(follow_text="", follow_model="model")
    service = _build_service(step_out, provider)

    response = asyncio.run(
        service.run_turn(
            Message(
                channel="console", target="me", body="hi", metadata={"session_id": "s3"}
            )
        )
    )
    metadata = response.metadata
    assert "security_events" in metadata
    assert "tool_budget_cost_exceeded" in metadata["security_events"]
    assert "tool_budget" in metadata


def test_brain_forced_tools_use_runner_without_delegate():
    step_out = _make_step_out(ok=True)
    provider = _FakeProvider(follow_text="final", follow_model="follow-model")
    service = _build_service(step_out, provider)
    delegated = AsyncMock()

    with patch("openminion.services.agent.AgentService.run_turn", delegated):
        response = asyncio.run(
            service.run_turn(
                Message(
                    channel="console",
                    target="me",
                    body="latest news",
                    metadata={"session_id": "s1"},
                ),
                forced_tools=["search.tavily.search"],
            )
        )

    delegated.assert_not_called()
    assert response.channel == "console"
    assert response.target == "me"
    assert service._runner is not None
    assert service._runner.last_run is not None
    assert service._runner.last_run["forced_tools"] == ["search.tavily.search"]


def test_brain_session_context_parity():
    import tempfile
    from pathlib import Path
    from openminion.modules.brain.adapters.session import LocalSessionStore

    with tempfile.TemporaryDirectory() as tmpdir:
        session_store = LocalSessionStore(root=Path(tmpdir))
        session_store.append_turn("sess-1", "user", "hello")
        session_store.append_turn("sess-1", "assistant", "hi there")
        session_store.append_event("sess-1", "tool_call", {"tool": "test"})

        turns = session_store.list_turns("sess-1")
        events = session_store.list_events("sess-1")

        assert len(turns) == 2
        assert len(events) == 1
        assert turns[0]["content"] == "hello"
        assert events[0]["type"] == "tool_call"


def test_brain_tool_loop_duplicate_handling():
    step_out = _make_step_out(ok=True)
    provider = _FakeProvider(follow_text="final", follow_model="follow-model")
    service = _build_service(step_out, provider)

    response1 = asyncio.run(
        service.run_turn(
            Message(
                channel="console", target="me", body="hi", metadata={"session_id": "s3"}
            )
        )
    )
    asyncio.run(
        service.run_turn(
            Message(
                channel="console",
                target="me",
                body="hi again",
                metadata={"session_id": "s3"},
            )
        )
    )

    assert "tool_execution_count" in response1.metadata
    assert "tool_loop_termination_reason" in response1.metadata
    assert response1.metadata["tool_loop_termination_reason"] in {
        "tool_final",
        "tool_no_success",
    }


def test_brain_mode_smoke_chat():
    step_out = _make_step_out(ok=True)
    provider = _FakeProvider(
        follow_text="Hello! How can I help?", follow_model="brain-orchestrator"
    )
    service = _build_service(step_out, provider)

    response = asyncio.run(
        service.run_turn(
            Message(
                channel="console",
                target="me",
                body="hi",
                metadata={"session_id": "smoke-1"},
            )
        )
    )

    assert response.text is not None
    assert len(response.text) > 0
    assert response.metadata["agent"] is not None
    assert response.metadata["model"] == "brain-orchestrator"
    assert "inference_steps" in response.metadata


def test_brain_decide_requests_are_schema_only():
    from openminion.modules.brain.schemas import DecisionAdapter

    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    plugins = PluginRegistry()
    provider = _CaptureProvider()
    log = logging.getLogger("brain-bridge-test")

    class _CaptureRunnerCtor:
        def __init__(self, **kwargs) -> None:
            self.llm_api = kwargs["llm_api"]
            # bootstrap reads `runner.task_manager` to wire the
            # checkpoint manager into the long-running goal runtime;
            # capture stub doesn't need a real task manager.
            self.task_manager = kwargs.get("task_manager")
            self.session_api = kwargs["session_api"]
            self.profile = SimpleNamespace(
                budgets=SimpleNamespace(
                    max_ticks_per_user_turn=40,
                    max_tool_calls=16,
                    max_a2a_calls=5,
                    max_total_llm_tokens=100000,
                    max_elapsed_ms=120000,
                )
            )

    with (
        patch(
            "openminion.services.brain.service.create_session_api",
            return_value=_DummySessionApi(),
        ),
        patch(
            "openminion.services.brain.service.create_context_api",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_tool_api",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_a2a_api",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_memory_api",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_policy_api",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_safety_api",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.init_rlm_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_compress_api",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_skill_api",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.init_retrieve_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_llm_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.factory.vector.init_vector_adapter",
            return_value=(None, None),
        ),
        patch(
            "openminion.services.brain.service.BrainBridgeService._validate_adapter_contracts",
            return_value=None,
        ),
        patch(
            "openminion.services.brain.service.BrainBridgeService._validate_runner_contract",
            return_value=None,
        ),
        patch("openminion.services.brain.service.BrainRunner", _CaptureRunnerCtor),
    ):
        service = BrainBridgeService(
            config=config,
            plugins=plugins,
            provider=provider,
            logger=log,
            tools=build_default_tool_registry(),
            security_policy=None,
            self_improvement=None,
            mode="auto",
            db_path="/tmp/brain-tests.db",
        )

    runner = service._get_runner()
    result = runner.llm_api.call_structured(
        model="brain-orchestrator",
        purpose="decide",
        context={
            "messages": [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "show me all tools"},
            ]
        },
        schema=DecisionAdapter,
    )

    assert isinstance(result, dict)
    assert provider.requests, "Expected at least one provider request"
    request = provider.requests[-1]
    tool_names = {spec.name for spec in request.tools}
    assert "submit_output" in tool_names
    assert "weather" not in tool_names
    assert request.tool_choice == {
        "type": "function",
        "function": {"name": "submit_output"},
    }


def test_brain_bridge_runner_profile_preserves_model_capability_overrides() -> None:
    config = OpenMinionConfig.from_dict(
        {
            "agents": {
                "hello-agent": {
                    "name": "hello-agent",
                    "provider": "openrouter",
                    "model_capability_overrides": {
                        "gpt4_default": {"decision_strategy": "two_step_classify"}
                    },
                },
            },
            "default_agent": "hello-agent",
        }
    )
    plugins = PluginRegistry()
    provider = _CaptureProvider()
    log = logging.getLogger("brain-bridge-test")

    captured_profile = None

    class _CaptureRunnerCtor:
        def __init__(self, **kwargs) -> None:
            nonlocal captured_profile
            captured_profile = kwargs["profile"]
            self.llm_api = kwargs["llm_api"]
            # bootstrap reads `runner.task_manager` to wire the
            # checkpoint manager into the long-running goal runtime;
            # capture stub doesn't need a real task manager.
            self.task_manager = kwargs.get("task_manager")
            self.session_api = kwargs["session_api"]
            self.profile = kwargs["profile"]

    with (
        patch(
            "openminion.services.brain.service.create_session_api",
            return_value=_DummySessionApi(),
        ),
        patch(
            "openminion.services.brain.service.create_context_api",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_tool_api",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_a2a_api",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_memory_api",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_policy_api",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_safety_api",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.init_rlm_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_compress_api",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_skill_api",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.init_retrieve_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.factory.vector.init_vector_adapter",
            return_value=(None, None),
        ),
        patch(
            "openminion.services.brain.service.BrainBridgeService._validate_adapter_contracts",
            return_value=None,
        ),
        patch(
            "openminion.services.brain.service.BrainBridgeService._validate_runner_contract",
            return_value=None,
        ),
        patch("openminion.services.brain.service.BrainRunner", _CaptureRunnerCtor),
    ):
        service = BrainBridgeService(
            config=config,
            plugins=plugins,
            provider=provider,
            logger=log,
            tools=build_default_tool_registry(),
            security_policy=None,
            self_improvement=None,
            mode="auto",
            db_path="/tmp/brain-tests.db",
        )
        service._get_runner()

        assert captured_profile is not None
        assert captured_profile.model_capability_overrides == {
            "gpt4_default": {"decision_strategy": "two_step_classify"}
        }


def test_brain_bridge_reuses_runtime_registry_for_tool_adapter() -> None:
    config = OpenMinionConfig.from_dict(
        {
            "agents": {"hello-agent": {"name": "hello-agent"}},
            "default_agent": "hello-agent",
        }
    )
    plugins = PluginRegistry()
    provider = _CaptureProvider()
    log = logging.getLogger("brain-bridge-test")
    runtime_registry = build_default_tool_registry()

    captured_tool_kwargs: dict[str, object] = {}

    class _CaptureRunnerCtor:
        def __init__(self, **kwargs) -> None:
            self.llm_api = kwargs["llm_api"]
            # bootstrap reads `runner.task_manager` to wire the
            # checkpoint manager into the long-running goal runtime;
            # capture stub doesn't need a real task manager.
            self.task_manager = kwargs.get("task_manager")
            self.session_api = kwargs["session_api"]
            self.profile = kwargs["profile"]

    with (
        patch(
            "openminion.services.brain.service.create_session_api",
            return_value=_DummySessionApi(),
        ),
        patch(
            "openminion.services.brain.service.create_context_api",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_tool_api",
            side_effect=lambda **kwargs: (
                captured_tool_kwargs.update(kwargs) or SimpleNamespace()
            ),
        ),
        patch(
            "openminion.services.brain.service.create_a2a_api",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_memory_api",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_policy_api",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_safety_api",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.init_rlm_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_compress_api",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_skill_api",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.init_retrieve_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_llm_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.factory.vector.init_vector_adapter",
            return_value=(None, None),
        ),
        patch(
            "openminion.services.brain.service.BrainBridgeService._validate_adapter_contracts",
            return_value=None,
        ),
        patch(
            "openminion.services.brain.service.BrainBridgeService._validate_runner_contract",
            return_value=None,
        ),
        patch("openminion.services.brain.service.BrainRunner", _CaptureRunnerCtor),
    ):
        service = BrainBridgeService(
            config=config,
            plugins=plugins,
            provider=provider,
            logger=log,
            tools=runtime_registry,
            security_policy=None,
            self_improvement=None,
            mode="auto",
            db_path="/tmp/brain-tests.db",
        )
        service._get_runner()

    assert captured_tool_kwargs["runtime_registry"] is runtime_registry


def test_brain_plan_requests_are_schema_only():
    from openminion.modules.brain.schemas import Plan
    from openminion.modules.llm.providers.base import ProviderResponse, ProviderToolCall

    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    plugins = PluginRegistry()
    log = logging.getLogger("brain-bridge-test")

    class _PlanCaptureProvider:
        name = "capture-provider"

        def __init__(self) -> None:
            self.requests: list[ProviderRequest] = []

        async def generate(self, req: ProviderRequest):
            self.requests.append(req)
            return ProviderResponse(
                text="",
                model="capture-model",
                tool_calls=[
                    ProviderToolCall(
                        id="plan-call-1",
                        name="submit_output",
                        arguments={
                            "objective": "weather summary",
                            "steps": [
                                {
                                    "kind": "tool",
                                    "title": "check weather",
                                    "tool_name": "weather",
                                    "args": {"location": "Tokyo"},
                                    "success_criteria": {"status": "success"},
                                }
                            ],
                            "stop_conditions": ["done"],
                            "assumptions": [],
                            "risk_summary": "low",
                            "success_criteria": {"status": "success"},
                        },
                    )
                ],
                usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                finish_reason="tool_calls",
            )

    provider = _PlanCaptureProvider()

    class _CaptureRunnerCtor:
        def __init__(self, **kwargs) -> None:
            self.llm_api = kwargs["llm_api"]
            # bootstrap reads `runner.task_manager` to wire the
            # checkpoint manager into the long-running goal runtime;
            # capture stub doesn't need a real task manager.
            self.task_manager = kwargs.get("task_manager")
            self.session_api = kwargs["session_api"]
            self.profile = SimpleNamespace(
                budgets=SimpleNamespace(
                    max_ticks_per_user_turn=40,
                    max_tool_calls=16,
                    max_a2a_calls=5,
                    max_total_llm_tokens=100000,
                    max_elapsed_ms=120000,
                )
            )

    with (
        patch(
            "openminion.services.brain.service.create_session_adapter",
            return_value=_DummySessionApi(),
        ),
        patch(
            "openminion.services.brain.service.create_context_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_tool_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_a2a_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_memory_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_policy_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_safety_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_rlm_adapter",
            return_value=SimpleNamespace(),
        ),
        patch("openminion.services.brain.service.BrainRunner", _CaptureRunnerCtor),
    ):
        service = BrainBridgeService(
            config=config,
            plugins=plugins,
            provider=provider,
            logger=log,
            tools=build_default_tool_registry(),
            security_policy=None,
            self_improvement=None,
            mode="auto",
            db_path="/tmp/brain-tests.db",
        )

    runner = service._get_runner()
    result = runner.llm_api.call_structured(
        model="brain-orchestrator",
        purpose="plan",
        context={
            "messages": [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "plan weather lookup"},
            ]
        },
        schema=Plan,
    )

    assert isinstance(Plan.model_validate(result), Plan)
    assert provider.requests, "Expected at least one provider request"
    request = provider.requests[-1]
    tool_names = {spec.name for spec in request.tools}
    assert "submit_output" in tool_names
    assert "weather" not in tool_names
    assert "browser" not in tool_names
    assert request.tool_choice == {
        "type": "function",
        "function": {"name": "submit_output"},
    }


def test_brain_bridge_runner_options_load_skill_selection_strategy_from_brain(
    tmp_path: Path,
):
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    plugins = PluginRegistry()
    log = logging.getLogger("brain-bridge-test")
    home_root = tmp_path / "home"
    data_root = tmp_path / "data"
    home_root.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)
    (home_root / "brain.yaml").write_text(
        "\n".join(
            [
                "brain:",
                "  budgets:",
                "    max_ticks_per_user_turn: 6",
                "    max_tool_calls: 4",
                "    max_a2a_calls: 0",
                "    max_total_llm_tokens: 4000",
                "    max_elapsed_ms: 120000",
                "  skill_selection_strategy: llm",
            ]
        ),
        encoding="utf-8",
    )

    class _CaptureRunnerCtor:
        contract_version = "v1"

        def __init__(self, **kwargs) -> None:
            self.options = kwargs["options"]
            self.llm_api = kwargs["llm_api"]
            # bootstrap reads `runner.task_manager` to wire the
            # checkpoint manager into the long-running goal runtime;
            # capture stub doesn't need a real task manager.
            self.task_manager = kwargs.get("task_manager")
            self.session_api = kwargs["session_api"]
            self.profile = SimpleNamespace(
                budgets=SimpleNamespace(
                    max_ticks_per_user_turn=40,
                    max_tool_calls=16,
                    max_a2a_calls=5,
                    max_total_llm_tokens=100000,
                    max_elapsed_ms=120000,
                )
            )

        def run(self, **kwargs):
            del kwargs
            return

        def step(self, **kwargs):
            del kwargs
            return

    with (
        patch(
            "openminion.services.brain.service.create_session_adapter",
            return_value=_DummySessionApi(),
        ),
        patch(
            "openminion.services.brain.service.create_context_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_tool_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_a2a_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_memory_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_policy_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_safety_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_rlm_adapter",
            return_value=SimpleNamespace(),
        ),
        patch("openminion.services.brain.service.BrainRunner", _CaptureRunnerCtor),
    ):
        service = BrainBridgeService(
            config=config,
            plugins=plugins,
            provider=_CaptureProvider(),
            logger=log,
            tools=build_default_tool_registry(),
            security_policy=None,
            self_improvement=None,
            mode="auto",
            db_path=str(tmp_path / "brain-tests.db"),
            home_root=home_root,
            data_root=data_root,
        )
        runner = service._get_runner()
        assert runner.options.skill_selection_strategy == "llm"


def test_brain_judge_requests_are_schema_only():
    from openminion.modules.brain.execution import ClosureJudgment
    from openminion.modules.llm.providers.base import ProviderResponse, ProviderToolCall

    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    plugins = PluginRegistry()
    log = logging.getLogger("brain-bridge-test")

    class _JudgeCaptureProvider:
        name = "capture-provider"

        def __init__(self) -> None:
            self.requests: list[ProviderRequest] = []

        async def generate(self, req: ProviderRequest):
            self.requests.append(req)
            return ProviderResponse(
                text="",
                model="capture-model",
                tool_calls=[
                    ProviderToolCall(
                        id="judge-call-1",
                        name="submit_output",
                        arguments={
                            "satisfied": False,
                            "reason": "still pending",
                            "next_action": "replan",
                        },
                    )
                ],
                usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                finish_reason="tool_calls",
            )

    provider = _JudgeCaptureProvider()

    class _CaptureRunnerCtor:
        def __init__(self, **kwargs) -> None:
            self.llm_api = kwargs["llm_api"]
            # bootstrap reads `runner.task_manager` to wire the
            # checkpoint manager into the long-running goal runtime;
            # capture stub doesn't need a real task manager.
            self.task_manager = kwargs.get("task_manager")
            self.session_api = kwargs["session_api"]
            self.profile = SimpleNamespace(
                budgets=SimpleNamespace(
                    max_ticks_per_user_turn=40,
                    max_tool_calls=16,
                    max_a2a_calls=5,
                    max_total_llm_tokens=100000,
                    max_elapsed_ms=120000,
                )
            )

    with (
        patch(
            "openminion.services.brain.service.create_session_adapter",
            return_value=_DummySessionApi(),
        ),
        patch(
            "openminion.services.brain.service.create_context_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_tool_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_a2a_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_memory_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_policy_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_safety_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_rlm_adapter",
            return_value=SimpleNamespace(),
        ),
        patch("openminion.services.brain.service.BrainRunner", _CaptureRunnerCtor),
    ):
        service = BrainBridgeService(
            config=config,
            plugins=plugins,
            provider=provider,
            logger=log,
            tools=build_default_tool_registry(),
            security_policy=None,
            self_improvement=None,
            mode="auto",
            db_path="/tmp/brain-tests.db",
        )

    runner = service._get_runner()
    result = runner.llm_api.call_structured(
        model="brain-orchestrator",
        purpose="judge",
        context={
            "messages": [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "is this done?"},
            ]
        },
        schema=ClosureJudgment,
    )

    assert isinstance(ClosureJudgment.model_validate(result), ClosureJudgment)
    assert provider.requests, "Expected at least one provider request"
    request = provider.requests[-1]
    tool_names = {spec.name for spec in request.tools}
    assert "submit_output" in tool_names
    assert "weather" not in tool_names
    assert "browser" not in tool_names
    assert request.tool_choice == {
        "type": "function",
        "function": {"name": "submit_output"},
    }


def test_brain_decide_merges_system_context_into_system_prompt():
    from openminion.modules.brain.schemas import DecisionAdapter

    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    plugins = PluginRegistry()
    provider = _CaptureProvider()
    log = logging.getLogger("brain-bridge-test")

    class _CaptureRunnerCtor:
        def __init__(self, **kwargs) -> None:
            self.llm_api = kwargs["llm_api"]
            # bootstrap reads `runner.task_manager` to wire the
            # checkpoint manager into the long-running goal runtime;
            # capture stub doesn't need a real task manager.
            self.task_manager = kwargs.get("task_manager")
            self.session_api = kwargs["session_api"]
            self.profile = SimpleNamespace(
                budgets=SimpleNamespace(
                    max_ticks_per_user_turn=40,
                    max_tool_calls=16,
                    max_a2a_calls=5,
                    max_total_llm_tokens=100000,
                    max_elapsed_ms=120000,
                )
            )

    with (
        patch(
            "openminion.services.brain.service.create_session_adapter",
            return_value=_DummySessionApi(),
        ),
        patch(
            "openminion.services.brain.service.create_context_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_tool_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_a2a_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_memory_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_policy_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_safety_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_rlm_adapter",
            return_value=SimpleNamespace(),
        ),
        patch("openminion.services.brain.service.BrainRunner", _CaptureRunnerCtor),
    ):
        service = BrainBridgeService(
            config=config,
            plugins=plugins,
            provider=provider,
            logger=log,
            tools=build_default_tool_registry(),
            security_policy=None,
            self_improvement=None,
            mode="auto",
            db_path="/tmp/brain-tests.db",
        )

    runner = service._get_runner()
    _ = runner.llm_api.call_structured(
        model="brain-orchestrator",
        purpose="decide",
        context={
            "messages": [
                {"role": "system", "content": "STATIC PREFIX"},
                {
                    "role": "system",
                    "content": "Session context (compacted). Use this as continuity reference.",
                },
                {
                    "role": "system",
                    "content": "Agent canonical memory (cross-session):\\nState:\\nagent_id=test",
                },
                {"role": "user", "content": "say hi"},
            ]
        },
        schema=DecisionAdapter,
    )

    assert provider.requests, "Expected at least one provider request"
    request = provider.requests[-1]
    assert request.system_prompt.startswith("STATIC PREFIX")
    assert (
        "Session context (compacted). Use this as continuity reference."
        in request.system_prompt
    )
    assert "Agent canonical memory (cross-session):" in request.system_prompt
    history_roles = [item.role for item in request.history]
    history_text = "\n".join(item.content for item in request.history)
    assert "system" not in history_roles
    assert (
        "Session context (compacted). Use this as continuity reference."
        not in history_text
    )
    assert "Agent canonical memory (cross-session):" not in history_text


def test_brain_bridge_profile_uses_config_defaults_and_env_overrides():
    config = OpenMinionConfig()
    _csc_install_default_agent(config, provider="openai")
    config.providers.openai.model = "cfg-openai-model"
    config.runtime.agent_loop_max_steps = 9
    config.runtime.session_context_token_budget = 4321
    config.security.tool_policy.max_calls_per_run = 7
    plugins = PluginRegistry()
    provider = _CaptureProvider()
    log = logging.getLogger("brain-bridge-test")

    captured: dict[str, object] = {}

    class _CaptureRunnerCtor:
        def __init__(self, **kwargs) -> None:
            captured["profile"] = kwargs["profile"]
            captured["options"] = kwargs["options"]
            self.llm_api = kwargs["llm_api"]
            # bootstrap reads `runner.task_manager` to wire the
            # checkpoint manager into the long-running goal runtime;
            # capture stub doesn't need a real task manager.
            self.task_manager = kwargs.get("task_manager")
            self.session_api = kwargs["session_api"]
            self.profile = kwargs["profile"]

    with (
        patch.dict(
            "os.environ",
            {
                "OPENMINION_BRAIN_DECIDE_MODEL": "env-decide-model",
                "OPENMINION_BRAIN_MAX_TOOL_CALLS": "11",
                "OPENMINION_BRAIN_REFLECTION_ENABLED": "1",
                "OPENMINION_PLAN_AUTO_SCALE_MAX_LLM_CALLS": "33",
                "OPENMINION_PLAN_AUTO_SCALE_MAX_TICKS": "44",
                "OPENMINION_PLAN_AUTO_SCALE_MAX_TOKENS": "55000",
                "OPENMINION_STRICT_ADAPTER_CONTRACTS": "0",
            },
            clear=False,
        ),
        patch(
            "openminion.services.brain.service.create_session_adapter",
            return_value=_DummySessionApi(),
        ),
        patch(
            "openminion.services.brain.service.create_context_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_tool_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_a2a_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_memory_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_policy_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_safety_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_rlm_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.BrainRunner",
            _CaptureRunnerCtor,
        ),
    ):
        service = BrainBridgeService(
            config=config,
            plugins=plugins,
            provider=provider,
            logger=log,
            tools=None,
            security_policy=None,
            self_improvement=None,
            mode="auto",
            db_path="/tmp/brain-tests.db",
        )
        _ = service._get_runner()

    profile = captured["profile"]
    options = captured["options"]
    assert getattr(profile.llm_profiles, "decide_model") == "env-decide-model"
    assert getattr(profile.llm_profiles, "plan_model") == "cfg-openai-model"
    assert int(getattr(profile.budgets, "max_ticks_per_user_turn")) == 9
    assert int(getattr(profile.budgets, "max_tool_calls")) == 11
    assert int(getattr(profile.budgets, "max_total_llm_tokens")) == 4321
    assert bool(getattr(options, "reflection_enabled")) is True
    assert int(getattr(options, "plan_auto_scale_max_llm_calls")) == 33
    assert int(getattr(options, "plan_auto_scale_max_ticks")) == 44
    assert int(getattr(options, "plan_auto_scale_max_tokens")) == 55_000


def test_brain_bridge_unset_runtime_token_budget_uses_safe_floor():
    config = OpenMinionConfig()
    _csc_install_default_agent(config, provider="openrouter")
    config.runtime.session_context_token_budget = 0
    plugins = PluginRegistry()
    provider = _CaptureProvider()
    log = logging.getLogger("brain-bridge-test")

    captured: dict[str, object] = {}

    class _CaptureRunnerCtor:
        def __init__(self, **kwargs) -> None:
            captured["profile"] = kwargs["profile"]
            self.llm_api = kwargs["llm_api"]
            # bootstrap reads `runner.task_manager` to wire the
            # checkpoint manager into the long-running goal runtime;
            # capture stub doesn't need a real task manager.
            self.task_manager = kwargs.get("task_manager")
            self.session_api = kwargs["session_api"]
            self.profile = kwargs["profile"]

        def run(self, **kwargs):
            del kwargs
            return SimpleNamespace()

        def step(self, **kwargs):
            del kwargs
            return SimpleNamespace()

        contract_version = "v1"

    with (
        patch.dict(
            "os.environ", {"OPENMINION_STRICT_ADAPTER_CONTRACTS": "0"}, clear=False
        ),
        patch(
            "openminion.services.brain.service.create_session_adapter",
            return_value=_DummySessionApi(),
        ),
        patch(
            "openminion.services.brain.service.create_context_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_tool_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_a2a_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_memory_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_policy_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_safety_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_rlm_adapter",
            return_value=SimpleNamespace(),
        ),
        patch("openminion.services.brain.service.BrainRunner", _CaptureRunnerCtor),
    ):
        service = BrainBridgeService(
            config=config,
            plugins=plugins,
            provider=provider,
            logger=log,
            tools=None,
            security_policy=None,
            self_improvement=None,
            mode="auto",
            db_path="/tmp/brain-tests.db",
        )
        _ = service._get_runner()

    profile = captured["profile"]
    assert int(getattr(profile.budgets, "max_total_llm_tokens")) >= 100000


def test_brain_bridge_unset_runtime_token_budget_respects_explicit_env_override():
    config = OpenMinionConfig()
    _csc_install_default_agent(config, provider="openrouter")
    config.runtime.session_context_token_budget = 0
    plugins = PluginRegistry()
    provider = _CaptureProvider()
    log = logging.getLogger("brain-bridge-test")

    captured: dict[str, object] = {}

    class _CaptureRunnerCtor:
        def __init__(self, **kwargs) -> None:
            captured["profile"] = kwargs["profile"]
            self.llm_api = kwargs["llm_api"]
            # bootstrap reads `runner.task_manager` to wire the
            # checkpoint manager into the long-running goal runtime;
            # capture stub doesn't need a real task manager.
            self.task_manager = kwargs.get("task_manager")
            self.session_api = kwargs["session_api"]
            self.profile = kwargs["profile"]

        def run(self, **kwargs):
            del kwargs
            return SimpleNamespace()

        def step(self, **kwargs):
            del kwargs
            return SimpleNamespace()

        contract_version = "v1"

    with (
        patch.dict(
            "os.environ",
            {
                "OPENMINION_STRICT_ADAPTER_CONTRACTS": "0",
                "OPENMINION_BRAIN_MAX_TOTAL_LLM_TOKENS": "4096",
            },
            clear=False,
        ),
        patch(
            "openminion.services.brain.service.create_session_adapter",
            return_value=_DummySessionApi(),
        ),
        patch(
            "openminion.services.brain.service.create_context_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_tool_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_a2a_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_memory_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_policy_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_safety_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_rlm_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.BrainRunner",
            _CaptureRunnerCtor,
        ),
    ):
        service = BrainBridgeService(
            config=config,
            plugins=plugins,
            provider=provider,
            logger=log,
            tools=None,
            security_policy=None,
            self_improvement=None,
            mode="auto",
            db_path="/tmp/brain-tests.db",
        )
        _ = service._get_runner()

    profile = captured["profile"]
    assert int(getattr(profile.budgets, "max_total_llm_tokens")) == 4096


def test_brain_bridge_token_budget_runtime_env_override_used_when_process_env_unset():
    config = OpenMinionConfig()
    _csc_install_default_agent(config, provider="openrouter")
    config.runtime.session_context_token_budget = 0
    config.runtime.env["OPENMINION_BRAIN_MAX_TOTAL_LLM_TOKENS"] = "3500"
    plugins = PluginRegistry()
    provider = _CaptureProvider()
    log = logging.getLogger("brain-bridge-test")

    captured: dict[str, object] = {}

    class _CaptureRunnerCtor:
        def __init__(self, **kwargs) -> None:
            captured["profile"] = kwargs["profile"]
            self.llm_api = kwargs["llm_api"]
            # bootstrap reads `runner.task_manager` to wire the
            # checkpoint manager into the long-running goal runtime;
            # capture stub doesn't need a real task manager.
            self.task_manager = kwargs.get("task_manager")
            self.session_api = kwargs["session_api"]
            self.profile = kwargs["profile"]

        def run(self, **kwargs):
            del kwargs
            return SimpleNamespace()

        def step(self, **kwargs):
            del kwargs
            return SimpleNamespace()

        contract_version = "v1"

    with (
        patch.dict(
            "os.environ",
            {
                "OPENMINION_STRICT_ADAPTER_CONTRACTS": "0",
                "OPENMINION_BRAIN_MAX_TOTAL_LLM_TOKENS": "",
            },
            clear=False,
        ),
        patch(
            "openminion.services.brain.service.create_session_adapter",
            return_value=_DummySessionApi(),
        ),
        patch(
            "openminion.services.brain.service.create_context_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_tool_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_a2a_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_memory_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_policy_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_safety_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_rlm_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.BrainRunner",
            _CaptureRunnerCtor,
        ),
    ):
        service = BrainBridgeService(
            config=config,
            plugins=plugins,
            provider=provider,
            logger=log,
            tools=None,
            security_policy=None,
            self_improvement=None,
            mode="auto",
            db_path="/tmp/brain-tests.db",
        )
        _ = service._get_runner()

    profile = captured["profile"]
    assert int(getattr(profile.budgets, "max_total_llm_tokens")) == 3500


def test_brain_bridge_plan_auto_scale_runtime_env_override_used_when_process_env_unset():
    config = OpenMinionConfig()
    _csc_install_default_agent(config, provider="openrouter")
    config.runtime.env["OPENMINION_PLAN_AUTO_SCALE_MAX_LLM_CALLS"] = "41"
    config.runtime.env["OPENMINION_PLAN_AUTO_SCALE_MAX_TICKS"] = "42"
    config.runtime.env["OPENMINION_PLAN_AUTO_SCALE_MAX_TOKENS"] = "43000"
    plugins = PluginRegistry()
    provider = _CaptureProvider()
    log = logging.getLogger("brain-bridge-test")

    captured: dict[str, object] = {}

    class _CaptureRunnerCtor:
        def __init__(self, **kwargs) -> None:
            captured["options"] = kwargs["options"]
            self.llm_api = kwargs["llm_api"]
            # bootstrap reads `runner.task_manager` to wire the
            # checkpoint manager into the long-running goal runtime;
            # capture stub doesn't need a real task manager.
            self.task_manager = kwargs.get("task_manager")
            self.session_api = kwargs["session_api"]
            self.profile = kwargs["profile"]

        def run(self, **kwargs):
            del kwargs
            return SimpleNamespace()

        def step(self, **kwargs):
            del kwargs
            return SimpleNamespace()

        contract_version = "v1"

    with (
        patch.dict(
            "os.environ",
            {
                "OPENMINION_STRICT_ADAPTER_CONTRACTS": "0",
                "OPENMINION_PLAN_AUTO_SCALE_MAX_LLM_CALLS": "",
                "OPENMINION_PLAN_AUTO_SCALE_MAX_TICKS": "",
                "OPENMINION_PLAN_AUTO_SCALE_MAX_TOKENS": "",
            },
            clear=False,
        ),
        patch(
            "openminion.services.brain.service.create_session_adapter",
            return_value=_DummySessionApi(),
        ),
        patch(
            "openminion.services.brain.service.create_context_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_tool_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_a2a_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_memory_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_policy_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_safety_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.create_rlm_adapter",
            return_value=SimpleNamespace(),
        ),
        patch(
            "openminion.services.brain.service.BrainRunner",
            _CaptureRunnerCtor,
        ),
    ):
        service = BrainBridgeService(
            config=config,
            plugins=plugins,
            provider=provider,
            logger=log,
            tools=None,
            security_policy=None,
            self_improvement=None,
            mode="auto",
            db_path="/tmp/brain-tests.db",
        )
        _ = service._get_runner()

    options = captured["options"]
    assert int(getattr(options, "plan_auto_scale_max_llm_calls")) == 41
    assert int(getattr(options, "plan_auto_scale_max_ticks")) == 42
    assert int(getattr(options, "plan_auto_scale_max_tokens")) == 43_000


def test_brain_integration_mode_default():
    from openminion.base.config import GatewayConfig

    config = GatewayConfig()
    assert config.brain_integration_mode == "contextctl_authoritative"


def test_brain_integration_mode_legacy_compat_rejected():
    from openminion.base.config import GatewayConfig
    from openminion.base.config import ConfigError

    try:
        GatewayConfig(brain_integration_mode="legacy_compat")
    except ConfigError:
        return
    assert False, "legacy_compat should be rejected"


def test_brain_integration_mode_skips_compaction():
    from openminion.base.config import GatewayConfig

    config = GatewayConfig(brain_integration_mode="ctxctl_authoritative")
    assert config.brain_integration_mode == "contextctl_authoritative"


def test_brain_hydrates_gateway_history_into_runner_session():
    step_out = _make_step_out(ok=True)
    provider = _FakeProvider(follow_text="final", follow_model="follow-model")
    service = _build_service(step_out, provider)
    capture_api = _CaptureSessionApi()
    service._runner = _DummyRunner(step_out=step_out)
    service._runner.session_api = capture_api

    history = [
        Message(
            channel="console",
            target="me",
            body="Agent canonical memory block",
            metadata={"role": "system", "session_id": "s-hydrate"},
        ),
        Message(
            channel="console",
            target="me",
            body="user prior question",
            metadata={"role": "user", "session_id": "s-hydrate"},
        ),
        Message(
            channel="console",
            target="me",
            body="assistant prior response",
            metadata={"role": "assistant", "session_id": "s-hydrate"},
        ),
    ]

    asyncio.run(
        service.run_turn(
            Message(
                channel="console",
                target="me",
                body="current prompt",
                metadata={"session_id": "s-hydrate"},
            ),
            history=history,
        )
    )

    hydrated = capture_api.list_turns("s-hydrate")
    hydrated_pairs = {(item["role"], item["content"]) for item in hydrated}

    assert (
        "system",
        "You are OpenMinion, a pragmatic assistant.",
    ) not in hydrated_pairs
    assert ("system", "Agent canonical memory block") not in hydrated_pairs
    assert ("user", "user prior question") in hydrated_pairs
    assert ("assistant", "assistant prior response") in hydrated_pairs


def test_brain_hydration_dedupes_prefixed_assistant_content():
    step_out = _make_step_out(ok=True)
    provider = _FakeProvider(follow_text="final", follow_model="follow-model")
    service = _build_service(step_out, provider)
    capture_api = _CaptureSessionApi()
    service._runner = _DummyRunner(step_out=step_out)
    service._runner.session_api = capture_api

    # Existing runner-native assistant turn (unprefixed)
    capture_api.append_turn(
        "s-dedupe", "assistant", "Could you clarify what you'd like me to decide?"
    )

    history = [
        Message(
            channel="console",
            target="me",
            body=f"{service._config.agents[next(iter(service._config.agents.keys()))].name}: Could you clarify what you'd like me to decide?",
            metadata={"role": "assistant", "session_id": "s-dedupe"},
        ),
    ]

    service._hydrate_runner_session_context(
        runner=service._runner,
        session_id="s-dedupe",
        history=history,
        system_prompt="You are OpenMinion, a pragmatic assistant.",
    )

    turns = capture_api.list_turns("s-dedupe")
    assistant_turns = [item for item in turns if item.get("role") == "assistant"]
    assert len(assistant_turns) == 1


def test_reset_state_for_new_input_supersedes_active_plan():
    step_out = _make_step_out(ok=True)
    provider = _FakeProvider(follow_text="final", follow_model="follow-model")
    service = _build_service(step_out, provider)
    capture_api = _CaptureSessionApi()
    service._runner = _DummyRunner(step_out=step_out)
    service._runner.session_api = capture_api

    capture_api.put_working_state(
        "s-reset-active",
        state_inline={
            "status": "active",
            "phase": "RESPOND",
            "goal": "old query",
            "plan": {"objective": "old", "steps": [{"kind": "tool"}]},
            "cursor": 1,
            "open_questions": ["old question?"],
            "pending_jobs": [{"task_id": "job-1"}],
            "retries_for_step": {"step-1": 1},
        },
    )

    service._reset_state_for_new_input(
        runner=service._runner,
        session_id="s-reset-active",
        user_input="list all tools",
    )
    updated = capture_api.get_latest_working_state("s-reset-active")

    assert updated["status"] == "active"
    assert updated.get("phase") is None
    assert updated.get("plan") is None
    assert updated.get("cursor") == 0
    assert updated.get("goal") == "list all tools"
    assert updated.get("open_questions") == []
    assert updated.get("pending_jobs") == []


def test_reset_state_for_resume_preserves_existing_plan():
    step_out = _make_step_out(ok=True)
    provider = _FakeProvider(follow_text="final", follow_model="follow-model")
    service = _build_service(step_out, provider)
    capture_api = _CaptureSessionApi()
    service._runner = _DummyRunner(step_out=step_out)
    service._runner.session_api = capture_api

    capture_api.put_working_state(
        "s-reset-resume",
        state_inline={
            "status": "active",
            "goal": "old query",
            "plan": {"objective": "old", "steps": [{"kind": "tool"}]},
            "cursor": 1,
        },
    )

    service._reset_state_for_new_input(
        runner=service._runner,
        session_id="s-reset-resume",
        user_input="resume",
    )
    updated = capture_api.get_latest_working_state("s-reset-resume")

    assert updated["status"] == "active"
    assert updated.get("plan") == {"objective": "old", "steps": [{"kind": "tool"}]}
    assert updated.get("cursor") == 1
    assert updated.get("goal") == "old query"


def test_brain_long_session_compaction_continuity():
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "sessions.db"

        config = OpenMinionConfig()
        _csc_install_default_agent(
            config, name="test-agent", system_prompt="You are a helpful assistant."
        )
        plugins = PluginRegistry()
        log = logging.getLogger("brain-bridge-test")

        with (
            patch(
                "openminion.services.brain.service.create_session_adapter"
            ) as mock_sess,
            patch(
                "openminion.services.brain.service.create_context_adapter"
            ) as mock_ctx,
            patch("openminion.services.brain.service.create_tool_adapter") as mock_tool,
            patch("openminion.services.brain.service.create_a2a_adapter") as mock_a2a,
            patch(
                "openminion.services.brain.service.create_memory_adapter"
            ) as mock_mem,
            patch(
                "openminion.services.brain.service.create_policy_adapter"
            ) as mock_pol,
            patch(
                "openminion.services.brain.service.create_safety_adapter"
            ) as mock_safe,
            patch("openminion.services.brain.service.create_rlm_adapter") as mock_rlm,
            patch(
                "openminion.services.brain.service.create_compress_adapter"
            ) as mock_comp,
        ):
            mock_sess.return_value = _DummySessionApi()
            mock_ctx.return_value = SimpleNamespace()
            mock_tool.return_value = SimpleNamespace()
            mock_a2a.return_value = SimpleNamespace()
            mock_mem.return_value = SimpleNamespace()
            mock_pol.return_value = SimpleNamespace()
            mock_safe.return_value = SimpleNamespace()
            mock_rlm.return_value = SimpleNamespace()
            mock_comp.return_value = SimpleNamespace()

            service = BrainBridgeService(
                config=config,
                plugins=plugins,
                provider=_FakeProvider(),
                logger=log,
                tools=None,
                security_policy=None,
                self_improvement=None,
                mode="auto",
                db_path=str(db_path),
                workspace_root=tmpdir,
            )

        session_id = "long-session-test"

        for i in range(25):
            msg = Message(
                channel="console",
                target="me",
                body=f"User message number {i}",
                metadata={"session_id": session_id},
            )
            asyncio.run(service.run_turn(msg, history=[]))

        session_api = service._runner.session_api
        turns_after_many = session_api.list_turns(session_id)
        assert len(turns_after_many) >= 20, (
            f"Expected at least 20 turns, got {len(turns_after_many)}"
        )

        service2 = BrainBridgeService(
            config=config,
            plugins=plugins,
            provider=_FakeProvider(),
            logger=log,
            tools=None,
            security_policy=None,
            self_improvement=None,
            mode="auto",
            db_path=str(db_path),
            workspace_root=tmpdir,
        )

        msg = Message(
            channel="console",
            target="me",
            body="User message after restart",
            metadata={"session_id": session_id},
        )
        asyncio.run(service2.run_turn(msg, history=[]))

        session_api2 = service2._runner.session_api
        turns_after_restart = session_api2.list_turns(session_id)
        assert len(turns_after_restart) > len(turns_after_many), (
            "Session should continue after restart"
        )


def test_tool_registry_parity_no_hallucination():
    from openminion.modules.tool import build_default_tool_registry
    from openminion.tools.config import resolve_tool_env

    runtime_registry = build_default_tool_registry()
    tools_dict = getattr(runtime_registry, "_tools", {}) or getattr(
        runtime_registry, "tools", {}
    )
    runtime_tool_names = {tool.name for tool in tools_dict.values()}

    hallucinated_tools = {
        "calculator",
        "text-to-speech",
        "web_browser",
        "code_interpreter",
    }

    overlap = runtime_tool_names & hallucinated_tools
    assert not overlap, f"Runtime registry contains hallucinated tool names: {overlap}"

    expected_any = [
        {"weather", "weather.openmeteo.current"},
        {"fetch.get", "fetch.head"},
        {"file.list_dir"},
        {"exec.run", "run_command"},
    ]
    for candidates in expected_any:
        if not (runtime_tool_names & candidates):
            raise AssertionError(
                f"Runtime registry missing expected tool(s): {sorted(candidates)}"
            )

    search_candidates = {"search.dispatch", "search.tavily.search"}
    env = resolve_tool_env()
    search_configured = any(
        env.get(name, "").strip() for name in ("TAVILY_API_KEY", "BRAVE_API_KEY")
    )
    if search_configured and not (runtime_tool_names & search_candidates):
        raise AssertionError(
            f"Runtime registry missing expected search tool(s): {sorted(search_candidates)}"
        )


def test_task_schedule_full_turn_response_includes_task_id():
    task_id = "abc12345-6789-0000-0000-000000000001"
    step_out = _make_step_out(
        ok=True,
        tool_name="task.schedule",
        summary=f"Scheduled task '{task_id}' every 1 min. Runs while daemon is active.",
        message="",
    )
    provider = _FakeProvider(follow_text="ignored", follow_model="follow-model")
    service = _build_service(step_out, provider)

    response = asyncio.run(
        service.run_turn(
            Message(
                channel="console",
                target="me",
                body="generate a joke every minute",
                metadata={
                    "session_id": "s-task-schedule",
                    "request_id": "trace-task-schedule",
                },
            )
        )
    )

    assert task_id in response.text, (
        f"Expected task_id '{task_id}' in response text, got: {response.text!r}"
    )
    assert response.metadata["tool_loop_termination_reason"] == "tool_final"


def test_brain_bridge_consumes_canonical_bootstrap_handles() -> None:
    config = OpenMinionConfig()
    _csc_install_default_agent(config, provider="echo")
    plugins = PluginRegistry()

    sentinel_retrieve = object()
    sentinel_action_policy = object()

    service = BrainBridgeService(
        config=config,
        plugins=plugins,
        provider=_FakeProvider(follow_text="x", follow_model="m"),
        logger=logging.getLogger("brain-bridge-parity-test"),
        tools=None,
        security_policy=None,
        self_improvement=None,
        mode="auto",
        db_path="/tmp/brain-bridge-parity-tests.db",
        retrieve_service=sentinel_retrieve,
        action_policy_service=sentinel_action_policy,
    )

    assert service._retrieve_service is sentinel_retrieve, (
        "Bridge must store retrieve_service from canonical bootstrap; "
        "re-derivation would drift from non-bridge lanes."
    )
    assert service._action_policy_service is sentinel_action_policy, (
        "Bridge must store action_policy_service from canonical bootstrap."
    )
    assert service._config is config


def test_brain_bridge_source_has_no_runner_monkey_patches() -> None:
    from pathlib import Path
    import openminion.services.brain.service as bridge_module

    source_path = Path(bridge_module.__file__)
    source = source_path.read_text(encoding="utf-8")

    assert "MethodType" not in source, (
        "BrainBridgeService must not use types.MethodType to override "
        "runner methods; express phase enablement via RunnerOptions."
    )
    assert "monkey" not in source.lower(), (
        "BrainBridgeService source mentions 'monkey'; runner contract "
        "changes must go through explicit capability surfaces."
    )

    forbidden_phase_assignments = [
        "_reflect =",
        "._reflect =",
        "._decide =",
        "._plan =",
    ]
    for phrase in forbidden_phase_assignments:
        if phrase == "_reflect =":
            for line in source.splitlines():
                stripped = line.strip()
                if stripped.startswith("_reflect =") or stripped.startswith(
                    "self._reflect ="
                ):
                    raise AssertionError(
                        f"Forbidden runner phase override in bridge: {stripped!r}"
                    )
            continue
        assert phrase not in source, (
            f"Forbidden runner phase override pattern in bridge: {phrase!r}"
        )


def test_brain_bridge_home_paths_match_config_manager() -> None:
    from openminion.base.config import ConfigManager

    with tempfile.TemporaryDirectory() as tmp:
        home_root = Path(tmp).resolve()
        data_root = home_root / "data"
        config_path = home_root / "config.json"
        config_path.write_text("{}\n", encoding="utf-8")

        manager = ConfigManager.load(
            config_path=str(config_path),
            home_root=home_root,
            data_root=data_root,
        )
        config = manager.base_config
        if not config.agents:
            _csc_install_default_agent(config, provider="echo")
        plugins = PluginRegistry()
        log = logging.getLogger("brain-bridge-bbse05")

        with (
            patch(
                "openminion.services.brain.service.create_session_adapter",
                return_value=_DummySessionApi(),
            ),
            patch(
                "openminion.services.brain.service.create_context_adapter",
                return_value=SimpleNamespace(),
            ),
            patch(
                "openminion.services.brain.service.create_tool_adapter",
                return_value=SimpleNamespace(),
            ),
            patch(
                "openminion.services.brain.service.create_a2a_adapter",
                return_value=SimpleNamespace(),
            ),
            patch(
                "openminion.services.brain.service.create_memory_adapter",
                return_value=SimpleNamespace(),
            ),
            patch(
                "openminion.services.brain.service.create_policy_adapter",
                return_value=SimpleNamespace(),
            ),
            patch(
                "openminion.services.brain.service.create_safety_adapter",
                return_value=SimpleNamespace(),
            ),
            patch(
                "openminion.services.brain.service.create_rlm_adapter",
                return_value=SimpleNamespace(),
            ),
        ):
            service = BrainBridgeService(
                config=config,
                plugins=plugins,
                provider=_FakeProvider(follow_text="x", follow_model="m"),
                logger=log,
                tools=None,
                security_policy=None,
                self_improvement=None,
                mode="auto",
                db_path=str(home_root / "sessions.db"),
                config_manager=manager,
            )

        assert service._home_paths.home_root == manager.home_root, (
            "Bridge home_root must equal ConfigManager.home_root when "
            "manager is provided; path-ownership drift detected."
        )
        assert service._home_paths.data_root == manager.data_root, (
            "Bridge data_root must equal ConfigManager.data_root when "
            "manager is provided."
        )
        assert Path(service.workspace_root) == manager.home_root


def test_brain_bridge_runtime_metadata_parity_with_canonical_bootstrap() -> None:
    from openminion.base.config import ConfigManager

    with tempfile.TemporaryDirectory() as tmp:
        home_root = Path(tmp).resolve()
        config_path = home_root / "config.json"
        config_path.write_text("{}\n", encoding="utf-8")
        manager = ConfigManager.load(
            config_path=str(config_path),
            home_root=home_root,
        )
        config = manager.base_config
        if not config.agents:
            _csc_install_default_agent(config, provider="echo")
        plugins = PluginRegistry()

        sentinel_retrieve = object()
        sentinel_action_policy = object()

        with (
            patch(
                "openminion.services.brain.service.create_session_adapter",
                return_value=_DummySessionApi(),
            ),
            patch(
                "openminion.services.brain.service.create_context_adapter",
                return_value=SimpleNamespace(),
            ),
            patch(
                "openminion.services.brain.service.create_tool_adapter",
                return_value=SimpleNamespace(),
            ),
            patch(
                "openminion.services.brain.service.create_a2a_adapter",
                return_value=SimpleNamespace(),
            ),
            patch(
                "openminion.services.brain.service.create_memory_adapter",
                return_value=SimpleNamespace(),
            ),
            patch(
                "openminion.services.brain.service.create_policy_adapter",
                return_value=SimpleNamespace(),
            ),
            patch(
                "openminion.services.brain.service.create_safety_adapter",
                return_value=SimpleNamespace(),
            ),
            patch(
                "openminion.services.brain.service.create_rlm_adapter",
                return_value=SimpleNamespace(),
            ),
        ):
            service = BrainBridgeService(
                config=config,
                plugins=plugins,
                provider=_FakeProvider(follow_text="x", follow_model="m"),
                logger=logging.getLogger("brain-bridge-bbse06"),
                tools=None,
                security_policy=None,
                self_improvement=None,
                mode="auto",
                db_path=str(home_root / "sessions.db"),
                config_manager=manager,
                retrieve_service=sentinel_retrieve,
                action_policy_service=sentinel_action_policy,
            )

        assert service._config is config
        assert service._config_manager is manager
        assert service._retrieve_service is sentinel_retrieve
        assert service._action_policy_service is sentinel_action_policy

        assert service._context.config_manager is manager

        assert service._env is manager.env, (
            "Bridge env must reuse ConfigManager.env when manager is "
            "provided; re-deriving via resolve_services_env would shadow "
            "the canonical env source."
        )

        debug = service._home_paths.to_debug_dict()
        assert debug["home_root"] == str(manager.home_root)
        assert debug["data_root"] == str(manager.data_root)
        assert "path_mode" in debug and "path_source" in debug


def test_brain_bridge_diagnostics_exposes_canonical_parts_posture() -> None:
    config = OpenMinionConfig()
    _csc_install_default_agent(config, provider="echo")
    plugins = PluginRegistry()
    sentinel_retrieve = object()
    sentinel_action_policy = object()

    service = BrainBridgeService(
        config=config,
        plugins=plugins,
        provider=_FakeProvider(follow_text="x", follow_model="m"),
        logger=logging.getLogger("brain-bridge-bbse07"),
        tools=None,
        security_policy=None,
        self_improvement=None,
        mode="auto",
        db_path="/tmp/brain-bridge-bbse07.db",
        retrieve_service=sentinel_retrieve,
        action_policy_service=sentinel_action_policy,
    )

    diagnostics = service.bridge_diagnostics()
    assert isinstance(diagnostics, dict)
    assert diagnostics["config_manager_present"] is False
    assert diagnostics["retrieve_service_present"] is True
    assert diagnostics["action_policy_service_present"] is True
    home_paths_payload = diagnostics["home_paths"]
    assert "home_root" in home_paths_payload
    assert "data_root" in home_paths_payload
    assert "path_mode" in home_paths_payload
    assert diagnostics["runner_method_overrides_present"] is False
    assert diagnostics["runner_assembled"] is False
