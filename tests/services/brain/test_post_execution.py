import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from openminion.base.config import OpenMinionConfig, build_runtime_config
from openminion.base.types import AgentResponse
from openminion.base.types import Message
from openminion.modules.memory.models import MemoryCandidate
from openminion.modules.memory.storage.memory import InMemoryMemoryStore
from openminion.modules.brain.loop.context.pending_turn import (
    PENDING_TURN_CONTEXT_MAX_STALE_TURNS,
)
from openminion.modules.llm.providers.base import ProviderResponse
from openminion.services.agent.constants import PRIOR_TURN_CONTEXT_CHAR_LIMIT
from openminion.services.brain.post_execution import BrainBridgeTurnMixin
from openminion.services.brain.post_execution.postprocess import (
    _tool_result_response_text,
)
from tests._csc_fixtures import _csc_install_default_agent


class DummyBridge(BrainBridgeTurnMixin):
    pass


class _DummySessionAPI:
    def __init__(self, state: dict) -> None:
        self._state = dict(state)
        self.written: dict | None = None
        self.events: list[dict] = []
        self.turns: list[dict[str, str]] = []

    def get_latest_working_state(self, session_id: str) -> dict:
        return dict(self._state)

    def put_working_state(self, session_id: str, *, state_inline: dict) -> None:
        self.written = dict(state_inline)

    def append_event(
        self,
        session_id: str,
        type: str,
        payload: dict,
        *,
        trace_id=None,
        **_kwargs,
    ) -> str:
        self.events.append(
            {
                "session_id": session_id,
                "type": type,
                "payload": dict(payload),
                "trace_id": trace_id,
            }
        )
        return f"{session_id}-event-{len(self.events)}"

    def list_events(self, session_id: str) -> list[dict]:
        return [item for item in self.events if item.get("session_id") == session_id]

    def append_turn(
        self,
        *,
        session_id: str,
        role: str,
        content: str,
        attachments=None,
        meta=None,
    ) -> str:
        del attachments, meta
        self.turns.append(
            {
                "session_id": session_id,
                "role": role,
                "content": content,
            }
        )
        return f"{session_id}-turn-{len(self.turns)}"

    def list_turns(self, session_id: str) -> list[dict[str, str]]:
        return [
            dict(item) for item in self.turns if item.get("session_id") == session_id
        ]


class _DummyRunner:
    def __init__(self, state: dict) -> None:
        self.session_api = _DummySessionAPI(state)
        self.profile = SimpleNamespace(
            budgets=SimpleNamespace(
                max_ticks_per_user_turn=8,
                max_tool_calls=8,
                max_a2a_calls=0,
                max_total_llm_tokens=100000,
                max_elapsed_ms=45000,
            )
        )


class _DummyIdentityClient:
    def __init__(self, system_prompt: str = "") -> None:
        self._system_prompt = system_prompt


class _DummyContextAdapter:
    def __init__(self) -> None:
        self.service = SimpleNamespace(_identityctl=_DummyIdentityClient("base"))


class _DummyMemoryApi:
    def __init__(self, context: str, meta: dict[str, str] | None = None) -> None:
        self.context = context
        self.meta = dict(meta or {})

    def build_context_with_metadata(
        self, *, session_id: str, user_message: str
    ) -> tuple[str, dict[str, str]]:
        assert session_id
        assert user_message
        return self.context, {
            "memory_envelope_lane": "capsule",
            **self.meta,
        }


class _DummyTelemetry:
    def __init__(self) -> None:
        self.events: list[tuple] = []

    async def emit_tick(
        self, session_id: str, turn_id: str, elapsed_ms: float, mode: str | None = None
    ) -> None:
        self.events.append(("tick", session_id, turn_id, mode))

    async def emit_tool_call(
        self,
        session_id: str,
        turn_id: str,
        tool_name: str,
        success: bool,
        mode: str | None = None,
    ) -> None:
        self.events.append(("tool_call", session_id, turn_id, tool_name, success, mode))


def _runtime_grounding_keys(prompt: str) -> set[str]:
    lines = str(prompt).splitlines()
    in_block = False
    keys: set[str] = set()
    for raw in lines:
        line = raw.strip()
        if line == "## Runtime Grounding":
            in_block = True
            continue
        if not in_block:
            continue
        if not line:
            break
        if line == "facts:":
            continue
        if not line.startswith("- "):
            continue
        key, _, _ = line[2:].partition(":")
        if key:
            keys.add(key.strip())
    return keys


def test_build_clarify_request_payload() -> None:
    step_out = SimpleNamespace(
        status="waiting_user",
        message="fallback",
        working_state=SimpleNamespace(
            unresolved_clarify_items=[
                {
                    "id": "q1",
                    "question": "Clarify?",
                    "reason_code": "weather_location_required",
                    "source": "clarify",
                }
            ],
            trace_id="trace-1",
        ),
    )
    payload = DummyBridge()._build_clarify_request_payload(
        step_out=step_out,
        session_id="session-1",
        trace_id=None,
    )

    assert payload is not None
    assert payload["session_id"] == "session-1"
    assert payload["questions"][0]["question"] == "Clarify?"
    assert payload["questions"][0]["reason_code"] == "weather_location_required"


def test_build_clarify_request_payload_ignores_generic_waiting_messages() -> None:
    step_out = SimpleNamespace(
        status="waiting_user",
        message="Hi there! How can I help you today?",
        working_state=SimpleNamespace(
            unresolved_clarify_items=[],
            trace_id="trace-2",
        ),
    )

    payload = DummyBridge()._build_clarify_request_payload(
        step_out=step_out,
        session_id="session-2",
        trace_id=None,
    )

    assert payload is None


def test_resolve_turn_session_ids_defaults_to_runtime_session() -> None:
    bridge = DummyBridge()
    runtime_session_id, brain_session_id = bridge._resolve_turn_session_ids(
        message=SimpleNamespace(metadata={"session_id": "s1"})
    )
    assert runtime_session_id == "s1"
    assert brain_session_id == "s1"


def test_run_turn_accepts_and_forwards_approval_callback() -> None:
    bridge = DummyBridge()
    bridge._logger = SimpleNamespace(info=lambda *args, **kwargs: None)  # type: ignore[attr-defined]
    message = Message(channel="console", target="focus", body="hello")
    runner = SimpleNamespace()
    approval_callback = object()
    captured: dict[str, object] = {}

    async def _prepare_turn(**_kwargs):
        return runner, "brain-session-1", "req-1", "turn-1", 0.0

    def _execute_turn(**kwargs):
        captured["approval_callback"] = kwargs.get("approval_callback")
        return SimpleNamespace(message="done")

    async def _postprocess_turn(**_kwargs):
        return AgentResponse(text="done", channel="console", target="focus")

    bridge._prepare_turn = _prepare_turn  # type: ignore[attr-defined]
    bridge._execute_turn = _execute_turn  # type: ignore[attr-defined]
    bridge._postprocess_turn = _postprocess_turn  # type: ignore[attr-defined]

    response = asyncio.run(
        bridge.run_turn(
            message,
            approval_callback=approval_callback,
        )
    )

    assert captured["approval_callback"] is approval_callback
    assert response.text == "done"


def test_inject_resume_task_hints_attaches_memory_consolidation_module_state() -> None:
    bridge = DummyBridge()
    runner = _DummyRunner({"module_state": {}})
    store = InMemoryMemoryStore()
    store.candidate_put(
        MemoryCandidate(
            candidate_id="cand-1",
            session_id="sess-1",
            proposed_scope="agent:agent-1",
            type="fact",
            title="Deploy region",
            content="Preferred deploy region is us-west-2.",
            confidence=0.8,
        )
    )
    runner.memory_api = SimpleNamespace(store=store)

    bridge._inject_resume_task_hints(
        runner=runner,
        session_id="sess-1",
        inbound_metadata={
            "cron_job_id": "job-1",
            "memory_consolidation_job": "true",
            "memory_consolidation_target_scope": "agent:agent-1",
            "memory_consolidation_batch_limit": "5",
            "memory_consolidation_max_iterations": "2",
            "memory_consolidation_timeout_seconds": "30",
        },
    )

    written = runner.session_api.written
    assert written is not None
    payload = written["module_state"]["memory_consolidation"]
    assert payload["enabled"] is True
    assert payload["target_scope"] == "agent:agent-1"
    assert payload["batch_limit"] == 5
    assert payload["candidates"][0]["candidate_id"] == "cand-1"


def test_collect_system_history_context_deduplicates_and_orders() -> None:
    bridge = DummyBridge()
    history = [
        Message(
            channel="console",
            target="user",
            body="## Agent Memory\n• one",
            metadata={"role": "system"},
        ),
        Message(
            channel="console",
            target="user",
            body="## Agent Memory\n• one",
            metadata={"role": "system"},
        ),
        Message(
            channel="console",
            target="user",
            body="hello",
            metadata={"role": "user"},
        ),
        Message(
            channel="console",
            target="user",
            body="## Memory (dynamic retrieval)\n• two",
            metadata={"role": "system"},
        ),
    ]

    merged = bridge._collect_system_history_context(history=history)  # noqa: SLF001
    assert merged == "## Agent Memory\n• one\n\n## Memory (dynamic retrieval)\n• two"


def test_build_turn_response_metadata_includes_turn_progress_summary() -> None:
    bridge = DummyBridge()
    bridge._config = SimpleNamespace(
        agent=SimpleNamespace(name="agent-1"),
        agents={"agent-1": SimpleNamespace(name="agent-1")},
        default_agent="agent-1",
    )
    bridge._provider = SimpleNamespace(name="fake-provider")
    runner = _DummyRunner({})

    metadata = bridge._build_turn_response_metadata(
        runner=runner,
        step_out=SimpleNamespace(
            status="done",
            action_result=SimpleNamespace(
                outputs={
                    "total_input_tokens_used": 700,
                    "total_output_tokens_used": 800,
                    "total_tokens_used": 1500,
                    "tool_calls_count": 2,
                }
            ),
        ),
        session_id="sess-1",
        request_id="trace-1",
        elapsed_ms=1234.5,
        llm_steps=2,
        termination_reason="model_final",
    )

    assert metadata["turn_duration_ms"] == "1234"
    assert metadata["total_input_tokens_used"] == "700"
    assert metadata["total_output_tokens_used"] == "800"
    assert metadata["total_tokens_used"] == "1500"
    assert metadata["tool_calls_count"] == "2"


def test_build_turn_response_metadata_uses_selected_runtime_agent_identity() -> None:
    bridge = DummyBridge()
    bridge._config = OpenMinionConfig.from_dict(
        {
            "default_agent": "minimax-m2-7",
            "agents": {
                "minimax-m2-7": {"name": "minimax-m2-7", "provider": "openai"},
                "minimax-m2-5": {"name": "minimax-m2-5", "provider": "openai"},
            },
        }
    )
    bridge._config = build_runtime_config(bridge._config, agent_id="minimax-m2-5")  # type: ignore[attr-defined]
    bridge._provider = SimpleNamespace(name="fake-provider")
    runner = _DummyRunner({})

    metadata = bridge._build_turn_response_metadata(
        runner=runner,
        step_out=SimpleNamespace(
            status="done",
            action_result=SimpleNamespace(outputs={}),
        ),
        session_id="sess-selected",
        request_id="trace-selected",
        elapsed_ms=200.0,
        llm_steps=1,
        termination_reason="model_final",
    )

    assert metadata["agent"] == "minimax-m2-5"


def test_prepare_turn_applies_runtime_system_prompt_override_with_gateway_system_context() -> (
    None
):
    bridge = DummyBridge()
    runner = SimpleNamespace(
        context_api=_DummyContextAdapter(),
        session_api=SimpleNamespace(),
    )

    bridge._runtime_system_prompt = lambda *, user_message: "BASE SYSTEM"  # type: ignore[method-assign]
    bridge._get_runner = lambda: runner  # type: ignore[attr-defined]
    bridge._reset_state_for_new_input = lambda **_: None  # type: ignore[attr-defined,method-assign]
    captured: dict[str, str] = {}
    gateway_captured: dict[str, str] = {}

    def _capture_hydrate(*, runner, session_id, history, system_prompt):  # noqa: ANN001
        captured["system_prompt"] = str(system_prompt)

    def _capture_gateway(*, runner, session_id, gateway_system_context):  # noqa: ANN001
        gateway_captured["context"] = str(gateway_system_context)

    bridge._hydrate_runner_session_context = _capture_hydrate  # type: ignore[method-assign]
    bridge._inject_gateway_system_context = _capture_gateway  # type: ignore[method-assign]
    bridge._security_policy = None
    bridge._telemetryctl = None
    bridge._llm_wrapper = None

    history = [
        Message(
            channel="console",
            target="user",
            body="## Agent Memory\n• remembers Alice preference",
            metadata={"role": "system"},
        )
    ]
    message = Message(
        channel="console",
        target="user",
        body="what do you know about alice?",
        metadata={"request_id": "req-1"},
    )

    _runner, session_id, _request_id, _turn_id, _start_time = asyncio.run(
        bridge._prepare_turn(
            message=message,
            history=history,
            forced_tools=None,
            capability_category=None,
            brain_session_id="brain-session-1",
        )
    )

    assert session_id == "brain-session-1"
    # Gateway memory must NOT be in the identity system prompt
    assert "Agent Memory" not in captured["system_prompt"]
    assert captured["system_prompt"].startswith("BASE SYSTEM")
    assert "## Runtime Grounding" in captured["system_prompt"]
    assert "- current_session_history_available: true" in captured["system_prompt"]
    assert "- prior_session_history_available: false" in captured["system_prompt"]
    assert "- session_working_state_available: true" in captured["system_prompt"]
    assert _runtime_grounding_keys(captured["system_prompt"]) == {
        "cwd",
        "workspace_root",
        "current_session_history_available",
        "prior_session_history_available",
        "prior_context_present",
        "prior_turn_present",
        "session_working_state_available",
    }
    assert "prior_context_policy:" not in captured["system_prompt"]
    # Gateway memory must be threaded through the per-turn injection path
    assert (
        gateway_captured["context"] == "## Agent Memory\n• remembers Alice preference"
    )


def test_prepare_turn_injects_runtime_memory_context_for_in_process_bridge() -> None:
    bridge = DummyBridge()
    runner = SimpleNamespace(
        context_api=_DummyContextAdapter(),
        session_api=SimpleNamespace(),
        memory_api=_DummyMemoryApi(
            "Agent canonical memory (cross-session):\n\n"
            "Relevant facts:\n"
            "- User email is scoped-agent@example.com."
        ),
    )

    bridge._runtime_system_prompt = lambda *, user_message: "BASE SYSTEM"  # type: ignore[method-assign]
    bridge._get_runner = lambda: runner  # type: ignore[attr-defined]
    bridge._reset_state_for_new_input = lambda **_: None  # type: ignore[attr-defined,method-assign]
    captured: dict[str, str] = {}
    gateway_captured: dict[str, str] = {}

    def _capture_hydrate(*, runner, session_id, history, system_prompt):  # noqa: ANN001
        captured["system_prompt"] = str(system_prompt)

    def _capture_gateway(*, runner, session_id, gateway_system_context):  # noqa: ANN001
        gateway_captured["context"] = str(gateway_system_context)

    bridge._hydrate_runner_session_context = _capture_hydrate  # type: ignore[method-assign]
    bridge._inject_gateway_system_context = _capture_gateway  # type: ignore[method-assign]
    bridge._security_policy = None
    bridge._telemetryctl = None
    bridge._llm_wrapper = None

    message = Message(
        channel="console",
        target="user",
        body="what is my email?",
        metadata={"request_id": "req-memory"},
    )

    asyncio.run(
        bridge._prepare_turn(
            message=message,
            history=[],
            forced_tools=None,
            capability_category=None,
            brain_session_id="brain-session-memory",
        )
    )

    assert "scoped-agent@example.com" not in captured["system_prompt"]
    assert "## Runtime Grounding" in captured["system_prompt"]
    assert "- prior_context_present: false" in captured["system_prompt"]
    assert "- prior_turn_present: false" in captured["system_prompt"]
    assert "prior_context_policy:" not in captured["system_prompt"]
    assert "scoped-agent@example.com" in gateway_captured["context"]
    assert "scoped-agent@example.com" in runner._pending_gateway_system_context


def test_prepare_turn_adds_descriptive_prior_context_grounding_when_flagged() -> None:
    bridge = DummyBridge()
    runner = SimpleNamespace(
        context_api=_DummyContextAdapter(),
        session_api=SimpleNamespace(),
        memory_api=_DummyMemoryApi(
            "## Continuing from recent sessions\n\nMost relevant prior session:\n"
            "  Topic: China itinerary\n",
            meta={"prior_context_present": "true"},
        ),
    )

    bridge._runtime_system_prompt = lambda *, user_message: "BASE SYSTEM"  # type: ignore[method-assign]
    bridge._get_runner = lambda: runner  # type: ignore[attr-defined]
    bridge._reset_state_for_new_input = lambda **_: None  # type: ignore[attr-defined,method-assign]
    captured: dict[str, str] = {}

    def _capture_hydrate(*, runner, session_id, history, system_prompt):  # noqa: ANN001
        captured["system_prompt"] = str(system_prompt)

    bridge._hydrate_runner_session_context = _capture_hydrate  # type: ignore[method-assign]
    bridge._inject_gateway_system_context = lambda **_: None  # type: ignore[method-assign]
    bridge._security_policy = None
    bridge._telemetryctl = None
    bridge._llm_wrapper = None

    asyncio.run(
        bridge._prepare_turn(
            message=Message(
                channel="console",
                target="user",
                body="Can you detail more for each day?",
                metadata={"request_id": "req-prior"},
            ),
            history=[],
            forced_tools=None,
            capability_category=None,
            brain_session_id="brain-session-prior",
        )
    )

    assert "- prior_context_present: true" in captured["system_prompt"]
    assert "- prior_turn_present: false" in captured["system_prompt"]
    assert (
        "prior_context_block_present: A recalled memory summary block is "
        "present in your current grounding context from the OpenMinion "
        "runtime."
    ) in captured["system_prompt"]


def test_prepare_turn_injects_identity_system_prompt_when_available() -> None:
    bridge = DummyBridge()
    runner = SimpleNamespace(
        context_api=_DummyContextAdapter(),
        session_api=SimpleNamespace(),
    )

    bridge._runtime_system_prompt = lambda *, user_message: "BASE SYSTEM"
    bridge._get_runner = lambda: runner
    bridge._reset_state_for_new_input = lambda **_: None
    bridge._inject_identity_system_prompt = lambda *, system_prompt, inbound_metadata: (
        f"{system_prompt}\n\nIDENTITY PURPOSE={inbound_metadata.get('purpose', '')}"
    )
    captured: dict[str, str] = {}

    def _capture_hydrate(*, runner, session_id, history, system_prompt):  # noqa: ANN001
        captured["system_prompt"] = str(system_prompt)

    bridge._hydrate_runner_session_context = _capture_hydrate  # type: ignore[method-assign]
    bridge._security_policy = None
    bridge._telemetryctl = None
    bridge._llm_wrapper = None

    message = Message(
        channel="console",
        target="user",
        body="make a plan",
        metadata={"request_id": "req-2", "purpose": "plan"},
    )

    _runner, session_id, _request_id, _turn_id, _start_time = asyncio.run(
        bridge._prepare_turn(
            message=message,
            history=[],
            forced_tools=None,
            capability_category=None,
            brain_session_id="brain-session-2",
        )
    )

    assert session_id == "brain-session-2"
    assert captured["system_prompt"].startswith("BASE SYSTEM\n\nIDENTITY PURPOSE=plan")
    assert "## Runtime Grounding" in captured["system_prompt"]
    assert "- current_session_history_available: true" in captured["system_prompt"]
    assert "- prior_session_history_available: false" in captured["system_prompt"]
    assert "- session_working_state_available: true" in captured["system_prompt"]
    assert _runtime_grounding_keys(captured["system_prompt"]) == {
        "cwd",
        "workspace_root",
        "current_session_history_available",
        "prior_session_history_available",
        "prior_context_present",
        "prior_turn_present",
        "session_working_state_available",
    }
    assert (
        runner.context_api.service._identityctl._system_prompt
        == captured["system_prompt"]
    )


def test_prepare_turn_appends_pending_turn_context_block_when_present() -> None:
    bridge = DummyBridge()
    runner = SimpleNamespace(
        context_api=_DummyContextAdapter(),
        session_api=_DummySessionAPI(
            {
                "pending_turn_context": {
                    "original_user_request": "can you save some python code for me?",
                    "active_work_summary": "The assistant drafted a local HTTP server and still needs the target path.",
                    "known_context": {
                        "cwd": "/tmp/openminion",
                    },
                    "missing_fields": ["path"],
                    "artifact_refs": ["artifact:previous"],
                    "response_preferences": {"language": "en"},
                }
            }
        ),
    )

    bridge._runtime_system_prompt = lambda *, user_message: "BASE SYSTEM"
    bridge._get_runner = lambda: runner
    bridge._reset_state_for_new_input = lambda **_: None
    bridge._inject_gateway_system_context = lambda **_: None
    bridge._security_policy = None
    bridge._telemetryctl = None
    bridge._llm_wrapper = None
    captured: dict[str, str] = {}

    def _capture_hydrate(*, runner, session_id, history, system_prompt):  # noqa: ANN001
        captured["system_prompt"] = str(system_prompt)

    bridge._hydrate_runner_session_context = _capture_hydrate  # type: ignore[method-assign]

    asyncio.run(
        bridge._prepare_turn(
            message=Message(
                channel="console",
                target="user",
                body="target-path-response",
                metadata={"request_id": "req-3"},
            ),
            history=[],
            forced_tools=None,
            capability_category=None,
            brain_session_id="brain-session-3",
        )
    )

    assert "## Pending Turn Context" in captured["system_prompt"]
    assert (
        "original_user_request: can you save some python code for me?"
        in captured["system_prompt"]
    )
    assert "missing_fields: path" in captured["system_prompt"]
    assert "artifact_refs: artifact:previous" in captured["system_prompt"]
    assert "response_preferences: language=en" in captured["system_prompt"]


def test_pending_turn_context_for_prompt_returns_present_context() -> None:
    bridge = DummyBridge()
    runner = _DummyRunner(
        {
            "pending_turn_context": {
                "original_user_request": "what is your location?",
                "active_work_summary": "get weather for Oakland",
                "known_context": {"location": "Oakland"},
            },
            "pending_turn_context_stale_turns": 1,
        }
    )

    prompt_context = bridge._pending_turn_context_for_prompt(
        runner=runner,
        session_id="s1",
    )

    assert prompt_context is not None
    assert prompt_context["active_work_summary"] == "get weather for Oakland"
    assert prompt_context["known_context"] == {"location": "Oakland"}


def test_pending_turn_context_for_prompt_returns_none_when_absent() -> None:
    bridge = DummyBridge()
    runner = _DummyRunner({})

    assert (
        bridge._pending_turn_context_for_prompt(runner=runner, session_id="s1") is None
    )


def test_pending_turn_context_stale_counter_allows_three_turns_then_expires() -> None:
    bridge = DummyBridge()
    runner = _DummyRunner(
        {
            "status": "waiting_user",
            "goal": "weather follow-up",
            "pending_turn_context": {
                "original_user_request": "what is your location?",
                "active_work_summary": "get weather for Oakland",
                "known_context": {"location": "Oakland"},
                "missing_fields": [],
                "artifact_refs": [],
                "response_preferences": {},
            },
            "pending_turn_context_stale_turns": 0,
        }
    )

    for turn_index in range(1, PENDING_TURN_CONTEXT_MAX_STALE_TURNS + 1):
        bridge._reset_state_for_new_input(
            runner=runner,
            session_id="s1",
            user_input=f"follow-up {turn_index}",
        )
        assert runner.session_api.written is not None
        assert runner.session_api.written["pending_turn_context"] is not None
        assert (
            runner.session_api.written["pending_turn_context_stale_turns"] == turn_index
        )
        assert (
            bridge._pending_turn_context_for_prompt(runner=runner, session_id="s1")
            is not None
        )
        runner.session_api._state = dict(runner.session_api.written)

    bridge._reset_state_for_new_input(
        runner=runner,
        session_id="s1",
        user_input="follow-up 4",
    )

    assert runner.session_api.written is not None
    assert runner.session_api.written["pending_turn_context"] is None
    assert runner.session_api.written["pending_turn_context_stale_turns"] == 0


def test_prior_turn_context_hint_prefers_history_and_keeps_verbatim_pair() -> None:
    bridge = DummyBridge()
    runner = _DummyRunner({})

    hint = bridge._prior_turn_context_hint(
        runner=runner,
        session_id="s1",
        history=[
            Message(
                channel="console",
                target="user",
                body="Can you get the weather for my city?",
                metadata={"role": "user"},
            ),
            Message(
                channel="console",
                target="user",
                body=(
                    "I checked your location. Oakland appears to be your current city. "
                    "Would you like me to get the current weather in Oakland for you?"
                ),
                metadata={"role": "assistant"},
            ),
        ],
    )

    assert hint == {
        "user_message": "Can you get the weather for my city?",
        "assistant_message": (
            "I checked your location. Oakland appears to be your current city. "
            "Would you like me to get the current weather in Oakland for you?"
        ),
    }


def test_prior_turn_context_hint_uses_verbatim_prefix_for_long_assistant_reply() -> (
    None
):
    bridge = DummyBridge()
    runner = _DummyRunner({})
    assistant_reply = (
        "Here is a detailed 10-day China itinerary with Beijing, Xian, and Shanghai. "
        "Day 1 focuses on flights and arrival logistics. "
        "Day 2 covers the Forbidden City and Wangfujing. "
        "Day 3 covers the Great Wall and local dinner plans. "
        "Day 4 covers the Summer Palace and hutongs. "
        "Day 5 covers the train to Xian. "
        "Want me to book anything?"
    )

    hint = bridge._prior_turn_context_hint(
        runner=runner,
        session_id="s1",
        history=[
            Message(
                channel="console",
                target="user",
                body="Can you plan a trip to China from next Wednesday?",
                metadata={"role": "user"},
            ),
            Message(
                channel="console",
                target="assistant",
                body=assistant_reply,
                metadata={"role": "assistant"},
            ),
        ],
    )

    assert hint is not None
    assert hint["user_message"] == "Can you plan a trip to China from next Wednesday?"
    assert hint["assistant_message"] == assistant_reply[:PRIOR_TURN_CONTEXT_CHAR_LIMIT]
    assert "Want me to book anything?" not in hint["assistant_message"]


def test_prepare_turn_appends_prior_turn_context_block_when_prior_assistant_exists() -> (
    None
):
    bridge = DummyBridge()
    runner = SimpleNamespace(
        context_api=_DummyContextAdapter(),
        session_api=_DummySessionAPI({}),
    )
    runner.session_api.turns.append(
        {
            "session_id": "brain-session-prior",
            "role": "user",
            "content": "Can you get the weather for my city?",
        }
    )
    runner.session_api.turns.append(
        {
            "session_id": "brain-session-prior",
            "role": "assistant",
            "content": (
                "I checked your location. Oakland appears to be your current city. "
                "Would you like me to get the current weather in Oakland for you?"
            ),
        }
    )

    bridge._runtime_system_prompt = lambda *, user_message: "BASE SYSTEM"
    bridge._get_runner = lambda: runner
    bridge._reset_state_for_new_input = lambda **_: None
    bridge._inject_gateway_system_context = lambda **_: None
    bridge._security_policy = None
    bridge._telemetryctl = None
    bridge._llm_wrapper = None
    captured: dict[str, str] = {}

    def _capture_hydrate(*, runner, session_id, history, system_prompt):  # noqa: ANN001
        captured["system_prompt"] = str(system_prompt)

    bridge._hydrate_runner_session_context = _capture_hydrate  # type: ignore[method-assign]

    asyncio.run(
        bridge._prepare_turn(
            message=Message(
                channel="console",
                target="user",
                body="yes",
                metadata={"request_id": "req-prior"},
            ),
            history=[],
            forced_tools=None,
            capability_category=None,
            brain_session_id="brain-session-prior",
        )
    )

    assert "## Prior Turn Context" in captured["system_prompt"]
    assert "- prior_turn_present: true" in captured["system_prompt"]
    assert (
        "prior_turn_block_present: A 'Prior Turn Context' block is present "
        "with the immediately preceding completed user/assistant turn from "
        "this live session."
    ) in captured["system_prompt"]
    assert '"Can you get the weather for my city?"' in captured["system_prompt"]
    assert (
        "Oakland appears to be your current city. Would you like me to get the current weather in Oakland for you?"
        in captured["system_prompt"]
    )


def test_prepare_turn_keeps_prior_turn_context_when_pending_context_exists() -> None:
    bridge = DummyBridge()
    runner = SimpleNamespace(
        context_api=_DummyContextAdapter(),
        session_api=_DummySessionAPI(
            {
                "pending_turn_context": {
                    "original_user_request": "plan a China trip",
                    "active_work_summary": "Detailed itinerary already drafted.",
                }
            }
        ),
    )
    runner.session_api.turns.extend(
        [
            {
                "session_id": "brain-session-prior",
                "role": "user",
                "content": "Can you plan a trip to China from next Wednesday?",
            },
            {
                "session_id": "brain-session-prior",
                "role": "assistant",
                "content": (
                    "Here is a 10-day China itinerary with Beijing, Xian, and Shanghai."
                ),
            },
        ]
    )

    bridge._runtime_system_prompt = lambda *, user_message: "BASE SYSTEM"
    bridge._get_runner = lambda: runner
    bridge._reset_state_for_new_input = lambda **_: None
    bridge._inject_gateway_system_context = lambda **_: None
    bridge._security_policy = None
    bridge._telemetryctl = None
    bridge._llm_wrapper = None
    captured: dict[str, str] = {}

    def _capture_hydrate(*, runner, session_id, history, system_prompt):  # noqa: ANN001
        captured["system_prompt"] = str(system_prompt)

    bridge._hydrate_runner_session_context = _capture_hydrate  # type: ignore[method-assign]

    asyncio.run(
        bridge._prepare_turn(
            message=Message(
                channel="console",
                target="user",
                body="Can you detail each day more?",
                metadata={"request_id": "req-prior"},
            ),
            history=[],
            forced_tools=None,
            capability_category=None,
            brain_session_id="brain-session-prior",
        )
    )

    assert "## Pending Turn Context" in captured["system_prompt"]
    assert "## Prior Turn Context" in captured["system_prompt"]
    assert "- prior_turn_present: true" in captured["system_prompt"]
    assert (
        "prior_turn_block_present: A 'Prior Turn Context' block is present "
        "with the immediately preceding completed user/assistant turn from "
        "this live session."
    ) in captured["system_prompt"]
    assert (
        '"Can you plan a trip to China from next Wednesday?"'
        in captured["system_prompt"]
    )
    assert (
        "Here is a 10-day China itinerary with Beijing, Xian, and Shanghai."
        in captured["system_prompt"]
    )


def test_prepare_turn_prior_turn_contract_holds_for_followup_phrase_family() -> None:
    bridge = DummyBridge()
    runner = SimpleNamespace(
        context_api=_DummyContextAdapter(),
        session_api=_DummySessionAPI({}),
    )
    runner.session_api.turns.extend(
        [
            {
                "session_id": "brain-session-prior",
                "role": "user",
                "content": (
                    "Can you plan a trip to China from next Wednesday? "
                    "You can decide all details and budget."
                ),
            },
            {
                "session_id": "brain-session-prior",
                "role": "assistant",
                "content": (
                    "Here is a detailed China itinerary with Beijing, Xian, and Shanghai."
                ),
            },
        ]
    )

    bridge._runtime_system_prompt = lambda *, user_message: "BASE SYSTEM"
    bridge._get_runner = lambda: runner
    bridge._reset_state_for_new_input = lambda **_: None
    bridge._inject_gateway_system_context = lambda **_: None
    bridge._security_policy = None
    bridge._telemetryctl = None
    bridge._llm_wrapper = None

    for followup in (
        "can you detail more for each day?",
        "can you give me detail on each day?",
        "can you break down each step?",
    ):
        captured: dict[str, str] = {}

        def _capture_hydrate(  # noqa: ANN001
            *, runner, session_id, history, system_prompt
        ):
            captured["system_prompt"] = str(system_prompt)

        bridge._hydrate_runner_session_context = _capture_hydrate  # type: ignore[method-assign]

        asyncio.run(
            bridge._prepare_turn(
                message=Message(
                    channel="console",
                    target="user",
                    body=followup,
                    metadata={"request_id": f"req-{followup}"},
                ),
                history=[],
                forced_tools=None,
                capability_category=None,
                brain_session_id="brain-session-prior",
            )
        )

        assert "- prior_turn_present: true" in captured["system_prompt"]
        assert "## Prior Turn Context" in captured["system_prompt"]
        assert (
            "prior_turn_block_present: A 'Prior Turn Context' block is present "
            "with the immediately preceding completed user/assistant turn from "
            "this live session."
        ) in captured["system_prompt"]
        assert (
            '"Can you plan a trip to China from next Wednesday? You can decide all details and budget."'
            in captured["system_prompt"]
        )
        assert (
            "Here is a detailed China itinerary with Beijing, Xian, and Shanghai."
            in captured["system_prompt"]
        )


def test_prepare_turn_appends_true_cwd_and_recent_artifact_facts() -> None:
    bridge = DummyBridge()
    runner = SimpleNamespace(
        context_api=_DummyContextAdapter(),
        session_api=_DummySessionAPI({}),
    )

    bridge._runtime_system_prompt = lambda *, user_message: "BASE SYSTEM"
    bridge._get_runner = lambda: runner
    bridge._reset_state_for_new_input = lambda **_: None
    bridge._inject_gateway_system_context = lambda **_: None
    bridge._security_policy = None
    bridge._telemetryctl = None
    bridge._llm_wrapper = None
    captured: dict[str, str] = {}

    def _capture_hydrate(*, runner, session_id, history, system_prompt):  # noqa: ANN001
        captured["system_prompt"] = str(system_prompt)

    bridge._hydrate_runner_session_context = _capture_hydrate  # type: ignore[method-assign]

    asyncio.run(
        bridge._prepare_turn(
            message=Message(
                channel="console",
                target="user",
                body="target.cpp",
                metadata={
                    "request_id": "req-4",
                    "cwd": "/tmp/openminion-chat",
                    "recent_artifacts": json.dumps(
                        [
                            {
                                "ref": "artifact:previous",
                                "path": "/tmp/openminion-chat/target.cpp",
                                "kind": "code",
                                "content": "ignored",
                            }
                        ]
                    ),
                },
            ),
            history=[],
            forced_tools=None,
            capability_category=None,
            brain_session_id="brain-session-4",
        )
    )

    expected_cwd = str(Path("/tmp/openminion-chat").resolve(strict=False))
    assert f"- cwd: {expected_cwd}" in captured["system_prompt"]
    assert (
        "- recent_artifacts: {ref=artifact:previous, path=/tmp/openminion-chat/target.cpp, kind=code}"
        in captured["system_prompt"]
    )


def test_resolve_turn_session_ids_scopes_by_conversation() -> None:
    bridge = DummyBridge()
    runtime_session_id, brain_session_id = bridge._resolve_turn_session_ids(
        message=SimpleNamespace(
            metadata={"session_id": "s1", "conversation_id": "conv-abc"}
        )
    )
    assert runtime_session_id == "s1"
    assert brain_session_id == "s1::conv:conv-abc"


def test_resolve_turn_session_ids_prefers_explicit_brain_session_override() -> None:
    bridge = DummyBridge()
    runtime_session_id, brain_session_id = bridge._resolve_turn_session_ids(
        message=SimpleNamespace(
            metadata={
                "session_id": "s1",
                "conversation_id": "conv-abc",
                "brain_session_id": "brain-scope-1",
            }
        )
    )
    assert runtime_session_id == "s1"
    assert brain_session_id == "brain-scope-1"


def test_resolve_turn_session_ids_falls_back_to_thread_id_scope() -> None:
    bridge = DummyBridge()
    runtime_session_id, brain_session_id = bridge._resolve_turn_session_ids(
        message=SimpleNamespace(
            metadata={"session_id": "s1", "thread_id": "thread-xyz"}
        )
    )
    assert runtime_session_id == "s1"
    assert brain_session_id == "s1::conv:thread-xyz"


def test_extract_memory_policy_metadata_snapshot_response() -> None:
    payload = DummyBridge()._extract_memory_policy_metadata(
        response_text=(
            "Memory policy snapshot:\n"
            "- source: runtime.config\n"
            "- version: memory_policy_snapshot.v1\n"
            "- retention_days: 30\n"
        )
    )
    assert payload is not None
    assert payload["memory_policy_route"] == "runtime_policy_snapshot"
    assert payload["memory_policy_source"] == "runtime.config"
    assert payload["memory_policy_version"] == "memory_policy_snapshot.v1"
    assert payload["reason_code"] == "memory_policy_snapshot"
    assert payload["response_posture"] == "deterministic"


def test_extract_memory_policy_metadata_policy_unavailable_response() -> None:
    payload = DummyBridge()._extract_memory_policy_metadata(
        response_text=(
            "MEMORY_POLICY: policy_unavailable "
            "(source=runtime.config version=memory_policy_snapshot.v1 "
            "reason=policy_unavailable:RuntimeError)"
        )
    )
    assert payload is not None
    assert payload["memory_policy_route"] == "runtime_policy_snapshot"
    assert payload["memory_policy_source"] == "runtime.config"
    assert payload["memory_policy_version"] == "memory_policy_snapshot.v1"
    assert payload["reason_code"] == "policy_unavailable"
    assert payload["response_posture"] == "degraded"
    assert payload["memory_policy_error"] == "policy_unavailable:RuntimeError"


def test_capability_inference_helpers_removed() -> None:
    bridge = DummyBridge()
    assert not hasattr(bridge, "_resolve_capability_category")
    assert not hasattr(bridge, "_pending_runtime_clarification_reason")
    assert not hasattr(bridge, "_looks_like_clarification_answer_fragment")
    assert not hasattr(bridge, "_looks_like_weather_location_fragment")


def test_reset_state_for_new_input_clears_stale_clarify_state() -> None:
    bridge = DummyBridge()
    runner = _DummyRunner(
        {
            "status": "waiting_user",
            "plan": {"steps": [{"id": "s1"}]},
            "cursor": 1,
            "retries_for_step": {"s1": 2},
            "pending_jobs": [{"id": "j1"}],
            "open_questions": ["old?"],
            "unresolved_clarify_items": [{"id": "q1"}],
            "pending_clarify_items": [{"id": "q1"}],
            "clarify_responses": {"q1": "n/a"},
            "clarify_resume_cursor": "q1",
            "pending_llm_clarify_context": {
                "original_user_input": "what's rather at china?",
                "inferred_goal": "weather",
                "known_context": {"place": "China"},
                "clarify_question": "Did you mean the weather in China?",
                "unresolved_question": "",
            },
            "constraints": [
                "Guardrail: Avoid repeating failed command: Tool call: browser"
            ],
            "last_result": {
                "status": "failed",
                "summary": "'no browser provider specified and no default configured'",
            },
            "last_command_id": "cmd-1",
            "step_outputs": [{"step_id": "s1", "status": "success"}],
            "recent_artifacts": [{"path": "/tmp/report.txt"}],
            "pending_confirmation_command": {"type": "tool", "tool_name": "weather"},
            "pending_confirmation_sub_intents": ["check_weather"],
            "pending_confirmation_sub_intent_refs": [
                {"id": "intent_01_check_weather", "description": "check_weather"}
            ],
            "pending_confirmation_rationale": "old rationale",
            "pending_confirmation_success_criteria": {"status": "success"},
            "pending_confirmation_feasibility_state": {"reviewed": True},
            "decision_sub_intents": ["check_weather"],
            "decision_sub_intent_refs": [
                {"id": "intent_01_check_weather", "description": "check_weather"}
            ],
            "decision_rationale": "weather rationale",
            "decision_success_criteria": {"status": "success"},
            "decision_feasibility_state": {"reviewed": True},
            "adaptive_satisfied_intent_ids": ["intent_01_check_weather"],
            "last_adaptive_revision_checkpoint": {
                "action": "continue",
                "completed_intent_ids": ["intent_01_check_weather"],
            },
            "last_progress_checkpoint": {"outcome": "continue"},
            "last_step_risk_assessment": {"outcome": "execute"},
            "intent_execution_states": [
                {
                    "intent_id": "intent_01_check_weather",
                    "description": "check_weather",
                    "status": "succeeded",
                }
            ],
            "goal": "old goal",
        }
    )

    bridge._reset_state_for_new_input(
        runner=runner,
        session_id="s1",
        user_input="show me file on this dir",
    )

    assert runner.session_api.written is not None
    written = runner.session_api.written
    assert written["goal"] == "show me file on this dir"
    assert written["unresolved_clarify_items"] == []
    assert written["pending_clarify_items"] == []
    assert written["clarify_responses"] == {}
    assert written["clarify_resume_cursor"] is None
    assert written["pending_llm_clarify_context"] is None
    assert written["constraints"] == []
    assert written["last_result"] is None
    assert written["last_command_id"] is None
    assert written["step_outputs"] == []
    assert written["recent_artifacts"] == []
    assert written["pending_confirmation_command"] is None
    assert written["pending_confirmation_sub_intents"] == []
    assert written["pending_confirmation_sub_intent_refs"] == []
    assert written["pending_confirmation_rationale"] == ""
    assert written["pending_confirmation_success_criteria"] == {}
    assert written["pending_confirmation_feasibility_state"] == {}
    assert written["pending_confirmation_feasibility_report"] is None
    assert written["decision_sub_intents"] == []
    assert written["decision_sub_intent_refs"] == []
    assert written["decision_rationale"] == ""
    assert written["decision_success_criteria"] == {}
    assert written["decision_feasibility_state"] == {}
    assert written["decision_feasibility_report"] is None
    assert written["adaptive_satisfied_intent_ids"] == []
    assert written["last_adaptive_revision_checkpoint"] is None
    assert written["last_progress_checkpoint"] is None
    assert written["last_step_risk_assessment"] is None
    assert written["intent_execution_states"] == []


def test_reset_state_for_new_input_preserves_pending_llm_clarify_context_for_waiting_user_followup() -> (
    None
):
    bridge = DummyBridge()
    runner = _DummyRunner(
        {
            "status": "waiting_user",
            "goal": "what's rather at china?",
            "unresolved_clarify_items": [],
            "pending_clarify_items": [],
            "clarify_responses": {},
            "pending_llm_clarify_context": {
                "original_user_input": "what's rather at china?",
                "inferred_goal": "weather",
                "known_context": {"place": "China"},
                "clarify_question": "Did you mean the weather in China?",
                "unresolved_question": "",
            },
        }
    )

    bridge._reset_state_for_new_input(
        runner=runner,
        session_id="s1",
        user_input="yes, weather",
    )

    assert runner.session_api.written is not None
    written = runner.session_api.written
    assert written["goal"] == "yes, weather"
    assert written["status"] == "waiting_user"
    assert written["pending_llm_clarify_context"] == {
        "original_user_input": "what's rather at china?",
        "inferred_goal": "weather",
        "known_context": {"place": "China"},
        "clarify_question": "Did you mean the weather in China?",
        "unresolved_question": "",
    }


def test_reset_state_for_new_input_clears_pending_llm_clarify_context_when_not_waiting_user() -> (
    None
):
    bridge = DummyBridge()
    runner = _DummyRunner(
        {
            "status": "active",
            "goal": "old goal",
            "pending_llm_clarify_context": {
                "original_user_input": "what's rather at china?",
                "inferred_goal": "weather",
                "known_context": {"place": "China"},
                "clarify_question": "Did you mean the weather in China?",
                "unresolved_question": "",
            },
        }
    )

    bridge._reset_state_for_new_input(
        runner=runner,
        session_id="s1",
        user_input="new task",
    )

    assert runner.session_api.written is not None
    written = runner.session_api.written
    assert written["goal"] == "new task"
    assert written["status"] == "active"
    assert written["pending_llm_clarify_context"] is None


def test_reset_state_for_new_input_preserves_pending_turn_context() -> None:
    bridge = DummyBridge()
    runner = _DummyRunner(
        {
            "status": "waiting_user",
            "goal": "save the server code",
            "pending_turn_context": {
                "original_user_request": "save the server code",
                "active_work_summary": "Waiting for a target path.",
                "known_context": {"cwd": "/tmp/openminion"},
                "missing_fields": ["path"],
                "artifact_refs": ["artifact:previous"],
                "response_preferences": {"language": "en"},
            },
            "pending_turn_context_stale_turns": 0,
        }
    )

    bridge._reset_state_for_new_input(
        runner=runner,
        session_id="s1",
        user_input="target-path-response",
    )

    assert runner.session_api.written is not None
    written = runner.session_api.written
    assert written["goal"] == "target-path-response"
    assert written["pending_turn_context"] is not None
    assert written["pending_turn_context_stale_turns"] == 1


def test_reset_state_for_new_input_preserves_session_work_summary() -> None:
    bridge = DummyBridge()
    runner = _DummyRunner(
        {
            "status": "active",
            "goal": "continue the auth work",
            "session_work_summary": (
                "Built authentication flow in auth.py and still need to wire token refresh."
            ),
        }
    )

    bridge._reset_state_for_new_input(
        runner=runner,
        session_id="s1",
        user_input="what next?",
    )

    assert runner.session_api.written is not None
    written = runner.session_api.written
    assert (
        written["session_work_summary"]
        == "Built authentication flow in auth.py and still need to wire token refresh."
    )


def test_reset_state_for_new_input_clears_task_backed_resume_state_for_new_goal() -> (
    None
):
    bridge = DummyBridge()
    runner = _DummyRunner(
        {
            "status": "waiting_user",
            "goal": "can you do deeper research?",
            "task_backed_task_id": "task-research-1",
            "task_backed_checkpoint_id": "research-task-research-1-cursor-2",
            "task_backed_resume_state": {
                "query": "can you do deeper research?",
                "iteration": 2,
            },
        }
    )

    bridge._reset_state_for_new_input(
        runner=runner,
        session_id="s1",
        user_input="what time is now?",
    )

    assert runner.session_api.written is not None
    written = runner.session_api.written
    assert written["goal"] == "what time is now?"
    assert written["task_backed_task_id"] is None
    assert written["task_backed_checkpoint_id"] is None
    assert written["task_backed_resume_state"] == {}


def test_reset_state_for_new_input_ages_out_pending_turn_context_at_ttl() -> None:
    bridge = DummyBridge()
    runner = _DummyRunner(
        {
            "status": "waiting_user",
            "goal": "save the server code",
            "pending_turn_context": {
                "original_user_request": "save the server code",
                "active_work_summary": "Waiting for a target path.",
                "known_context": {"cwd": "/tmp/openminion"},
                "missing_fields": ["path"],
                "artifact_refs": ["artifact:previous"],
                "response_preferences": {"language": "en"},
            },
            "pending_turn_context_stale_turns": PENDING_TURN_CONTEXT_MAX_STALE_TURNS,
        }
    )

    bridge._reset_state_for_new_input(
        runner=runner,
        session_id="s1",
        user_input="another follow-up",
    )

    assert runner.session_api.written is not None
    written = runner.session_api.written
    assert written["pending_turn_context"] is None
    assert written["pending_turn_context_stale_turns"] == 0


def test_pending_turn_context_for_prompt_skips_aged_out_context() -> None:
    bridge = DummyBridge()
    runner = _DummyRunner(
        {
            "pending_turn_context": {
                "original_user_request": "save the server code",
                "active_work_summary": "Waiting for a target path.",
            },
            "pending_turn_context_stale_turns": (
                PENDING_TURN_CONTEXT_MAX_STALE_TURNS + 1
            ),
        }
    )

    assert (
        bridge._pending_turn_context_for_prompt(runner=runner, session_id="s1") is None
    )


def test_reset_state_for_new_input_previews_continue_mission_budget() -> None:
    bridge = DummyBridge()
    runner = _DummyRunner(
        {
            "session_id": "s1",
            "agent_id": "mission-agent",
            "status": "waiting_user",
            "goal": "mission objective",
            "mission": {
                "mission_id": "mission-1",
                "objective": "mission objective",
                "status": "active",
                "budget": {
                    "total_remaining": {
                        "ticks": 6,
                        "tool_calls": 4,
                        "a2a_calls": 0,
                        "tokens": 3000,
                        "time_ms": 12000,
                    },
                    "per_turn_max": {
                        "ticks": 3,
                        "tool_calls": 2,
                        "a2a_calls": 0,
                        "tokens": 1500,
                        "time_ms": 6000,
                    },
                    "remaining_llm_calls_total": 9,
                    "llm_calls_per_turn_max": 5,
                },
                "latest_route_action": "start",
            },
            "budgets_remaining": {
                "ticks": 6,
                "tool_calls": 4,
                "a2a_calls": 0,
                "tokens": 3000,
                "time_ms": 12000,
            },
        }
    )

    bridge._reset_state_for_new_input(
        runner=runner,
        session_id="s1",
        user_input="continue mission",
    )

    assert runner.session_api.written is not None
    written = runner.session_api.written
    assert written["goal"] == "mission objective"
    assert written["budgets_remaining"] == {
        "ticks": 3,
        "tool_calls": 2,
        "a2a_calls": 0,
        "tokens": 1500,
        "time_ms": 6000,
    }
    assert written["llm_calls_max"] == 5


def test_reset_state_for_new_input_allows_ordinary_turn_after_paused_mission() -> None:
    bridge = DummyBridge()
    runner = _DummyRunner(
        {
            "session_id": "s1",
            "agent_id": "mission-agent",
            "status": "waiting_user",
            "goal": "mission objective",
            "mission": {
                "mission_id": "mission-2",
                "objective": "mission objective",
                "status": "paused",
                "budget": {
                    "total_remaining": {
                        "ticks": 6,
                        "tool_calls": 4,
                        "a2a_calls": 0,
                        "tokens": 3000,
                        "time_ms": 12000,
                    },
                    "per_turn_max": {
                        "ticks": 3,
                        "tool_calls": 2,
                        "a2a_calls": 0,
                        "tokens": 1500,
                        "time_ms": 6000,
                    },
                    "remaining_llm_calls_total": 9,
                    "llm_calls_per_turn_max": 5,
                },
                "latest_route_action": "pause",
            },
            "budgets_remaining": {
                "ticks": 6,
                "tool_calls": 4,
                "a2a_calls": 0,
                "tokens": 3000,
                "time_ms": 12000,
            },
        }
    )

    bridge._reset_state_for_new_input(
        runner=runner,
        session_id="s1",
        user_input="ordinary follow-up",
    )

    assert runner.session_api.written is not None
    written = runner.session_api.written
    assert written["goal"] == "ordinary follow-up"


def test_reset_state_for_continue_preserves_goal_but_drops_stale_nonresumable_plan() -> (
    None
):
    bridge = DummyBridge()
    runner = _DummyRunner(
        {
            "status": "waiting_user",
            "phase": None,
            "goal": "plan trip plan for me for japan from next week for 2 weeks.",
            "plan": {
                "objective": "stale blocked work",
                "steps": [
                    {
                        "kind": "ask_user",
                        "title": "blocked",
                        "question": "I don't have access to the tools needed.",
                    }
                ],
            },
            "cursor": 0,
            "decision_sub_intents": ["research_japan_destinations"],
            "decision_feasibility_state": {},
        }
    )

    bridge._reset_state_for_new_input(
        runner=runner,
        session_id="s1",
        user_input="continue",
    )

    assert runner.session_api.written is not None
    written = runner.session_api.written
    assert (
        written["goal"] == "plan trip plan for me for japan from next week for 2 weeks."
    )
    assert written["plan"] is None
    assert written["cursor"] == 0
    assert written["decision_sub_intents"] == []
    assert written["decision_feasibility_state"] == {}


def test_reset_state_for_followup_control_inputs_preserves_resumable_plan_state() -> (
    None
):
    bridge = DummyBridge()
    base_state = {
        "status": "waiting_user",
        "phase": "ACT",
        "goal": "check weather and book me a flight",
        "plan": {
            "objective": "travel",
            "steps": [
                {"kind": "tool", "title": "weather"},
                {"kind": "tool", "title": "book"},
            ],
        },
        "cursor": 1,
        "decision_sub_intents": ["check_weather", "book_flight"],
        "decision_sub_intent_refs": [
            {"id": "intent_01_check_weather", "description": "check_weather"},
            {"id": "intent_02_book_flight", "description": "book_flight"},
        ],
        "decision_success_criteria": {"status": "success"},
        "decision_feasibility_state": {
            "awaiting_user_choice": True,
            "reviewed": True,
        },
        "decision_feasibility_report": {"plan_viable": False},
        "adaptive_satisfied_intent_ids": ["intent_01_check_weather"],
        "last_adaptive_revision_checkpoint": {
            "action": "continue",
            "completed_intent_ids": ["intent_01_check_weather"],
        },
        "last_progress_checkpoint": {"outcome": "continue"},
        "last_step_risk_assessment": {"outcome": "execute"},
        "intent_execution_states": [
            {
                "intent_id": "intent_01_check_weather",
                "description": "check_weather",
                "status": "succeeded",
            }
        ],
    }

    for control_input in ("continue", "retry", "skip", "cancel"):
        runner = _DummyRunner(base_state)
        bridge._reset_state_for_new_input(
            runner=runner,
            session_id="s1",
            user_input=control_input,
        )
        assert runner.session_api.written is not None
        written = runner.session_api.written
        assert written["goal"] == "check weather and book me a flight"
        assert written["plan"] == base_state["plan"]
        assert written["cursor"] == 1
        assert written["decision_sub_intents"] == ["check_weather", "book_flight"]
        assert written["decision_feasibility_state"]["awaiting_user_choice"] is True
        assert written["adaptive_satisfied_intent_ids"] == ["intent_01_check_weather"]
        assert written["last_adaptive_revision_checkpoint"] == {
            "action": "continue",
            "completed_intent_ids": ["intent_01_check_weather"],
        }


def test_reset_state_for_continue_preserves_waiting_plan_with_executable_current_step() -> (
    None
):
    bridge = DummyBridge()
    runner = _DummyRunner(
        {
            "status": "waiting_user",
            "phase": "PLAN",
            "goal": "plan trip plan for me for japan from next week for 2 weeks.",
            "plan": {
                "objective": "japan trip",
                "steps": [
                    {"kind": "tool", "title": "research destinations"},
                    {"kind": "think", "title": "synthesize itinerary"},
                ],
            },
            "cursor": 0,
        }
    )

    bridge._reset_state_for_new_input(
        runner=runner,
        session_id="s1",
        user_input="continue",
    )

    assert runner.session_api.written is not None
    written = runner.session_api.written
    assert (
        written["goal"] == "plan trip plan for me for japan from next week for 2 weeks."
    )
    assert written["plan"] == runner.session_api._state["plan"]
    assert written["cursor"] == 0


def test_is_state_machine_error_text_detects_only_explicit_system_sentinel() -> None:
    bridge = DummyBridge()
    assert bridge._is_state_machine_error_text(
        "[system: UNEXECUTABLE_TOOL_ENVELOPE] blocked"
    )
    assert not bridge._is_state_machine_error_text(
        "'no browser provider specified and no default configured'"
    )
    assert not bridge._is_state_machine_error_text(
        "The prior state machine error was resolved by retrying the browser."
    )


def test_hydrate_runner_session_context_skips_duplicates_and_error_turns() -> None:
    bridge = DummyBridge()
    bridge._config = OpenMinionConfig()  # type: ignore[attr-defined]
    _csc_install_default_agent(bridge._config)  # type: ignore[attr-defined]
    runner = SimpleNamespace(session_api=_DummySessionAPI({}))
    runner.session_api.turns = [
        {
            "session_id": "s1",
            "role": "assistant",
            "content": "hello there",
        }
    ]
    history = [
        Message(
            channel="console",
            target="user",
            body="openminion: hello there",
            metadata={"role": "assistant"},
        ),
        Message(
            channel="console",
            target="user",
            body="[system: UNEXECUTABLE_TOOL_ENVELOPE] blocked",
            metadata={"role": "assistant"},
        ),
        Message(
            channel="console",
            target="user",
            body="new question",
            metadata={"role": "user"},
        ),
        Message(
            channel="console",
            target="user",
            body="",
            metadata={"role": "assistant"},
        ),
    ]

    bridge._hydrate_runner_session_context(
        runner=runner,
        session_id="s1",
        history=history,
        system_prompt="BASE SYSTEM",
    )

    assert runner.session_api.turns == [
        {
            "session_id": "s1",
            "role": "assistant",
            "content": "hello there",
        },
        {
            "session_id": "s1",
            "role": "user",
            "content": "new question",
        },
    ]


def test_follow_up_after_tool_uses_fallback_for_embedded_tool_call_text() -> None:
    bridge = DummyBridge()
    bridge._config = OpenMinionConfig()
    _csc_install_default_agent(bridge._config)
    bridge._provider = SimpleNamespace(name="fake-provider")
    bridge._llm_runtime = None
    bridge._tools = None
    bridge._runner = SimpleNamespace(session_api=_DummySessionAPI({}))

    async def _invoke_provider_request(_request):
        return ProviderResponse(
            text="[tool_call] weather",
            model="follow-model",
            tool_calls=[],
            finish_reason="stop",
            usage={"total_tokens": 3},
        )

    bridge._invoke_provider_request = _invoke_provider_request  # type: ignore[attr-defined]

    final_text, final_model = asyncio.run(
        bridge._follow_up_after_tool(
            message=Message(channel="console", target="user", body="weather"),
            history=[],
            prior_assistant_text="",
            tool_results=[
                {
                    "tool_name": "weather",
                    "ok": True,
                    "verified": True,
                    "content": "Kyoto is 10C.",
                    "error": "",
                    "data": {},
                    "call_id": "call-1",
                    "source": "native",
                }
            ],
            session_id="s-follow",
            trace_id="trace-follow",
        )
    )

    assert final_text == "Kyoto is 10C."
    assert final_model == "follow-model"
    event_types = [item["type"] for item in bridge._runner.session_api.events]
    assert event_types == ["llm.call.started", "llm.call.completed"]


def test_postprocess_turn_attaches_clarify_request_metadata() -> None:
    bridge = DummyBridge()
    bridge._config = OpenMinionConfig()
    _csc_install_default_agent(bridge._config)
    bridge._provider = SimpleNamespace(name="fake-provider")
    bridge._telemetryctl = None
    bridge._identity_metadata = dict
    runner = SimpleNamespace(session_api=_DummySessionAPI({}))
    step_out = SimpleNamespace(
        message="Please clarify.",
        status="waiting_user",
        action_result=None,
        working_state=SimpleNamespace(
            plan=SimpleNamespace(steps=[]),
            llm_calls_used=1,
            active_mode_name="plan",
            unresolved_clarify_items=[
                {
                    "id": "q1",
                    "question": "Which city?",
                    "reason_code": "weather_location_required",
                    "source": "clarify",
                }
            ],
            trace_id="trace-clarify",
        ),
    )

    response = asyncio.run(
        bridge._postprocess_turn(
            runner=runner,
            step_out=step_out,
            message=Message(channel="console", target="user", body="weather?"),
            history=[],
            session_id="s-clarify",
            request_id="trace-clarify",
            turn_id="turn-1",
            turn_start_time=0.0,
        )
    )

    assert response.metadata["finish_reason"] == "stop"
    assert response.metadata["clarify_id"]
    assert response.metadata["clarify_question_count"] == "1"
    assert "Which city?" in response.metadata["clarify_request"]


def test_postprocess_turn_attaches_tool_and_security_metadata() -> None:
    bridge = DummyBridge()
    bridge._config = OpenMinionConfig()
    _csc_install_default_agent(bridge._config)
    bridge._provider = SimpleNamespace(name="fake-provider")
    bridge._telemetryctl = _DummyTelemetry()
    bridge._identity_metadata = dict
    runner = SimpleNamespace(session_api=_DummySessionAPI({}))
    action_error = SimpleNamespace(
        code="tool_budget_cost_exceeded",
        message="tool budget exceeded",
        details={"policy_version": "v2", "budget_cost_total": 4},
    )
    action_result = SimpleNamespace(
        status="error",
        outputs={"error": "tool_budget_cost_exceeded"},
        summary="budget denied",
        error=action_error,
        command_id="cmd-1",
    )

    class _Step:
        command_id = "cmd-1"

        @staticmethod
        def model_dump(mode: str = "json") -> dict[str, str]:
            del mode
            return {
                "command_id": "cmd-1",
                "kind": "tool",
                "tool_name": "search",
            }

    step_out = SimpleNamespace(
        message="",
        status="running",
        action_result=action_result,
        working_state=SimpleNamespace(
            plan=SimpleNamespace(steps=[_Step()]),
            llm_calls_used=1,
            active_mode_name="act",
            unresolved_clarify_items=[],
        ),
    )

    response = asyncio.run(
        bridge._postprocess_turn(
            runner=runner,
            step_out=step_out,
            message=Message(channel="console", target="user", body="search now"),
            history=[],
            session_id="s-tool",
            request_id="trace-tool",
            turn_id="turn-2",
            turn_start_time=0.0,
        )
    )

    assert response.metadata["tool_execution_count"] == "1"
    assert response.metadata["tool_loop_termination_reason"] == "tool_no_success"
    assert "tool_budget_cost_exceeded" in response.metadata["security_events"]
    assert response.metadata["tool_budget"] == (
        '{"budget_cost_total": 4, "policy_version": "v2"}'
    )


def test_postprocess_turn_prefixes_response_with_selected_runtime_agent() -> None:
    bridge = DummyBridge()
    config = OpenMinionConfig.from_dict(
        {
            "default_agent": "minimax-m2-7",
            "agents": {
                "minimax-m2-7": {"name": "minimax-m2-7", "provider": "openai"},
                "minimax-m2-5": {"name": "minimax-m2-5", "provider": "openai"},
            },
        }
    )
    bridge._config = build_runtime_config(config, agent_id="minimax-m2-5")
    bridge._provider = SimpleNamespace(name="fake-provider")
    bridge._telemetryctl = None
    bridge._identity_metadata = dict
    runner = SimpleNamespace(session_api=_DummySessionAPI({}))
    step_out = SimpleNamespace(
        message="Here is the answer.",
        status="done",
        action_result=SimpleNamespace(outputs={}),
        working_state=SimpleNamespace(
            plan=SimpleNamespace(steps=[]),
            llm_calls_used=1,
            active_mode_name="act",
            unresolved_clarify_items=[],
        ),
    )

    response = asyncio.run(
        bridge._postprocess_turn(
            runner=runner,
            step_out=step_out,
            message=Message(channel="console", target="user", body="hello"),
            history=[],
            session_id="s-selected-agent",
            request_id="trace-selected-agent",
            turn_id="turn-selected-agent",
            turn_start_time=0.0,
        )
    )

    assert response.text.startswith("minimax-m2-5: Here is the answer.")
    assert response.metadata["agent"] == "minimax-m2-5"


def test_postprocess_turn_preserves_explicit_duplicate_termination_reason() -> None:
    bridge = DummyBridge()
    bridge._config = OpenMinionConfig()
    _csc_install_default_agent(bridge._config)
    bridge._provider = SimpleNamespace(name="fake-provider")
    bridge._telemetryctl = _DummyTelemetry()
    bridge._identity_metadata = dict
    runner = SimpleNamespace(session_api=_DummySessionAPI({}))
    action_error = SimpleNamespace(
        code="act_adaptive_duplicate_tool_calls",
        message="repeated identical tool calls detected without reaching a final answer",
        details={"reason_code": "act_adaptive_duplicate_tool_calls"},
    )
    action_result = SimpleNamespace(
        status="blocked",
        outputs={
            "adaptive.termination_reason": "duplicate_tool_calls",
            "tool_results": [
                {
                    "tool_name": "weather",
                    "ok": False,
                    "verified": False,
                    "content": "Tool execution failed",
                    "error": "One of location/city/query/place or latitude+longitude is required",
                    "error_code": "EXEC_ERROR",
                }
            ],
        },
        summary="[act] repeated identical tool calls detected without reaching a final answer.",
        error=action_error,
        command_id="cmd-dup",
    )
    step_out = SimpleNamespace(
        message="[act] repeated identical tool calls detected without reaching a final answer.",
        status="running",
        action_result=action_result,
        working_state=SimpleNamespace(
            plan=None,
            llm_calls_used=4,
            active_mode_name="act",
            unresolved_clarify_items=[],
        ),
    )

    response = asyncio.run(
        bridge._postprocess_turn(
            runner=runner,
            step_out=step_out,
            message=Message(channel="console", target="user", body="weather?"),
            history=[],
            session_id="s-dup",
            request_id="trace-dup",
            turn_id="turn-dup",
            turn_start_time=0.0,
        )
    )

    assert response.metadata["tool_execution_count"] == "1"
    assert response.metadata["tool_loop_termination_reason"] == "duplicate_tool_calls"


def test_postprocess_turn_uses_aggregated_tool_results_from_action_outputs() -> None:
    bridge = DummyBridge()
    bridge._config = OpenMinionConfig()
    _csc_install_default_agent(bridge._config)
    bridge._provider = SimpleNamespace(name="fake-provider")
    bridge._telemetryctl = _DummyTelemetry()
    bridge._identity_metadata = dict
    runner = SimpleNamespace(session_api=_DummySessionAPI({}))
    step_out = SimpleNamespace(
        message="I'll fetch that for you.",
        status="done",
        action_result=SimpleNamespace(
            status="success",
            summary="final answer",
            command_id="cmd-final",
            outputs={
                "tool_results": [
                    {
                        "tool_name": "time",
                        "ok": True,
                        "verified": True,
                        "content": "2026-04-09T00:00:00Z",
                        "error": "",
                        "data": {"timezone": "UTC"},
                        "call_id": "cmd-time",
                        "source": "native",
                    },
                    {
                        "tool_name": "file.read",
                        "ok": True,
                        "verified": True,
                        "content": "# README",
                        "error": "",
                        "data": {"path": "/tmp/README.md"},
                        "call_id": "cmd-readme",
                        "source": "native",
                    },
                ]
            },
        ),
        working_state=SimpleNamespace(
            plan=SimpleNamespace(steps=[]),
            llm_calls_used=1,
            active_mode_name="act",
            unresolved_clarify_items=[],
        ),
    )

    response = asyncio.run(
        bridge._postprocess_turn(
            runner=runner,
            step_out=step_out,
            message=Message(channel="console", target="user", body="what time is it?"),
            history=[],
            session_id="s-tool-batch",
            request_id="trace-tool-batch",
            turn_id="turn-3",
            turn_start_time=0.0,
        )
    )

    assert response.metadata["finish_reason"] == "stop"
    assert response.metadata["tool_execution_count"] == "2"
    assert response.metadata["tool_loop_termination_reason"] == "model_final"
    assert '"tool_name": "time"' in response.metadata["tool_results"]
    assert '"tool_name": "file.read"' in response.metadata["tool_results"]


def test_postprocess_turn_exposes_cumulative_tool_results_from_last_result() -> None:
    bridge = DummyBridge()
    bridge._config = OpenMinionConfig()
    _csc_install_default_agent(bridge._config)
    bridge._provider = SimpleNamespace(name="fake-provider")
    bridge._telemetryctl = _DummyTelemetry()
    bridge._identity_metadata = dict
    runner = SimpleNamespace(session_api=_DummySessionAPI({}))
    prior_result = SimpleNamespace(
        outputs={
            "tool_results": [
                {
                    "tool_name": "file.write",
                    "ok": True,
                    "verified": True,
                    "call_id": "write-1",
                    "content": "wrote pyproject",
                },
                {
                    "tool_name": "file.write",
                    "ok": True,
                    "verified": True,
                    "call_id": "write-2",
                    "content": "wrote tests",
                },
            ]
        }
    )
    step_out = SimpleNamespace(
        message="Project files are present.",
        status="done",
        action_result=SimpleNamespace(
            status="success",
            summary="listed files",
            command_id="cmd-list",
            outputs={
                "tool_results": [
                    {
                        "tool_name": "file.list_dir",
                        "ok": True,
                        "verified": True,
                        "call_id": "list-1",
                        "content": "pyproject.toml, tests/",
                    }
                ]
            },
        ),
        working_state=SimpleNamespace(
            plan=SimpleNamespace(steps=[]),
            llm_calls_used=1,
            active_mode_name="act",
            unresolved_clarify_items=[],
            last_result=prior_result,
        ),
    )

    response = asyncio.run(
        bridge._postprocess_turn(
            runner=runner,
            step_out=step_out,
            message=Message(channel="console", target="user", body="continue"),
            history=[],
            session_id="s-cumulative",
            request_id="trace-cumulative",
            turn_id="turn-cumulative",
            turn_start_time=0.0,
        )
    )

    assert response.metadata["tool_execution_count"] == "1"
    assert response.metadata["tool_execution_count_cumulative"] == "3"
    cumulative = json.loads(response.metadata["tool_calls_cumulative"])
    assert [item["tool_name"] for item in cumulative] == [
        "file.write",
        "file.write",
        "file.list_dir",
    ]


def test_postprocess_turn_attaches_watch_outcome_metadata() -> None:
    bridge = DummyBridge()
    bridge._config = OpenMinionConfig()
    _csc_install_default_agent(bridge._config)
    bridge._provider = SimpleNamespace(name="fake-provider")
    bridge._telemetryctl = _DummyTelemetry()
    bridge._identity_metadata = dict
    runner = SimpleNamespace(session_api=_DummySessionAPI({}))
    step_out = SimpleNamespace(
        message="Deployment is unhealthy.",
        status="done",
        action_result=SimpleNamespace(
            status="success",
            summary="Deployment is unhealthy.",
            command_id="cmd-watch",
            outputs={
                "watch.condition_met": True,
                "watch.summary": "Deployment is unhealthy.",
            },
        ),
        working_state=SimpleNamespace(
            plan=SimpleNamespace(steps=[]),
            llm_calls_used=1,
            active_mode_name="act",
            unresolved_clarify_items=[],
        ),
    )

    response = asyncio.run(
        bridge._postprocess_turn(
            runner=runner,
            step_out=step_out,
            message=Message(channel="console", target="user", body="check it"),
            history=[],
            session_id="s-watch",
            request_id="trace-watch",
            turn_id="turn-watch",
            turn_start_time=0.0,
        )
    )

    assert response.metadata["watch_condition_met"] == "true"
    assert '"condition_met": true' in response.metadata["watch_outcome"]
    assert response.metadata["watch_summary"] == "Deployment is unhealthy."


def test_postprocess_turn_attaches_structured_action_output_metadata() -> None:
    bridge = DummyBridge()
    bridge._config = OpenMinionConfig()
    _csc_install_default_agent(bridge._config)
    bridge._provider = SimpleNamespace(name="fake-provider")
    bridge._telemetryctl = None
    bridge._identity_metadata = dict
    runner = SimpleNamespace(session_api=_DummySessionAPI({}))
    step_out = SimpleNamespace(
        message="## PLAN\n1. Compare tools.\n## TABLE\n...\n## UNCERTAINTIES\n...",
        status="done",
        action_result=SimpleNamespace(
            status="success",
            summary="Comparison complete.",
            command_id="cmd-finalization",
            outputs={
                "adaptive.finalization_status": {
                    "status": "final_answer",
                    "reasoning": "done",
                    "remaining_work": "",
                    "blocking_reason": "",
                },
                "pending_turn_context": {
                    "original_user_request": "finish later",
                    "active_work_summary": "compared tools",
                    "known_context": {"topic": "uv vs pipx"},
                    "missing_fields": [],
                    "artifact_refs": [],
                    "response_preferences": {},
                },
                "session_work_summary": "Compared uv and pipx using official docs.",
            },
        ),
        working_state=SimpleNamespace(
            plan=SimpleNamespace(steps=[]),
            llm_calls_used=1,
            active_mode_name="act",
            unresolved_clarify_items=[],
        ),
    )

    response = asyncio.run(
        bridge._postprocess_turn(
            runner=runner,
            step_out=step_out,
            message=Message(
                channel="console", target="user", body="compare uv and pipx"
            ),
            history=[],
            session_id="s-finalization",
            request_id="trace-finalization",
            turn_id="turn-finalization",
            turn_start_time=0.0,
        )
    )

    assert response.metadata["adaptive.finalization_status"] == (
        '{"blocking_reason": "", "reasoning": "done", "remaining_work": "", "status": "final_answer"}'
    )
    assert (
        '"active_work_summary": "compared tools"'
        in response.metadata["pending_turn_context"]
    )
    assert (
        response.metadata["session_work_summary"]
        == "Compared uv and pipx using official docs."
    )


def test_postprocess_turn_salvages_finalization_status_trailer_from_message() -> None:
    bridge = DummyBridge()
    bridge._config = OpenMinionConfig()
    _csc_install_default_agent(bridge._config)
    bridge._provider = SimpleNamespace(name="fake-provider")
    bridge._telemetryctl = None
    bridge._identity_metadata = dict
    runner = SimpleNamespace(session_api=_DummySessionAPI({}))
    step_out = SimpleNamespace(
        message=(
            "## PLAN\n1. Done.\n"
            '<finalization_status>{"status":"final_answer","reasoning":"done","remaining_work":"","blocking_reason":""}</finalization_status>'
        ),
        status="done",
        action_result=SimpleNamespace(
            status="success",
            summary="Done.",
            command_id="cmd-finalization-trailer",
            outputs={},
        ),
        working_state=SimpleNamespace(
            plan=SimpleNamespace(steps=[]),
            llm_calls_used=1,
            active_mode_name="act",
            unresolved_clarify_items=[],
        ),
    )

    response = asyncio.run(
        bridge._postprocess_turn(
            runner=runner,
            step_out=step_out,
            message=Message(channel="console", target="user", body="do work"),
            history=[],
            session_id="s-finalization-trailer",
            request_id="trace-finalization-trailer",
            turn_id="turn-finalization-trailer",
            turn_start_time=0.0,
        )
    )

    assert "<finalization_status>" not in response.text
    assert response.metadata["adaptive.finalization_status"] == (
        '{"blocking_reason": "", "reasoning": "done", "remaining_work": "", "status": "final_answer"}'
    )


def test_postprocess_turn_salvages_attribute_style_finalization_status_trailer() -> (
    None
):
    bridge = DummyBridge()
    bridge._config = OpenMinionConfig()
    _csc_install_default_agent(bridge._config)
    bridge._provider = SimpleNamespace(name="fake-provider")
    bridge._telemetryctl = None
    bridge._identity_metadata = dict
    runner = SimpleNamespace(session_api=_DummySessionAPI({}))
    step_out = SimpleNamespace(
        message=(
            "## PLAN\n1. Done.\n"
            '<finalization_status status="final_answer"</finalization_status>'
        ),
        status="done",
        action_result=SimpleNamespace(
            status="success",
            summary="Done.",
            command_id="cmd-finalization-attr-trailer",
            outputs={},
        ),
        working_state=SimpleNamespace(
            plan=SimpleNamespace(steps=[]),
            llm_calls_used=1,
            active_mode_name="act",
            unresolved_clarify_items=[],
        ),
    )

    response = asyncio.run(
        bridge._postprocess_turn(
            runner=runner,
            step_out=step_out,
            message=Message(channel="console", target="user", body="do work"),
            history=[],
            session_id="s-finalization-attr-trailer",
            request_id="trace-finalization-attr-trailer",
            turn_id="turn-finalization-attr-trailer",
            turn_start_time=0.0,
        )
    )

    assert "<finalization_status" not in response.text
    assert response.metadata["adaptive.finalization_status"] == (
        '{"blocking_reason": "", "reasoning": "", "remaining_work": "", "status": "final_answer"}'
    )


def test_postprocess_turn_salvages_summary_alias_finalization_status_trailer() -> None:
    bridge = DummyBridge()
    bridge._config = OpenMinionConfig()
    _csc_install_default_agent(bridge._config)
    bridge._provider = SimpleNamespace(name="fake-provider")
    bridge._telemetryctl = None
    bridge._identity_metadata = dict
    runner = SimpleNamespace(session_api=_DummySessionAPI({}))
    step_out = SimpleNamespace(
        message=(
            "## SOURCES\n- official docs checked.\n"
            '<finalization_status>{"status":"final_answer","summary":"official sources reviewed"}</finalization_status>'
        ),
        status="done",
        action_result=SimpleNamespace(
            status="success",
            summary="Done.",
            command_id="cmd-finalization-summary-alias",
            outputs={},
        ),
        working_state=SimpleNamespace(
            plan=SimpleNamespace(steps=[]),
            llm_calls_used=1,
            active_mode_name="act",
            unresolved_clarify_items=[],
        ),
    )

    response = asyncio.run(
        bridge._postprocess_turn(
            runner=runner,
            step_out=step_out,
            message=Message(channel="console", target="user", body="do work"),
            history=[],
            session_id="s-finalization-summary-alias",
            request_id="trace-finalization-summary-alias",
            turn_id="turn-finalization-summary-alias",
            turn_start_time=0.0,
        )
    )

    assert "<finalization_status>" not in response.text
    assert response.metadata["adaptive.finalization_status"] == (
        '{"blocking_reason": "", "reasoning": "official sources reviewed", "remaining_work": "", "status": "final_answer"}'
    )


def test_tool_result_response_text_appends_search_source_from_structured_results() -> (
    None
):
    response_text = _tool_result_response_text(
        response_text="Here are the latest headlines about Iran.",
        tool_results_payload=[
            {
                "tool_name": "web.search",
                "ok": True,
                "verified": True,
                "content": 'Web search for "iran" returned 3 result(s).',
                "data": {"query": "iran", "results": [], "source": "tavily"},
                "source": "native",
            }
        ],
    )

    assert response_text.endswith("source=tavily")


def test_tool_result_response_text_preserves_existing_search_source_marker() -> None:
    response_text = _tool_result_response_text(
        response_text="Here are the latest headlines.\n\nsource=tavily",
        tool_results_payload=[
            {
                "tool_name": "web.search",
                "ok": True,
                "verified": True,
                "content": "search body",
                "data": {"query": "iran", "results": [], "source": "tavily"},
                "source": "native",
            }
        ],
    )

    assert response_text.count("source=tavily") == 1


def test_tool_result_response_text_uses_literal_search_source_fallback() -> None:
    response_text = _tool_result_response_text(
        response_text="Here are the latest headlines about Iran.",
        tool_results_payload=[
            {
                "tool_name": "web.search",
                "ok": True,
                "verified": True,
                "content": 'Web search for "iran" returned 3 result(s).\nsource=brave',
                "data": {"query": "iran", "results": []},
                "source": "fallback",
            }
        ],
    )

    assert response_text.endswith("source=brave")


def test_tool_result_response_text_leaves_non_search_turns_unchanged() -> None:
    response_text = _tool_result_response_text(
        response_text="The current time in UTC is 12:00.",
        tool_results_payload=[
            {
                "tool_name": "time",
                "ok": True,
                "verified": True,
                "content": "2026-04-11T12:00:00Z",
                "data": {"timezone": "UTC"},
                "source": "native",
            }
        ],
    )

    assert response_text == "The current time in UTC is 12:00."


def test_tool_result_response_text_renders_stable_multi_provider_marker() -> None:
    response_text = _tool_result_response_text(
        response_text="Here are the latest headlines about Iran.",
        tool_results_payload=[
            {
                "tool_name": "web.search",
                "ok": True,
                "verified": True,
                "content": "search body",
                "data": {"query": "iran", "results": [], "source": "tavily"},
                "source": "native",
            },
            {
                "tool_name": "web.search",
                "ok": True,
                "verified": True,
                "content": "search body",
                "data": {"query": "iran", "results": [], "source": "brave"},
                "source": "native",
            },
        ],
    )

    assert response_text.endswith("source=brave,tavily")


def test_looks_like_embedded_tool_call_detects_blocked_envelope_marker() -> None:
    bridge = DummyBridge()
    assert bridge._looks_like_embedded_tool_call(
        "[system: UNEXECUTABLE_TOOL_ENVELOPE] blocked"
    )
    assert not bridge._looks_like_embedded_tool_call(
        "The phrase unexecutable_tool_envelope appears in this explanation only."
    )


def test_looks_like_embedded_tool_call_detects_raw_minimax_markup() -> None:
    bridge = DummyBridge()
    assert bridge._looks_like_embedded_tool_call(
        '<minimax:tool_call><invoke name="web.search"></invoke></minimax:tool_call>'
    )


def test_postprocess_turn_emits_mode_aware_tool_and_tick_telemetry() -> None:
    bridge = DummyBridge()
    bridge._config = SimpleNamespace(
        agent=SimpleNamespace(name="agent"),
        agents={"agent": SimpleNamespace(name="agent")},
        default_agent="agent",
        runtime=SimpleNamespace(env={}),
    )
    bridge._provider = SimpleNamespace(name="provider")
    bridge._telemetryctl = _DummyTelemetry()
    bridge._identity_metadata = dict
    bridge._resolve_command = lambda *, step_out: {  # type: ignore[assignment]
        "kind": "tool",
        "tool_name": "weather",
    }
    runner = SimpleNamespace(session_api=SimpleNamespace(list_events=lambda _sid: []))
    step_out = SimpleNamespace(
        status="done",
        message="completed.",
        action_result=SimpleNamespace(
            summary="It is 72F and sunny.",
            status="success",
            command_id="cmd-1",
            data={},
            outputs={},
        ),
        working_state=SimpleNamespace(
            llm_calls_used=1,
            active_mode_name="plan",
            trace_id="trace-1",
        ),
    )
    message = Message(channel="console", target="user", body="weather in sf")

    response = asyncio.run(
        bridge._postprocess_turn(
            runner=runner,
            step_out=step_out,
            message=message,
            history=[],
            session_id="sess-1",
            request_id="trace-1",
            turn_id="turn-1",
            turn_start_time=0.0,
        )
    )

    assert "It is 72F and sunny." in response.text
    assert (
        "tool_call",
        "sess-1",
        "turn-1",
        "weather",
        True,
        "plan",
    ) in bridge._telemetryctl.events
    assert any(
        event[0] == "tick"
        and event[1] == "sess-1"
        and event[2] == "turn-1"
        and event[3] == "plan"
        for event in bridge._telemetryctl.events
    )


def _fael_make_step_out(message_text: str) -> SimpleNamespace:
    return SimpleNamespace(
        message=message_text,
        status="done",
        action_result=SimpleNamespace(
            status="success",
            summary="Done.",
            command_id="cmd-fael",
            outputs={},
        ),
        working_state=SimpleNamespace(
            plan=SimpleNamespace(steps=[]),
            llm_calls_used=1,
            active_mode_name="act",
            unresolved_clarify_items=[],
        ),
    )


def test_postprocess_turn_unwraps_final_answer_envelope() -> None:
    bridge = DummyBridge()
    bridge._config = OpenMinionConfig()
    _csc_install_default_agent(bridge._config)
    bridge._provider = SimpleNamespace(name="fake-provider")
    bridge._telemetryctl = None
    bridge._identity_metadata = dict
    runner = SimpleNamespace(session_api=_DummySessionAPI({}))
    envelope_body = json.dumps(
        {
            "status": "final_answer",
            "summary": "Answered weather question with stored Austin default.",
            "output": "It is 68F and partly cloudy in Austin today.",
        }
    )
    step_out = _fael_make_step_out(envelope_body)

    response = asyncio.run(
        bridge._postprocess_turn(
            runner=runner,
            step_out=step_out,
            message=Message(channel="console", target="user", body="weather"),
            history=[],
            session_id="s-fael-unwrap",
            request_id="trace-fael-unwrap",
            turn_id="turn-fael-unwrap",
            turn_start_time=0.0,
        )
    )

    # The CLI/transcript surface must see ONLY the `output` text, never the
    # raw JSON envelope.
    assert "{" not in response.text
    assert "status" not in response.text
    assert response.text.endswith("It is 68F and partly cloudy in Austin today.")
    # The structured payload is preserved via the existing metadata channel
    # so downstream consumers can still react to status.
    payload = json.loads(response.metadata["adaptive.finalization_status"])
    assert payload["status"] == "final_answer"
    assert payload["reasoning"] == (
        "Answered weather question with stored Austin default."
    )


def test_postprocess_turn_preserves_plain_text_body() -> None:
    bridge = DummyBridge()
    bridge._config = OpenMinionConfig()
    _csc_install_default_agent(bridge._config)
    bridge._provider = SimpleNamespace(name="fake-provider")
    bridge._telemetryctl = None
    bridge._identity_metadata = dict
    runner = SimpleNamespace(session_api=_DummySessionAPI({}))
    plain_body = "It is 68F and partly cloudy in Austin today."
    step_out = _fael_make_step_out(plain_body)

    response = asyncio.run(
        bridge._postprocess_turn(
            runner=runner,
            step_out=step_out,
            message=Message(channel="console", target="user", body="weather"),
            history=[],
            session_id="s-fael-plain",
            request_id="trace-fael-plain",
            turn_id="turn-fael-plain",
            turn_start_time=0.0,
        )
    )

    assert response.text.endswith(plain_body)
    # No envelope was unwrapped, so finalization metadata must not be invented.
    assert "adaptive.finalization_status" not in response.metadata


def test_postprocess_turn_preserves_nonmatching_json_body() -> None:
    # A JSON object that does not match the exact schema must be left alone.
    bridge = DummyBridge()
    bridge._config = OpenMinionConfig()
    _csc_install_default_agent(bridge._config)
    bridge._provider = SimpleNamespace(name="fake-provider")
    bridge._telemetryctl = None
    bridge._identity_metadata = dict
    runner = SimpleNamespace(session_api=_DummySessionAPI({}))
    non_matching_body = json.dumps(
        {"status": "final_answer", "output": "missing summary key"}
    )
    step_out = _fael_make_step_out(non_matching_body)

    response = asyncio.run(
        bridge._postprocess_turn(
            runner=runner,
            step_out=step_out,
            message=Message(channel="console", target="user", body="weather"),
            history=[],
            session_id="s-fael-nonmatch",
            request_id="trace-fael-nonmatch",
            turn_id="turn-fael-nonmatch",
            turn_start_time=0.0,
        )
    )

    # Body preserved verbatim; no unwrap happened.
    assert non_matching_body in response.text
    assert "adaptive.finalization_status" not in response.metadata


def test_postprocess_turn_unwraps_incomplete_envelope() -> None:
    # Same contract holds for `incomplete` and `blocked` statuses.
    bridge = DummyBridge()
    bridge._config = OpenMinionConfig()
    _csc_install_default_agent(bridge._config)
    bridge._provider = SimpleNamespace(name="fake-provider")
    bridge._telemetryctl = None
    bridge._identity_metadata = dict
    runner = SimpleNamespace(session_api=_DummySessionAPI({}))
    envelope_body = json.dumps(
        {
            "status": "incomplete",
            "summary": "Awaiting tool data.",
            "output": "Still working on the weather lookup.",
        }
    )
    step_out = _fael_make_step_out(envelope_body)

    response = asyncio.run(
        bridge._postprocess_turn(
            runner=runner,
            step_out=step_out,
            message=Message(channel="console", target="user", body="weather"),
            history=[],
            session_id="s-fael-incomplete",
            request_id="trace-fael-incomplete",
            turn_id="turn-fael-incomplete",
            turn_start_time=0.0,
        )
    )

    assert response.text.endswith("Still working on the weather lookup.")
    assert "{" not in response.text
    payload = json.loads(response.metadata["adaptive.finalization_status"])
    assert payload["status"] == "incomplete"
