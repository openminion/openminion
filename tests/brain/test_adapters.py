from __future__ import annotations

import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from openminion.modules.brain.adapters.llm import _extract_structured_output
from openminion.modules.brain.interfaces import (
    BRAIN_ADAPTER_INTERFACE_VERSION,
    BRAIN_RUNNER_INTERFACE_VERSION,
    ensure_adapter_compatibility,
    ensure_runner_compatibility,
)
from openminion.modules.brain.adapters.session import LocalSessionStore
from openminion.modules.brain.adapters.context import LocalContextAdapter
from openminion.modules.brain.runner import BrainRunner
from openminion.modules.brain.schemas import (
    AgentBudgets,
    AgentProfile,
    BudgetCounters,
    DecisionAdapter,
    LLMProfiles,
    Plan,
    WorkingState,
)
from openminion.modules.brain.schemas.simple import (
    _ActPayload,
)
from openminion.modules.telemetry.trace.structured import trace_context_payload
from openminion.modules.telemetry.trace.layout import resolve_trace_root
from tests.brain.runner_test_support import (
    fake_context_pack,
    fake_context_service,
    fake_llm_client,
)


def _find_retry_message(messages: list[object]) -> str:
    for message in messages:
        role = str(getattr(message, "role", "")).strip().lower()
        content = str(getattr(message, "content", "")).strip()
        if role == "system" and content:
            return content
    raise AssertionError("missing retry system message")


class LocalSessionStoreTests(unittest.TestCase):
    def test_working_state_serialization_and_versioning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = LocalSessionStore(root)

            state1 = WorkingState(
                session_id="session_1",
                agent_id="agent_1",
                budgets_remaining=BudgetCounters(
                    ticks=10, tool_calls=5, a2a_calls=5, tokens=1000, time_ms=10000
                ),
                status="active",
                trace_id="t1",
            )

            v1 = store.put_working_state(
                "session_1", state_inline=state1.model_dump(mode="json")
            )
            self.assertEqual(v1, 1)

            latest = store.get_latest_working_state("session_1")
            self.assertIsNotNone(latest)
            assert latest is not None
            self.assertEqual(latest["version"], 1)
            self.assertEqual(latest["state_inline"]["status"], "active")

            state2 = state1.model_copy(update={"status": "waiting_user"})
            v2 = store.put_working_state(
                "session_1", state_inline=state2.model_dump(mode="json")
            )
            self.assertEqual(v2, 2)

            latest2 = store.get_latest_working_state("session_1")
            self.assertIsNotNone(latest2)
            assert latest2 is not None
            self.assertEqual(latest2["version"], 2)
            self.assertEqual(latest2["state_inline"]["status"], "waiting_user")

            reloaded_state = WorkingState.model_validate(latest2["state_inline"])
            self.assertEqual(reloaded_state.status, "waiting_user")
            self.assertEqual(reloaded_state.budgets_remaining.tokens, 1000)

    def test_extract_structured_output_accepts_fallback_submit_output_tool_calls(
        self,
    ) -> None:
        response = SimpleNamespace(
            tool_calls=[
                SimpleNamespace(
                    name="submit_output",
                    arguments={
                        "mode": "respond",
                        "confidence": 1.0,
                        "reason_code": "greeting",
                        "respond_kind": "answer",
                        "sub_intents": [],
                        "rationale": "",
                        "answer": "hello",
                    },
                    status="fallback",
                )
            ],
            output_text="",
        )

        parsed = _extract_structured_output(response, DecisionAdapter)

        self.assertIsInstance(parsed, dict)
        assert isinstance(parsed, dict)
        self.assertEqual(parsed.get("route"), "respond")

    def test_extract_structured_output_rejects_incomplete_act_payload(
        self,
    ) -> None:
        response = SimpleNamespace(
            provider="openrouter",
            model="openai/gpt-4o",
            session_id="default",
            tool_calls=[
                SimpleNamespace(
                    name="submit_output",
                    arguments={
                        "mode": "act",
                        "confidence": 0.95,
                        "reason_code": "web_search_simple",
                        "act_profile": "general",
                        "sub_intents": ["fetch_latest_news"],
                        "rationale": "Search the latest news on Iran.",
                        "tool_name": "web.search",
                    },
                    status="parsed",
                )
            ],
            output_text="",
        )

        parsed = _extract_structured_output(response, DecisionAdapter)

        self.assertIsNone(parsed)

    def test_extract_structured_output_unwraps_nested_arguments_inside_args(
        self,
    ) -> None:
        response = SimpleNamespace(
            provider="openrouter",
            model="openai/gpt-5.4",
            session_id="default",
            tool_calls=[
                SimpleNamespace(
                    name="submit_output",
                    arguments={
                        "objective": "Fetch example domain",
                        "steps": [
                            {
                                "kind": "tool",
                                "title": "Fetch page",
                                "tool_name": "web.fetch",
                                "args": {
                                    "arguments": {
                                        "url": "https://example.com",
                                    }
                                },
                                "success_criteria": {"status": "success"},
                            }
                        ],
                        "stop_conditions": ["done"],
                        "assumptions": [],
                        "risk_summary": "low",
                        "success_criteria": {"status": "success"},
                    },
                    status="parsed",
                )
            ],
            output_text="",
        )

        parsed = _extract_structured_output(response, Plan)

        self.assertIsInstance(parsed, dict)
        assert isinstance(parsed, dict)
        step = parsed["steps"][0]
        self.assertEqual(step["tool_name"], "web.fetch")
        self.assertEqual(step["args"], {"url": "https://example.com"})

    def test_extract_structured_output_rejects_act_payload_missing_profile(
        self,
    ) -> None:
        response = SimpleNamespace(
            provider="openai",
            model="MiniMax-M2.5",
            session_id="default",
            tool_calls=[
                SimpleNamespace(
                    name="submit_output",
                    arguments={
                        "mode": "act",
                        "confidence": 0.95,
                        "reason_code": "simple_factual_query",
                        "execution_target": {"kind": "local"},
                        "sub_intents": ["fetch_iran_news"],
                        "rationale": (
                            "User wants current news on Iran - a single web "
                            "search can retrieve the latest headlines and "
                            "developments."
                        ),
                        "tool_name": "web.search",
                    },
                    status="parsed",
                )
            ],
            output_text="",
        )

        parsed = _extract_structured_output(response, DecisionAdapter)

        self.assertIsNone(parsed)

    def test_extract_structured_output_rejects_act_payload_with_string_target(
        self,
    ) -> None:
        response = SimpleNamespace(
            provider="openrouter",
            model="openai/gpt-4o",
            session_id="default",
            tool_calls=[
                SimpleNamespace(
                    name="submit_output",
                    arguments={
                        "mode": "act",
                        "confidence": 0.95,
                        "reason_code": "simple_weather",
                        "act_profile": "general",
                        "execution_target": "local",
                        "sub_intents": ["fetch_weather"],
                        "rationale": "One tool call is enough.",
                    },
                    status="parsed",
                )
            ],
            output_text="",
        )

        parsed = _extract_structured_output(response, DecisionAdapter)

        self.assertIsNone(parsed)

    def test_extract_structured_output_writes_ordered_structured_trace_sidecar(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["OPENMINION_TRACE_REQUESTS"] = "1"
            response = SimpleNamespace(
                provider="openai",
                model="gpt-5.4",
                tool_calls=[
                    SimpleNamespace(
                        name="submit_output",
                        arguments={
                            "mode": "respond",
                            "confidence": 0.8,
                            "reason_code": "invalid_first",
                            "sub_intents": [],
                            "rationale": "",
                        },
                        status="parsed",
                    )
                ],
                output_text=json.dumps(
                    {
                        "mode": "respond",
                        "confidence": 0.8,
                        "reason_code": "json_body",
                        "respond_kind": "answer",
                        "sub_intents": [],
                        "rationale": "",
                        "answer": "hello",
                    }
                ),
            )
            trace_context = trace_context_payload(
                session_id="sess",
                turn_id="turn",
                inference_step=1,
                label="call01",
                provider="openai",
                model="gpt-5.4",
                home_root=Path(tmp),
            )

            parsed = _extract_structured_output(
                response,
                DecisionAdapter,
                trace_context=trace_context,
            )

            self.assertIsInstance(parsed, dict)
            trace_path = resolve_trace_root(home_root=Path(tmp)) / str(
                trace_context["structured_trace_filename"]
            )
            self.assertTrue(trace_path.exists())
            payload = json.loads(trace_path.read_text(encoding="utf-8"))
            attempts = payload["extraction_attempts"]
            self.assertEqual(
                [item["strategy"] for item in attempts], ["tool_calls", "json_body"]
            )
            self.assertEqual(attempts[0]["outcome"], "validation_failed")
            self.assertEqual(
                attempts[0]["validation_errors"][0]["path"], "respond_kind"
            )
            self.assertTrue(attempts[0]["validation_errors"][0]["category"])
            self.assertEqual(attempts[1]["outcome"], "validated")
            self.assertEqual(payload["selected_extraction_strategy"], "json_body")

    def test_extract_structured_output_rejects_act_payload_with_invalid_target_mapping(
        self,
    ) -> None:
        response = SimpleNamespace(
            provider="openrouter",
            model="openai/gpt-4o-mini",
            session_id="default",
            tool_calls=[
                SimpleNamespace(
                    name="submit_output",
                    arguments={
                        "mode": "act",
                        "confidence": 0.92,
                        "reason_code": "latest_news",
                        "act_profile": "general",
                        "execution_target": {"target_agent_id": "agent.weather"},
                        "sub_intents": ["search_news"],
                        "rationale": "Search once for current headlines.",
                    },
                    status="parsed",
                )
            ],
            output_text="",
        )

        parsed = _extract_structured_output(response, DecisionAdapter)

        self.assertIsNone(parsed)

    def test_extract_structured_output_rejects_act_payload_with_list_target(
        self,
    ) -> None:
        response = SimpleNamespace(
            provider="openrouter",
            model="google/gemini-2.5-pro",
            session_id="default",
            tool_calls=[
                SimpleNamespace(
                    name="submit_output",
                    arguments={
                        "mode": "act",
                        "confidence": 0.8,
                        "reason_code": "one_step_search",
                        "act_profile": "general",
                        "execution_target": ["local"],
                        "sub_intents": ["web_search", "summarize"],
                        "rationale": "A single web search can gather the latest news.",
                    },
                    status="parsed",
                )
            ],
            output_text="",
        )

        parsed = _extract_structured_output(response, DecisionAdapter)

        self.assertIsNone(parsed)

    def test_extract_structured_output_accepts_minimal_act_payload_shape_without_llm_strategy(
        self,
    ) -> None:
        response = SimpleNamespace(
            provider="openrouter",
            model="openai/gpt-5.4-nano",
            session_id="default",
            tool_calls=[
                SimpleNamespace(
                    name="submit_output",
                    arguments={
                        "title": "Get San Francisco weather",
                        "tool_name": "weather",
                        "args": {"location": "San Francisco"},
                    },
                    status="parsed",
                )
            ],
            output_text="",
        )

        parsed = _extract_structured_output(response, _ActPayload)

        self.assertEqual(
            parsed,
            {
                "act_profile": None,
                "execution_target": None,
                "max_steps_hint": None,
                "rationale": "",
                "subtasks": [],
            },
        )

    def test_extract_structured_output_rejects_decision_payload_missing_target(
        self,
    ) -> None:
        response = SimpleNamespace(
            provider="openrouter",
            model="openai/gpt-5.4",
            session_id="default",
            tool_calls=[
                SimpleNamespace(
                    name="submit_output",
                    arguments={
                        "mode": "act",
                        "confidence": 0.91,
                        "reason_code": "time_now",
                        "act_profile": "general",
                        "sub_intents": ["time_now"],
                        "rationale": "One time tool call can answer the request.",
                        "tool_name": "time",
                        "args": {},
                    },
                    status="parsed",
                )
            ],
            output_text="",
        )

        parsed = _extract_structured_output(response, DecisionAdapter)

        self.assertIsNone(parsed)

    def test_extract_structured_output_keeps_minimal_act_payload_when_tool_fields_are_extra(
        self,
    ) -> None:
        response = SimpleNamespace(
            provider="openrouter",
            model="openai/gpt-5.4",
            session_id="default",
            tool_calls=[
                SimpleNamespace(
                    name="submit_output",
                    arguments={
                        "mode": "act",
                        "confidence": 0.88,
                        "reason_code": "weather_now",
                        "act_profile": "general",
                        "execution_target": {"kind": "local"},
                        "sub_intents": ["weather_now"],
                        "rationale": "One weather call is enough.",
                        "tool_name": "weather",
                        "args": {"location": "Tokyo"},
                    },
                    status="parsed",
                )
            ],
            output_text="",
        )

        parsed = _extract_structured_output(response, _ActPayload)

        self.assertEqual(
            parsed,
            {
                "act_profile": "general",
                "execution_target": {
                    "kind": "local",
                    "target_agent_id": "",
                    "target_capability": "",
                    "expect_async": False,
                },
                "max_steps_hint": None,
                "rationale": "One weather call is enough.",
                "subtasks": [],
            },
        )

    def test_append_turn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = LocalSessionStore(root)

            t1 = store.append_turn("session_2", "user", "help")
            t2 = store.append_turn("session_2", "assistant", "what?")

            turns = store.list_turns("session_2")
            self.assertEqual(len(turns), 2)
            self.assertEqual(turns[0]["turn_id"], t1)
            self.assertEqual(turns[0]["role"], "user")
            self.assertEqual(turns[0]["content"], "help")
            self.assertEqual(turns[1]["turn_id"], t2)

    def test_append_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = LocalSessionStore(root)

            e1 = store.append_event("session_3", "brain.decide", {"mode": "plan"})

            events = store.list_events("session_3")
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["event_id"], e1)
            self.assertEqual(events[0]["type"], "brain.decide")
            self.assertEqual(events[0]["payload"]["mode"], "plan")

    def test_get_slice_includes_recent_turns_and_active_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = LocalSessionStore(root)

            store.append_turn("session_ctx", "user", "hello")
            store.append_turn("session_ctx", "assistant", "hi there")
            state = WorkingState(
                session_id="session_ctx",
                agent_id="agent_1",
                budgets_remaining=BudgetCounters(
                    ticks=10, tool_calls=5, a2a_calls=5, tokens=1000, time_ms=10000
                ),
                status="active",
                trace_id="trace-1",
                open_questions=["next?"],
            )
            store.put_working_state(
                "session_ctx", state_inline=state.model_dump(mode="json")
            )

            slice_payload = store.get_slice("session_ctx", "decide", {"max_turns": 8})
            self.assertTrue(str(slice_payload["slice_version"]).startswith("local:"))
            self.assertEqual(len(slice_payload["recent_turns"]), 2)
            self.assertEqual(slice_payload["recent_turns"][0]["role"], "user")
            self.assertEqual(slice_payload["recent_turns"][1]["role"], "assistant")
            self.assertIsInstance(slice_payload.get("active_state"), dict)
            self.assertEqual(slice_payload["open_tasks"], ["next?"])


class ContextAndLLMAdapterTests(unittest.TestCase):
    def test_local_context_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = LocalSessionStore(root)
            ctx = LocalContextAdapter(session_store=store)

            store.append_turn("s1", "user", "input")
            store.append_event("s1", "brain.interpret", {"x": 1})

            built = ctx.build(
                session_id="s1",
                agent_id="a1",
                purpose="decide",
                budget={"max_tokens": 1000},
                hints={"h": "hi"},
            )
            self.assertEqual(built["session_id"], "s1")
            self.assertEqual(built["agent_id"], "a1")
            self.assertEqual(built["purpose"], "decide")
            self.assertEqual(built["hints"]["h"], "hi")
            self.assertEqual(len(built["turns"]), 1)
            self.assertEqual(len(built["events"]), 1)

            delta = ctx.make_delta(session_id="s1", agent_id="a1")
            self.assertTrue(delta.startswith("delta://s1/a1/"))

    def test_local_llm_adapter(self) -> None:
        from openminion.modules.brain.adapters.llm import LocalLLMAdapter
        from openminion.modules.brain.schemas import DecisionAdapter

        llm = LocalLLMAdapter()
        est = llm.estimate_tokens(model="mock", context={})
        self.assertEqual(est, 50)

        dec_payload = llm.call_structured(
            model="mock",
            purpose="decide",
            context={"hints": {"user_input": "plan something"}},
            schema=DecisionAdapter,
        )
        decision = DecisionAdapter.validate_python(dec_payload)
        self.assertEqual(decision.mode, "respond")

        plan_payload = llm.call_structured(
            model="mock", purpose="plan", context={}, schema=Plan
        )
        plan = Plan.model_validate(plan_payload)
        self.assertEqual(plan.objective, "mock_plan_objective")
        self.assertEqual(len(plan.steps), 1)


class ToolAdapterTests(unittest.TestCase):
    def test_local_tool_adapter(self) -> None:
        from openminion.modules.brain.adapters.tool import LocalToolAdapter

        adapter = LocalToolAdapter()

        res_echo = adapter.execute(
            command={"tool_name": "echo", "args": {"x": 1}},
            session_id="s1",
            trace_id="t1",
        )
        self.assertEqual(res_echo["status"], "success")
        self.assertEqual(res_echo["outputs"]["echo"]["x"], 1)

        res_fail = adapter.execute(
            command={"tool_name": "fail"}, session_id="s1", trace_id="t1"
        )
        self.assertEqual(res_fail["status"], "failed")

        res_art = adapter.execute(
            command={"tool_name": "create_artifact"}, session_id="s1", trace_id="t1"
        )
        self.assertEqual(res_art["status"], "success")
        self.assertEqual(len(res_art["artifact_refs"]), 1)
        self.assertEqual(res_art["artifact_refs"][0]["ref"], "art_123")


class A2AAndPolicyAdapterTests(unittest.TestCase):
    def test_local_a2a_adapter(self) -> None:
        from openminion.modules.brain.adapters.a2a import LocalA2AAdapter

        adapter = LocalA2AAdapter()

        res_sync = adapter.call(
            command={"target_agent_id": "agent2", "method": "do_work"},
            session_id="s1",
            trace_id="t1",
        )
        self.assertEqual(res_sync["status"], "success")
        self.assertEqual(res_sync["outputs"]["target_agent_id"], "agent2")

        res_async = adapter.call(
            command={"expect_async": True}, session_id="s1", trace_id="t1"
        )
        self.assertEqual(res_async["status"], "running")
        self.assertIn("task_id", res_async)

    def test_local_policy_adapter(self) -> None:
        from openminion.modules.brain.adapters.policy import LocalPolicyAdapter
        from openminion.modules.brain.schemas import (
            ToolCommand,
            WorkingState,
            BudgetCounters,
        )

        adapter = LocalPolicyAdapter()

        state = WorkingState(
            session_id="s1",
            agent_id="a1",
            budgets_remaining=BudgetCounters(
                ticks=10, tool_calls=5, a2a_calls=5, tokens=100, time_ms=1000
            ),
            status="active",
            trace_id="t1",
        )

        cmd_high = ToolCommand(
            title="x",
            tool_name="echo",
            args={},
            risk_level="high",
            idempotency_key="i1",
            success_criteria={},
        )
        res_high = adapter.evaluate(
            command=cmd_high, working_state=state, session_context={}
        )
        self.assertEqual(res_high.outcome, "REQUIRE_CONFIRMATION")

        cmd_rm = ToolCommand(
            title="x",
            tool_name="rm",
            args={},
            risk_level="low",
            idempotency_key="i2",
            success_criteria={},
        )
        res_rm = adapter.evaluate(
            command=cmd_rm, working_state=state, session_context={}
        )
        self.assertEqual(res_rm.outcome, "DENY")

        cmd_trav = ToolCommand(
            title="x",
            tool_name="cat",
            args={},
            cwd="../secret",
            risk_level="low",
            idempotency_key="i3",
            success_criteria={},
        )
        res_trav = adapter.evaluate(
            command=cmd_trav, working_state=state, session_context={}
        )
        self.assertEqual(res_trav.outcome, "MODIFY")
        self.assertIsNotNone(res_trav.patched_command)
        assert res_trav.patched_command is not None
        self.assertEqual(getattr(res_trav.patched_command, "cwd"), "/secret")

        # Test allow
        cmd_ok = ToolCommand(
            title="x",
            tool_name="cat",
            args={},
            cwd="/tmp",
            risk_level="low",
            idempotency_key="i4",
            success_criteria={},
        )
        res_ok = adapter.evaluate(
            command=cmd_ok, working_state=state, session_context={}
        )
        self.assertEqual(res_ok.outcome, "ALLOW")


class MemoryAdapterTests(unittest.TestCase):
    def test_local_memory_adapter(self) -> None:
        from openminion.modules.brain.adapters.memory import LocalMemoryAdapter

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            adapter = LocalMemoryAdapter(root)

            r_id = adapter.put_record(
                scope="brain", record_type="fact", title="t", content={"x": 1}
            )
            self.assertTrue(r_id.startswith("mem_"))

            c_id = adapter.stage_candidate(
                scope="brain", record_type="fact", title="t", content={"x": 2}
            )
            self.assertTrue(c_id.startswith("cand_"))

            lines = (root / "memory.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 2)
            import json

            j1 = json.loads(lines[0])
            self.assertEqual(j1["id"], r_id)
            self.assertEqual(j1["content"]["x"], 1)

            j2 = json.loads(lines[1])
            self.assertEqual(j2["id"], c_id)
            self.assertEqual(j2["content"]["x"], 2)


class SessctlAdapterTests(unittest.TestCase):
    def test_real_session_adapter(self) -> None:
        try:
            importlib.import_module("openminion.modules.session")
        except ImportError:
            self.skipTest("openminion_session not installed")

        from openminion.modules.brain.adapters.factory import create_session_adapter

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "mock.db"
            adapter = create_session_adapter(mode="strict", db_path=db_path)

            sid = adapter.store.create_session()

            adapter.append_turn(sid, "user", "hi")
            adapter.append_event(sid, "brain.interpret", {"msg": "yo"})

            turns = adapter.store.list_turns(sid)
            events = adapter.store.list_events(sid)
            self.assertEqual(len(turns), 1)
            self.assertEqual(len(events), 2)
            self.assertEqual(
                [event.get("type") for event in events],
                ["turn.user", "brain.interpret"],
            )

            adapter.put_working_state(sid, state_inline={"status": "active"})
            ls = adapter.get_latest_working_state(sid)
            assert ls is not None
            self.assertEqual(ls["state_inline"], {"status": "active"})

            adapter.update_summary(
                sid,
                summary_short="short summary",
                summary_long="long summary",
            )
            summary = adapter.store.get_summary(sid)
            self.assertIn("short summary", summary)


class RealCtxAndLlmAdapterTests(unittest.TestCase):
    def test_decision_request_includes_pending_turn_context_guidance(self) -> None:
        from openminion.modules.brain.adapters.llm.request import _build_request

        request = _build_request(
            model="test",
            purpose="decide",
            context={
                "messages": [{"role": "user", "content": "what is your location?"}]
            },
            schema=DecisionAdapter,
            temperature=0.0,
        )

        system_text = "\n".join(
            str(message.content)
            for message in request.messages
            if str(message.role).lower() == "system"
        )
        self.assertIn("Decision.pending_turn_context", system_text)
        self.assertIn("active_work_summary", system_text)
        self.assertIn("detail each day", system_text)
        self.assertIn("break down each step", system_text)
        self.assertIn("latest price", system_text)
        self.assertIn("market cap", system_text)
        self.assertIn("who created it", system_text)
        self.assertNotIn("<pending_turn_context>", system_text)

    def test_pending_turn_context_guidance_is_decision_schema_only(self) -> None:
        from openminion.modules.brain.adapters.llm.request import _build_request

        plan_request = _build_request(
            model="test",
            purpose="plan",
            context={"messages": [{"role": "user", "content": "make a plan"}]},
            schema=Plan,
            temperature=0.0,
        )
        decide_plan_schema_request = _build_request(
            model="test",
            purpose="decide",
            context={"messages": [{"role": "user", "content": "make a plan"}]},
            schema=Plan,
            temperature=0.0,
        )

        plan_system_text = "\n".join(
            str(message.content)
            for message in plan_request.messages
            if str(message.role).lower() == "system"
        )
        decide_plan_schema_system_text = "\n".join(
            str(message.content)
            for message in decide_plan_schema_request.messages
            if str(message.role).lower() == "system"
        )
        self.assertNotIn("Decision.pending_turn_context", plan_system_text)
        self.assertNotIn(
            "Decision.pending_turn_context",
            decide_plan_schema_system_text,
        )

    def test_decision_request_includes_clarify_context_guidance(self) -> None:
        from openminion.modules.brain.adapters.llm.request import _build_request

        request = _build_request(
            model="test",
            purpose="decide",
            context={"messages": [{"role": "user", "content": "plan my trip"}]},
            schema=DecisionAdapter,
            temperature=0.0,
        )

        system_text = "\n".join(
            str(message.content)
            for message in request.messages
            if str(message.role).lower() == "system"
        )
        self.assertIn("respond_kind", system_text)
        self.assertIn("clarify_context", system_text)
        self.assertIn("clarify_question", system_text)
        self.assertIn("original_user_input", system_text)
        self.assertIn("Set question to the same user-facing", system_text)

    def test_decision_request_includes_research_profile_guidance(self) -> None:
        from openminion.modules.brain.adapters.llm.request import _build_request

        request = _build_request(
            model="test",
            purpose="decide",
            context={
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "check latest iran news and do deep research with at least "
                            "two passes before proposing a stock basket"
                        ),
                    }
                ]
            },
            schema=DecisionAdapter,
            temperature=0.0,
        )

        system_text = "\n".join(
            str(message.content)
            for message in request.messages
            if str(message.role).lower() == "system"
        )
        self.assertIn('act_profile="research"', system_text)
        self.assertIn("deep research", system_text)
        self.assertIn("iterate twice", system_text)
        self.assertIn("multiple searches", system_text)
        self.assertIn("decompose control tool", system_text)
        self.assertIn("independent subtasks", system_text)
        self.assertIn('act_profile="general"', system_text)

    def test_clarify_context_guidance_is_decision_schema_only(self) -> None:
        from openminion.modules.brain.adapters.llm.request import _build_request

        plan_request = _build_request(
            model="test",
            purpose="plan",
            context={"messages": [{"role": "user", "content": "make a plan"}]},
            schema=Plan,
            temperature=0.0,
        )
        decide_plan_schema_request = _build_request(
            model="test",
            purpose="decide",
            context={"messages": [{"role": "user", "content": "make a plan"}]},
            schema=Plan,
            temperature=0.0,
        )

        plan_system_text = "\n".join(
            str(message.content)
            for message in plan_request.messages
            if str(message.role).lower() == "system"
        )
        decide_plan_schema_system_text = "\n".join(
            str(message.content)
            for message in decide_plan_schema_request.messages
            if str(message.role).lower() == "system"
        )
        self.assertNotIn("clarify_context.original_user_input", plan_system_text)
        self.assertNotIn(
            "clarify_context.original_user_input",
            decide_plan_schema_system_text,
        )
        self.assertNotIn('act_profile="research"', plan_system_text)
        self.assertNotIn('act_profile="research"', decide_plan_schema_system_text)

    def test_context_adapter(self) -> None:
        try:
            importlib.import_module("openminion.modules.context.schemas")
        except ImportError:
            self.skipTest("openminion_context not installed")

        from openminion.modules.brain.adapters.context import ContextCtlAdapter

        mock_pack = fake_context_pack({"pack_version": "123"})
        mock_svc = fake_context_service(pack=mock_pack)

        adapter = ContextCtlAdapter(mock_svc)
        res = adapter.build(session_id="s1", agent_id="a1", purpose="decide", budget={})
        self.assertEqual(res, {"pack_version": "123"})
        mock_svc.build_pack.assert_called_once()

    def test_context_adapter_derives_prompt_and_runtime_tools_from_single_bundle(
        self,
    ) -> None:
        from openminion.modules.brain.adapters.context import ContextCtlAdapter

        mock_pack = fake_context_pack({"pack_version": "123"})
        mock_svc = fake_context_service(pack=mock_pack)
        adapter = ContextCtlAdapter(mock_svc)

        raw_runtime = [
            {
                "name": "web.search",
                "description": "Search the web",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                },
            },
            {
                "name": "weather",
                "description": "Get weather",
                "parameters": {
                    "type": "object",
                    "properties": {"location": {"type": "string"}},
                },
            },
        ]
        result = adapter.build(
            session_id="s1",
            agent_id="a1",
            purpose="decide",
            budget={},
            hints={
                "user_input": "weather in san francisco",
                "prompt_tool_schemas_enabled": True,
                "runtime_tool_schemas": raw_runtime,
            },
        )
        self.assertIn("hints", result)
        self.assertEqual(
            sorted(
                item.get("name")
                for item in result["hints"].get("runtime_tool_schemas", [])
            ),
            ["weather", "web.search"],
        )
        self.assertGreaterEqual(len(result["hints"].get("tool_schemas", [])), 1)

        req = mock_svc.build_pack.call_args.args[0]
        self.assertEqual(
            sorted(item.get("name") for item in req.constraints.runtime_tool_schemas),
            ["weather", "web.search"],
        )
        self.assertGreaterEqual(len(req.constraints.tool_schemas), 1)

    def test_context_adapter_forces_prompt_schemas_for_decide_when_runtime_tools_exist(
        self,
    ) -> None:
        from openminion.modules.brain.adapters.context import ContextCtlAdapter

        mock_pack = fake_context_pack({"pack_version": "123"})
        mock_svc = fake_context_service(pack=mock_pack)
        adapter = ContextCtlAdapter(mock_svc)

        adapter.build(
            session_id="s1",
            agent_id="a1",
            purpose="decide",
            budget={},
            hints={
                "user_input": "weather in san francisco",
                "prompt_tool_schemas_enabled": False,
                "runtime_tool_schemas": [
                    {
                        "name": "weather",
                        "description": "Get weather",
                        "parameters": {
                            "type": "object",
                            "properties": {"location": {"type": "string"}},
                        },
                    }
                ],
            },
        )
        req = mock_svc.build_pack.call_args.args[0]
        self.assertEqual(
            [item.get("name") for item in req.constraints.runtime_tool_schemas],
            ["weather"],
        )
        self.assertEqual(
            [item.get("name") for item in req.constraints.tool_schemas], ["weather"]
        )

    def test_context_adapter_respects_prompt_schema_flag_for_chat(self) -> None:
        from openminion.modules.brain.adapters.context import ContextCtlAdapter

        mock_pack = fake_context_pack({"pack_version": "123"})
        mock_svc = fake_context_service(pack=mock_pack)
        adapter = ContextCtlAdapter(mock_svc)

        adapter.build(
            session_id="s1",
            agent_id="a1",
            purpose="chat",
            budget={},
            hints={
                "user_input": "weather in san francisco",
                "prompt_tool_schemas_enabled": False,
                "runtime_tool_schemas": [
                    {
                        "name": "weather",
                        "description": "Get weather",
                        "parameters": {
                            "type": "object",
                            "properties": {"location": {"type": "string"}},
                        },
                    }
                ],
            },
        )
        req = mock_svc.build_pack.call_args.args[0]
        self.assertEqual(
            [item.get("name") for item in req.constraints.runtime_tool_schemas],
            ["weather"],
        )
        self.assertEqual(req.constraints.tool_schemas, [])

    def test_context_adapter_applies_runtime_budget_cap(self) -> None:
        from openminion.modules.brain.adapters.context import ContextCtlAdapter

        mock_pack = fake_context_pack({"pack_version": "123"})
        mock_svc = fake_context_service(pack=mock_pack)
        adapter = ContextCtlAdapter(mock_svc, runtime_token_budget=1200)

        adapter.build(
            session_id="s1",
            agent_id="a1",
            purpose="decide",
            budget={"max_tokens": 3000},
            hints={"user_input": "hello"},
        )

        req = mock_svc.build_pack.call_args.args[0]
        self.assertEqual(req.budgets_override.total_max_tokens, 1200)


class AdapterInterfaceContractTests(unittest.TestCase):
    def test_local_adapters_satisfy_interface_contract(self) -> None:
        from openminion.modules.brain.adapters.a2a import LocalA2AAdapter
        from openminion.modules.brain.adapters.context import LocalContextAdapter
        from openminion.modules.brain.adapters.llm import LocalLLMAdapter
        from openminion.modules.brain.adapters.memory import LocalMemoryAdapter
        from openminion.modules.brain.adapters.policy import LocalPolicyAdapter
        from openminion.modules.brain.adapters.recursive import LocalRLMAdapter
        from openminion.modules.brain.adapters.retrieve import LocalRetrieveAdapter
        from openminion.modules.brain.adapters.session import LocalSessionStore
        from openminion.modules.brain.adapters.tool import LocalToolAdapter

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = LocalSessionStore(root / "sessions")
            context = LocalContextAdapter(session_store=session)
            llm = LocalLLMAdapter()
            tool = LocalToolAdapter()
            a2a = LocalA2AAdapter()
            memory = LocalMemoryAdapter(root / "memory")
            policy = LocalPolicyAdapter()
            rlm = LocalRLMAdapter()
            retrieve = LocalRetrieveAdapter()

            self.assertEqual(session.contract_version, BRAIN_ADAPTER_INTERFACE_VERSION)
            self.assertEqual(context.contract_version, BRAIN_ADAPTER_INTERFACE_VERSION)
            self.assertEqual(llm.contract_version, BRAIN_ADAPTER_INTERFACE_VERSION)
            self.assertEqual(tool.contract_version, BRAIN_ADAPTER_INTERFACE_VERSION)

            ensure_adapter_compatibility(session, adapter_type="session")
            ensure_adapter_compatibility(context, adapter_type="context")
            ensure_adapter_compatibility(llm, adapter_type="llm")
            ensure_adapter_compatibility(tool, adapter_type="tool")
            ensure_adapter_compatibility(a2a, adapter_type="a2a")
            ensure_adapter_compatibility(memory, adapter_type="memory")
            ensure_adapter_compatibility(policy, adapter_type="policy")
            ensure_adapter_compatibility(rlm, adapter_type="rlm")
            ensure_adapter_compatibility(retrieve, adapter_type="retrieve")

    def test_local_llm_skill_selection_requires_explicit_skill_id_mention(self) -> None:
        from openminion.modules.brain.adapters.llm import LocalLLMAdapter
        from openminion.modules.brain.bootstrap.skill.selection import (
            SkillSubsetSelection,
        )

        adapter = LocalLLMAdapter()
        catalog_message = (
            "- github.skill: GitHub workflow support\n"
            "- deploy.skill: Deployment workflow support\n"
        )

        no_match = adapter.call_structured(
            model="local",
            purpose="skill_select",
            context={
                "messages": [
                    {
                        "role": "system",
                        "content": catalog_message
                        + 'User message: "please help me debug pytest failures"',
                    }
                ]
            },
            schema=SkillSubsetSelection,
        )
        self.assertEqual(no_match.get("skill_ids"), [])

        explicit_match = adapter.call_structured(
            model="local",
            purpose="skill_select",
            context={
                "messages": [
                    {
                        "role": "system",
                        "content": catalog_message
                        + 'User message: "use github.skill for this task"',
                    }
                ]
            },
            schema=SkillSubsetSelection,
        )
        self.assertEqual(explicit_match.get("skill_ids"), ["github.skill"])

    def test_local_llm_single_weather_tool_prefers_clarify_tool(self) -> None:
        from openminion.modules.brain.adapters.llm import LocalLLMAdapter
        from openminion.modules.llm.schemas import LLMRequest, Message, ToolSpec

        adapter = LocalLLMAdapter()
        response = adapter.call(
            LLMRequest(
                messages=[Message(role="user", content="weather")],
                model="local",
                tools=[
                    ToolSpec(
                        name="weather", description="Get weather", input_schema={}
                    ),
                    ToolSpec(
                        name="clarify", description="Ask a question", input_schema={}
                    ),
                ],
                metadata={},
            )
        )

        self.assertEqual(len(response.tool_calls), 1)
        self.assertEqual(response.tool_calls[0].name, "clarify")
        self.assertIn(
            "location",
            str(response.tool_calls[0].arguments.get("question", "")).lower(),
        )

    def test_interface_validator_rejects_incompatible_adapter(self) -> None:
        class _Broken:
            contract_version = "v1"

            def build(self):  # pragma: no cover - shape only
                return {}

        with self.assertRaises(TypeError):
            ensure_adapter_compatibility(_Broken(), adapter_type="context")

    def test_runner_contract_validator_accepts_state_machine_runner(self) -> None:
        profile = AgentProfile(
            agent_id="test-agent",
            role="general",
            llm_profiles=LLMProfiles(
                decide_model="test-decide",
                plan_model="test-plan",
                reflect_model="test-reflect",
                summarize_model="test-summarize",
            ),
            budgets=AgentBudgets(
                max_ticks_per_user_turn=10,
                max_tool_calls=10,
                max_a2a_calls=2,
                max_total_llm_tokens=5000,
                max_elapsed_ms=10000,
            ),
        )
        runner = BrainRunner(profile=profile, session_api=MagicMock())
        self.assertEqual(runner.contract_version, BRAIN_RUNNER_INTERFACE_VERSION)
        ensure_runner_compatibility(runner)

    def test_runner_contract_validator_rejects_broken_runner(self) -> None:
        class _BrokenRunner:
            contract_version = "v1"

            def run(
                self,
                *,
                session_id: str,
                user_input: str | None = None,
                trace_id: str | None = None,
            ):
                del session_id, user_input, trace_id
                return {}

        with self.assertRaises(TypeError):
            ensure_runner_compatibility(_BrokenRunner())

    def test_factory_context_adapter_contract_smoke(self) -> None:
        try:
            from openminion.modules.brain.adapters.factory import create_context_adapter
            from openminion.modules.context.service import ContextCtlService  # noqa: F401
        except ImportError:
            self.skipTest("factory adapter not importable")

        class _SessionStore:
            def get_slice(
                self, *, session_id: str, purpose: str, limits: dict[str, int]
            ):
                del purpose, limits
                return {
                    "session_id": session_id,
                    "slice_version": "slice:v1",
                    "summary": {"summary_short": "recent summary"},
                    "recent_turns": [
                        {"turn_id": "t-user", "role": "user", "text": "hello"},
                        {"turn_id": "t-assistant", "role": "assistant", "text": "hi"},
                    ],
                    "open_tasks": [],
                    "active_state": {},
                    "recent_tool_events": [],
                }

        adapter = create_context_adapter(mode="auto", session_store=_SessionStore())
        if isinstance(adapter, LocalContextAdapter):
            self.skipTest("openminion_context not available in this test environment")
        payload = adapter.build(
            session_id="s-ctx",
            agent_id="a-ctx",
            purpose="decide",
            budget={"max_tokens": 512},
            hints={"query": "hello"},
        )

        self.assertEqual(payload.get("session_id"), "s-ctx")
        self.assertIn("messages", payload)
        rendered = "\n".join(
            str(item.get("content", ""))
            for item in payload.get("messages", [])
            if isinstance(item, dict)
        )
        self.assertIn("agent_id=a-ctx", rendered)
        self.assertNotIn("identity_mode=brain-bridge-stub", rendered)

    def test_factory_context_adapter_identity_fallback_emits_warning_and_sentinel(
        self,
    ) -> None:
        try:
            from openminion.modules.brain.adapters.factory import create_context_adapter
            from openminion.modules.context.service import ContextCtlService  # noqa: F401
        except ImportError:
            self.skipTest("factory adapter not importable")

        class _SessionStore:
            def get_slice(
                self, *, session_id: str, purpose: str, limits: dict[str, int]
            ):
                del purpose, limits
                return {
                    "session_id": session_id,
                    "slice_version": "slice:v1",
                    "summary": {"summary_short": "recent summary"},
                    "recent_turns": [],
                    "open_tasks": [],
                    "active_state": {},
                    "recent_tool_events": [],
                }

        with self.assertLogs(
            "openminion.modules.brain.adapters.context.bridges", level="WARNING"
        ) as captured:
            adapter = create_context_adapter(
                mode="auto",
                session_store=_SessionStore(),
                identity_system_prompt="System instruction: fallback prompt",
            )
            if isinstance(adapter, LocalContextAdapter):
                self.skipTest(
                    "openminion_context not available in this test environment"
                )
            payload = adapter.build(
                session_id="s-ctx",
                agent_id="a-ctx",
                purpose="decide",
                budget={"max_tokens": 512},
                hints={"query": "hello"},
            )

        self.assertEqual(payload.get("profile_version"), "brain-bridge:fallback:v1")
        self.assertEqual(payload.get("render_version"), "brain-bridge:fallback:v1")
        self.assertTrue(
            any("identity.bridge_fallback" in entry for entry in captured.output)
        )
        self.assertTrue(
            any(
                "sentinel=brain-bridge:fallback:v1" in entry
                for entry in captured.output
            )
        )

    def test_factory_context_adapter_cold_agent_seeded_without_fallback(self) -> None:
        try:
            from openminion.modules.brain.adapters.factory import create_context_adapter
            from openminion.modules.context.service import ContextCtlService  # noqa: F401
        except ImportError:
            self.skipTest("factory adapter not importable")

        with tempfile.TemporaryDirectory() as tmp:
            openminion_dir = Path(tmp) / ".openminion"
            session_dir = openminion_dir / "session"
            session_dir.mkdir(parents=True)
            session_db = session_dir / "sessions.db"
            session_db.touch()

            class _SessionStore:
                sqlite_path = str(session_db)

                def get_slice(
                    self, *, session_id: str, purpose: str, limits: dict[str, int]
                ):
                    del purpose, limits
                    return {
                        "session_id": session_id,
                        "slice_version": "slice:v1",
                        "summary": {"summary_short": ""},
                        "recent_turns": [],
                        "open_tasks": [],
                        "active_state": {},
                        "recent_tool_events": [],
                    }

            adapter = create_context_adapter(
                mode="auto",
                session_store=_SessionStore(),
                identity_system_prompt="You are a cold agent with no prior profile.",
            )
            if isinstance(adapter, LocalContextAdapter):
                self.skipTest(
                    "openminion_context not available in this test environment"
                )

            with self.assertNoLogs(
                "openminion.modules.brain.adapters.context.bridges",
                level="WARNING",
            ):
                payload = adapter.build(
                    session_id="s-cold",
                    agent_id="cold-agent-test",
                    purpose="decide",
                    budget={"max_tokens": 512},
                    hints={"query": "hello"},
                )

        self.assertNotEqual(payload.get("profile_version"), "brain-bridge:fallback:v1")
        self.assertNotEqual(payload.get("render_version"), "brain-bridge:fallback:v1")

    def test_bridge_identity_default_profile_uses_system_prompt_mission(self) -> None:
        from openminion.modules.brain.adapters.context.bridges.identity import (
            _ensure_default_profile,
        )

        class _IdentityCtl:
            def __init__(self) -> None:
                self.profile = None

            def get_profile(self, agent_id: str):  # noqa: ANN201
                del agent_id
                return

            def upsert_profile(self, profile) -> None:  # noqa: ANN001
                self.profile = profile

        identityctl = _IdentityCtl()
        _ensure_default_profile(
            identityctl,
            "minimax-m2-5",
            system_prompt="You are OpenMinion, a pragmatic assistant. Keep answers concise.",
        )

        self.assertIsNotNone(identityctl.profile)
        if identityctl.profile is None:  # pragma: no cover
            self.fail("expected default profile")
        self.assertEqual(
            identityctl.profile.role.mission,
            "You are OpenMinion, a pragmatic assistant.",
        )

    def test_bridge_identity_repairs_legacy_default_profile_mission(self) -> None:
        from openminion.modules.brain.adapters.context.bridges.identity import (
            _ensure_default_profile,
        )
        from openminion.modules.identity.models import (
            AgentProfile,
            PersonalitySpec,
            RiskSpec,
            RoleSpec,
            ToolPostureSpec,
        )

        class _IdentityCtl:
            def __init__(self) -> None:
                self.profile = AgentProfile(
                    agent_id="minimax-m2-5",
                    display_name="minimax-m2-5",
                    profile_revision=1,
                    role=RoleSpec(
                        mission="I am minimax-m2-5, a pragmatic AI assistant.",
                        responsibilities=[],
                        hard_constraints=[],
                    ),
                    personality=PersonalitySpec(
                        tone="professional", verbosity="normal"
                    ),
                    risk=RiskSpec(
                        risk_level="medium",
                        confirm_before=["destructive_actions"],
                    ),
                    tool_posture=ToolPostureSpec(tool_use="allowed"),
                    meta={"source": "default"},
                )

            def get_profile(self, agent_id: str):  # noqa: ANN201
                del agent_id
                return self.profile

            def upsert_profile(self, profile) -> None:  # noqa: ANN001
                self.profile = profile

        identityctl = _IdentityCtl()
        _ensure_default_profile(
            identityctl,
            "minimax-m2-5",
            system_prompt="You are OpenMinion, a pragmatic assistant. Keep answers concise.",
        )

        self.assertEqual(
            identityctl.profile.role.mission,
            "You are OpenMinion, a pragmatic assistant.",
        )

    def test_factory_context_adapter_sanitizes_technical_error_turns(self) -> None:
        try:
            from openminion.modules.brain.adapters.factory import create_context_adapter
            from openminion.modules.context.service import ContextCtlService  # noqa: F401
        except ImportError:
            self.skipTest("factory adapter not importable")

        class _SessionStore:
            def get_slice(
                self, *, session_id: str, purpose: str, limits: dict[str, int]
            ):
                del purpose, limits
                return {
                    "session_id": session_id,
                    "slice_version": "slice:v1",
                    "summary": {
                        "summary_short": (
                            "assistant: [system: UNEXECUTABLE_TOOL_ENVELOPE]\n"
                            "The model generated a tool envelope that could not be executed.\n"
                            "Target: cli-mcp-server_run_command\n"
                            "Reason: tool_not_allowed\n"
                            "This response has been blocked to prevent raw markup leak."
                        ),
                        "is_error": True,
                    },
                    "recent_turns": [
                        {
                            "turn_id": "t-user",
                            "role": "user",
                            "text": "show me file on this dir",
                        },
                        {
                            "turn_id": "t-assistant-error",
                            "role": "assistant",
                            "text": "'no browser provider specified and no default configured'",
                            "is_error": True,
                        },
                        {
                            "turn_id": "t-assistant-ok",
                            "role": "assistant",
                            "text": "Sure, I can help with that.",
                        },
                    ],
                    "open_tasks": [],
                    "active_state": {},
                    "recent_tool_events": [],
                }

        adapter = create_context_adapter(mode="auto", session_store=_SessionStore())
        if isinstance(adapter, LocalContextAdapter):
            self.skipTest("openminion_context not available in this test environment")
        payload = adapter.build(
            session_id="s-ctx",
            agent_id="a-ctx",
            purpose="decide",
            budget={"max_tokens": 512},
            hints={"query": "show me file on this dir"},
        )

        rendered = "\n".join(
            str(item.get("content", ""))
            for item in payload.get("messages", [])
            if isinstance(item, dict)
        ).lower()
        self.assertNotIn("unexecutable_tool_envelope", rendered)
        self.assertNotIn(
            "no browser provider specified and no default configured", rendered
        )

    def test_llm_adapter(self) -> None:
        try:
            from openminion.modules.llm.schemas import ToolCall
            from openminion.modules.llm.errors import LLMCtlError
            from openminion.modules.brain.adapters.llm import LlmctlAdapter
        except ImportError:
            self.skipTest("openminion.modules.llm not installed")

        from pydantic import BaseModel

        class DummySchema(BaseModel):
            x: int

        from unittest.mock import MagicMock

        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_call = ToolCall(id="1", name="submit_output", arguments={"x": 42})
        mock_resp.tool_calls = [mock_call]
        mock_client = fake_llm_client(response=mock_resp)

        adapter = LlmctlAdapter(mock_client)

        res = adapter.call_structured(
            model="test", purpose="decide", context={}, schema=DummySchema
        )
        self.assertEqual(res.get("x"), 42)

        failing_resp = MagicMock()
        failing_resp.ok = False
        failing_resp.error = SimpleNamespace(
            code="RATE_LIMITED",
            message='openai rate limited: {"error":{"message":"insufficient balance (1008)","http_code":"429"}}',
            details={"status_code": 429, "response_text": "insufficient balance"},
        )
        mock_client.call.return_value = failing_resp

        with self.assertRaises(LLMCtlError) as ctx:
            adapter.call_structured(
                model="test", purpose="decide", context={}, schema=DummySchema
            )

        self.assertEqual(ctx.exception.code, "RATE_LIMITED")
        self.assertEqual(ctx.exception.details.get("status_code"), 429)

    def test_llm_adapter_decide_is_schema_only_and_submit_output_forced(self) -> None:
        try:
            from openminion.modules.llm.schemas import ToolCall
            from openminion.modules.brain.adapters.llm import LlmctlAdapter
        except ImportError:
            self.skipTest("openminion.modules.llm not installed")

        from pydantic import BaseModel

        class DummySchema(BaseModel):
            x: int

        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.tool_calls = [
            ToolCall(id="1", name="submit_output", arguments={"x": 7})
        ]
        mock_client = fake_llm_client(response=mock_resp)
        adapter = LlmctlAdapter(mock_client)

        res = adapter.call_structured(
            model="test",
            purpose="decide",
            context={
                "hints": {
                    "user_input": "open browser and go to google",
                    "runtime_tool_schemas": [
                        {
                            "name": "browser",
                            "description": "Navigate pages",
                            "parameters": {"type": "object"},
                        },
                        {
                            "name": "weather",
                            "description": "Current weather",
                            "parameters": {"type": "object"},
                        },
                    ],
                }
            },
            schema=DummySchema,
        )

        self.assertEqual(res.get("x"), 7)
        sent_request = mock_client.call.call_args[0][0]
        self.assertEqual(
            sent_request.tool_choice,
            {"type": "function", "function": {"name": "submit_output"}},
        )
        sent_tool_names = [tool.name for tool in (sent_request.tools or [])]
        self.assertEqual(sent_tool_names, ["submit_output"])

    def test_llm_adapter_judge_is_schema_only_and_submit_output_forced(self) -> None:
        try:
            from openminion.modules.llm.schemas import ToolCall
            from openminion.modules.brain.adapters.llm import LlmctlAdapter
            from openminion.modules.brain.execution import ClosureJudgment
        except ImportError:
            self.skipTest("openminion modules not installed")

        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.tool_calls = [
            ToolCall(
                id="judge-1",
                name="submit_output",
                arguments={
                    "satisfied": False,
                    "reason": "need follow-up",
                    "next_action": "replan",
                },
            )
        ]
        mock_client = fake_llm_client(response=mock_resp)
        adapter = LlmctlAdapter(mock_client)

        res = adapter.call_structured(
            model="test",
            purpose="judge",
            context={
                "hints": {
                    "runtime_tool_schemas": [
                        {
                            "name": "weather",
                            "description": "Current weather",
                            "parameters": {"type": "object"},
                        },
                        {
                            "name": "browser",
                            "description": "Navigate web pages",
                            "parameters": {"type": "object"},
                        },
                    ]
                }
            },
            schema=ClosureJudgment,
        )

        self.assertIsInstance(res, dict)
        self.assertEqual(res.get("next_action"), "replan")
        sent_request = mock_client.call.call_args[0][0]
        self.assertEqual(
            sent_request.tool_choice,
            {"type": "function", "function": {"name": "submit_output"}},
        )
        sent_tool_names = [tool.name for tool in (sent_request.tools or [])]
        self.assertEqual(sent_tool_names, ["submit_output"])

    def test_llm_adapter_plan_is_schema_only_and_submit_output_forced(self) -> None:
        try:
            from openminion.modules.llm.schemas import ToolCall
            from openminion.modules.brain.adapters.llm import LlmctlAdapter
            from openminion.modules.brain.schemas import Plan
        except ImportError:
            self.skipTest("openminion modules not installed")

        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.tool_calls = [
            ToolCall(
                id="plan-1",
                name="submit_output",
                arguments={
                    "objective": "collect weather and summarize",
                    "steps": [
                        {
                            "kind": "tool",
                            "title": "search weather",
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
        ]
        mock_client = fake_llm_client(response=mock_resp)
        adapter = LlmctlAdapter(mock_client)

        res = adapter.call_structured(
            model="test",
            purpose="plan",
            context={
                "hints": {
                    "runtime_tool_schemas": [
                        {
                            "name": "weather",
                            "description": "Current weather",
                            "parameters": {"type": "object"},
                        },
                        {
                            "name": "browser",
                            "description": "Navigate web pages",
                            "parameters": {"type": "object"},
                        },
                    ]
                }
            },
            schema=Plan,
        )

        self.assertIsInstance(res, dict)
        self.assertEqual(res.get("objective"), "collect weather and summarize")
        sent_request = mock_client.call.call_args[0][0]
        self.assertEqual(
            sent_request.tool_choice,
            {"type": "function", "function": {"name": "submit_output"}},
        )
        sent_tool_names = [tool.name for tool in (sent_request.tools or [])]
        self.assertEqual(sent_tool_names, ["submit_output"])

    def test_llm_adapter_plan_rejects_native_tool_calls(self) -> None:
        try:
            from openminion.modules.llm.schemas import ToolCall
            from openminion.modules.brain.adapters.llm import LlmctlAdapter
            from openminion.modules.brain.retry import call_structured_with_retry
            from openminion.modules.brain.schemas import Plan
        except ImportError:
            self.skipTest("openminion modules not installed")

        invalid_resp = MagicMock()
        invalid_resp.ok = True
        invalid_resp.tool_calls = [
            ToolCall(
                id="tool-1",
                name="weather",
                arguments={"location": "Tokyo"},
            )
        ]
        mock_client = fake_llm_client(response=invalid_resp)
        adapter = LlmctlAdapter(mock_client)

        with self.assertRaisesRegex(RuntimeError, "structured output"):
            _ = call_structured_with_retry(
                adapter,
                model="test",
                purpose="plan",
                context={"messages": [{"role": "user", "content": "plan a trip"}]},
                schema=Plan,
            )

    def test_llm_adapter_validate_retries_plain_text_until_submit_output(self) -> None:
        try:
            from openminion.modules.llm.schemas import ToolCall
            from openminion.modules.brain.adapters.llm import LlmctlAdapter
            from openminion.modules.brain.retry import call_structured_with_retry
            from openminion.modules.brain.schemas import FeasibilityReport
        except ImportError:
            self.skipTest("openminion modules not installed")

        invalid_resp = MagicMock()
        invalid_resp.ok = True
        invalid_resp.tool_calls = []
        invalid_resp.output_text = "I'll check the weather in Tokyo for you."

        valid_resp = MagicMock()
        valid_resp.ok = True
        valid_resp.tool_calls = [
            ToolCall(
                id="submit-1",
                name="submit_output",
                arguments={
                    "plan_viable": True,
                    "recommendation": "proceed_full",
                    "user_message": "",
                    "requires_user_choice": False,
                    "viable_intent_ids": ["intent_01_check_weather"],
                    "blocked_intent_ids": [],
                    "assessments": [
                        {
                            "intent_id": "intent_01_check_weather",
                            "status": "covered",
                            "reason": "",
                            "covering_tools": ["weather"],
                        }
                    ],
                },
            )
        ]
        valid_resp.output_text = ""

        mock_client = fake_llm_client(responses=[invalid_resp, valid_resp])
        adapter = LlmctlAdapter(mock_client)

        result = call_structured_with_retry(
            adapter,
            model="test",
            purpose="validate",
            context={"messages": [{"role": "user", "content": "weather in tokyo"}]},
            schema=FeasibilityReport,
        )

        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("recommendation"), "proceed_full")
        self.assertEqual(mock_client.call.call_count, 2)

    def test_llm_adapter_plan_retry_includes_viable_subset_guidance(self) -> None:
        try:
            from openminion.modules.llm.schemas import ToolCall
            from openminion.modules.brain.adapters.llm import LlmctlAdapter
            from openminion.modules.brain.retry import call_structured_with_retry
            from openminion.modules.brain.schemas import Plan
        except ImportError:
            self.skipTest("openminion modules not installed")

        invalid_resp = MagicMock()
        invalid_resp.ok = True
        invalid_resp.tool_calls = []
        invalid_resp.output_text = "I can do part of this request, but not all of it."

        valid_resp = MagicMock()
        valid_resp.ok = True
        valid_resp.tool_calls = [
            ToolCall(
                id="submit-1",
                name="submit_output",
                arguments={
                    "objective": "weather",
                    "steps": [
                        {
                            "kind": "tool",
                            "title": "Weather",
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
        ]
        valid_resp.output_text = ""

        mock_client = fake_llm_client(responses=[invalid_resp, valid_resp])
        adapter = LlmctlAdapter(mock_client)

        result = call_structured_with_retry(
            adapter,
            model="test",
            purpose="plan",
            context={
                "messages": [{"role": "user", "content": "weather and book a flight"}]
            },
            schema=Plan,
        )

        self.assertEqual(result.get("objective"), "weather")
        self.assertEqual(mock_client.call.call_count, 2)
        retry_req = mock_client.call.call_args_list[1].args[0]
        retry_message = _find_retry_message(retry_req.messages)
        self.assertEqual(retry_req.messages[0].role, "system")
        self.assertIn("viable subset", retry_message)
        self.assertIn(
            "do not explain capability gaps in prose",
            retry_message.lower(),
        )
        self.assertIn("objective, steps, stop_conditions", retry_message)

    def test_llm_adapter_plan_retry_rejects_unresolved_placeholder_steps(self) -> None:
        try:
            from openminion.modules.llm.schemas import ToolCall
            from openminion.modules.brain.adapters.llm import LlmctlAdapter
            from openminion.modules.brain.retry import call_structured_with_retry
            from openminion.modules.brain.schemas import Plan
        except ImportError:
            self.skipTest("openminion modules not installed")

        invalid_resp = MagicMock()
        invalid_resp.ok = True
        invalid_resp.tool_calls = [
            ToolCall(
                id="submit-1",
                name="submit_output",
                arguments={
                    "objective": "Get latest news on Iran",
                    "steps": [
                        {
                            "kind": "tool",
                            "title": "Search latest Iran news",
                            "tool_name": "web.search",
                            "args": {"query": "latest news iran"},
                            "success_criteria": {"status": "success"},
                        },
                        {
                            "kind": "tool",
                            "title": "Review result",
                            "tool_name": "web.fetch",
                            "args": {"url": "<UNKNOWN>"},
                            "success_criteria": {"status": "success"},
                        },
                        {
                            "kind": "finish",
                            "title": "Deliver summary",
                            "final_message": (
                                "Based on the web searches, the latest news on Iran "
                                "includes [SUMMARY]."
                            ),
                        },
                    ],
                    "stop_conditions": ["done"],
                    "assumptions": [],
                    "risk_summary": "low",
                    "success_criteria": {"status": "success"},
                },
            )
        ]
        invalid_resp.output_text = ""

        valid_resp = MagicMock()
        valid_resp.ok = True
        valid_resp.tool_calls = [
            ToolCall(
                id="submit-2",
                name="submit_output",
                arguments={
                    "objective": "Get latest news on Iran",
                    "steps": [
                        {
                            "kind": "tool",
                            "title": "Search latest Iran news",
                            "tool_name": "web.search",
                            "args": {"query": "latest news iran"},
                            "success_criteria": {"status": "success"},
                        }
                    ],
                    "stop_conditions": ["done"],
                    "assumptions": [],
                    "risk_summary": "low",
                    "success_criteria": {"status": "success"},
                },
            )
        ]
        valid_resp.output_text = ""

        mock_client = fake_llm_client(responses=[invalid_resp, valid_resp])
        adapter = LlmctlAdapter(mock_client)

        result = call_structured_with_retry(
            adapter,
            model="test",
            purpose="plan",
            context={"messages": [{"role": "user", "content": "latest news on iran"}]},
            schema=Plan,
        )

        self.assertEqual(result.get("objective"), "Get latest news on Iran")
        self.assertEqual(mock_client.call.call_count, 2)
        retry_req = mock_client.call.call_args_list[1].args[0]
        retry_message = _find_retry_message(retry_req.messages)
        self.assertIn("Do not emit unresolved placeholders", retry_message)
        self.assertIn("[SUMMARY]", retry_message)
        self.assertIn("<UNKNOWN>", retry_message)

    def test_llm_adapter_plan_accepts_json_output_text_when_schema_valid(self) -> None:
        try:
            from openminion.modules.brain.adapters.llm import LlmctlAdapter
            from openminion.modules.brain.schemas import Plan
        except ImportError:
            self.skipTest("openminion modules not installed")

        resp = MagicMock()
        resp.ok = True
        resp.tool_calls = []
        resp.output_text = (
            '{"objective":"weather","steps":[{"kind":"tool","title":"Weather",'
            '"tool_name":"weather","args":{"location":"Tokyo"},'
            '"success_criteria":{"status":"success"}}],'
            '"stop_conditions":["done"],"assumptions":[],"risk_summary":"low",'
            '"success_criteria":{"status":"success"}}'
        )
        mock_client = fake_llm_client(response=resp)
        adapter = LlmctlAdapter(mock_client)

        result = adapter.call_structured(
            model="test",
            purpose="plan",
            context={"messages": [{"role": "user", "content": "weather"}]},
            schema=Plan,
        )

        self.assertEqual(result.get("objective"), "weather")
        self.assertEqual(mock_client.call.call_count, 1)

    def test_llm_adapter_plan_normalizes_nested_wrapper_and_drifted_step_shapes(
        self,
    ) -> None:
        try:
            from openminion.modules.llm.schemas import ToolCall
            from openminion.modules.brain.adapters.llm import LlmctlAdapter
            from openminion.modules.brain.schemas import Plan
        except ImportError:
            self.skipTest("openminion modules not installed")

        resp = MagicMock()
        resp.ok = True
        resp.tool_calls = [
            ToolCall(
                id="submit-1",
                name="submit_output",
                arguments={
                    "plan": {
                        "objective": "Find the latest news on Iran",
                        "steps": json.dumps(
                            [
                                {
                                    "kind": "web.search",
                                    "title": "Search latest Iran news",
                                    "inputs": {"query": "latest news on iran"},
                                },
                                {
                                    "kind": "FinishCommand",
                                    "title": "Close",
                                    "final_message": "Search completed successfully.",
                                },
                            ]
                        ),
                        "stop_conditions": ["done"],
                        "assumptions": [],
                        "risk_summary": "low",
                        "success_criteria": {"status": "success"},
                    }
                },
            )
        ]
        resp.output_text = ""
        mock_client = fake_llm_client(response=resp)
        adapter = LlmctlAdapter(mock_client)

        with self.assertLogs(
            "openminion.modules.brain.adapters.llm.normalize",
            level="WARNING",
        ) as logs:
            result = adapter.call_structured(
                model="test",
                purpose="plan",
                context={"messages": [{"role": "user", "content": "latest news"}]},
                schema=Plan,
            )

        self.assertEqual(result.get("objective"), "Find the latest news on Iran")
        steps = result.get("steps")
        self.assertIsInstance(steps, list)
        assert isinstance(steps, list)
        self.assertEqual(steps[0].get("kind"), "tool")
        self.assertEqual(steps[0].get("tool_name"), "web.search")
        self.assertEqual(steps[0].get("args", {}).get("query"), "latest news on iran")
        self.assertEqual(steps[1].get("kind"), "finish")
        joined_logs = "\n".join(logs.output)
        self.assertIn("plan_unwrapped", joined_logs)
        self.assertIn("steps.parsed_json_string", joined_logs)
        self.assertIn("steps[0].tool_name", joined_logs)

    def test_llm_adapter_plan_normalizes_params_alias_to_args(self) -> None:
        try:
            from openminion.modules.llm.schemas import ToolCall
            from openminion.modules.brain.adapters.llm import LlmctlAdapter
            from openminion.modules.brain.schemas import Plan
        except ImportError:
            self.skipTest("openminion modules not installed")

        resp = MagicMock()
        resp.ok = True
        resp.tool_calls = [
            ToolCall(
                id="submit-1",
                name="submit_output",
                arguments={
                    "objective": "Find the latest news on Iran",
                    "steps": [
                        {
                            "kind": "tool",
                            "tool_name": "web.search",
                            "title": "Search latest Iran news",
                            "params": {"query": "latest news on iran"},
                        },
                        {
                            "kind": "finish",
                            "title": "Close",
                            "final_message": "Search completed successfully.",
                        },
                    ],
                    "stop_conditions": ["done"],
                    "assumptions": [],
                    "risk_summary": "low",
                    "success_criteria": {"status": "success"},
                },
            )
        ]
        resp.output_text = ""
        mock_client = fake_llm_client(response=resp)
        adapter = LlmctlAdapter(mock_client)

        with self.assertLogs(
            "openminion.modules.brain.adapters.llm.normalize",
            level="WARNING",
        ) as logs:
            result = adapter.call_structured(
                model="test",
                purpose="plan",
                context={"messages": [{"role": "user", "content": "latest news"}]},
                schema=Plan,
            )

        steps = result.get("steps")
        self.assertIsInstance(steps, list)
        assert isinstance(steps, list)
        self.assertEqual(steps[0].get("kind"), "tool")
        self.assertEqual(steps[0].get("tool_name"), "web.search")
        self.assertEqual(steps[0].get("args", {}).get("query"), "latest news on iran")
        self.assertNotIn("params", steps[0])
        joined_logs = "\n".join(logs.output)
        self.assertIn("structured.submit_output_normalized", joined_logs)
        self.assertIn("steps[0].args", joined_logs)

    def test_llm_adapter_plan_replaces_empty_args_with_parameters_payload(self) -> None:
        try:
            from openminion.modules.llm.schemas import ToolCall
            from openminion.modules.brain.adapters.llm import LlmctlAdapter
            from openminion.modules.brain.schemas import Plan
        except ImportError:
            self.skipTest("openminion modules not installed")

        resp = MagicMock()
        resp.ok = True
        resp.tool_calls = [
            ToolCall(
                id="submit-1",
                name="submit_output",
                arguments={
                    "objective": "Check the time and latest news",
                    "steps": [
                        {
                            "kind": "functions.time",
                            "title": "Check UTC time",
                            "args": {},
                            "parameters": {"utc_offset": "+00:00"},
                        },
                        {
                            "kind": "tool",
                            "tool_name": "functions.web.search",
                            "title": "Search current OpenAI news",
                            "args": {},
                            "parameters": {"query": "latest OpenAI news"},
                        },
                    ],
                    "stop_conditions": ["done"],
                    "assumptions": [],
                    "risk_summary": "low",
                    "success_criteria": {"status": "success"},
                },
            )
        ]
        resp.output_text = ""
        mock_client = fake_llm_client(response=resp)
        adapter = LlmctlAdapter(mock_client)

        with self.assertLogs(
            "openminion.modules.brain.adapters.llm.normalize",
            level="WARNING",
        ) as logs:
            result = adapter.call_structured(
                model="openai/gpt-4o-mini",
                purpose="plan",
                context={
                    "messages": [
                        {"role": "user", "content": "time and latest OpenAI news"}
                    ]
                },
                schema=Plan,
            )

        steps = result.get("steps")
        self.assertIsInstance(steps, list)
        assert isinstance(steps, list)
        self.assertEqual(steps[0].get("kind"), "tool")
        self.assertEqual(steps[0].get("tool_name"), "functions.time")
        self.assertEqual(steps[0].get("args", {}).get("utc_offset"), "+00:00")
        self.assertNotIn("parameters", steps[0])
        self.assertEqual(steps[1].get("tool_name"), "functions.web.search")
        self.assertEqual(
            steps[1].get("args", {}).get("query"),
            "latest OpenAI news",
        )
        self.assertNotIn("parameters", steps[1])
        joined_logs = "\n".join(logs.output)
        self.assertIn("steps[0].parameters_replaced_empty_args", joined_logs)
        self.assertIn("steps[1].parameters_replaced_empty_args", joined_logs)
        self.assertNotIn("steps[0].parameters_conflict", joined_logs)
        self.assertNotIn("steps[1].parameters_conflict", joined_logs)

    def test_llm_adapter_plan_rejects_string_steps_without_explicit_llm_structure(
        self,
    ) -> None:
        try:
            from openminion.modules.llm.schemas import ToolCall
            from openminion.modules.brain.adapters.llm import LlmctlAdapter
            from openminion.modules.brain.schemas import Plan
        except ImportError:
            self.skipTest("openminion modules not installed")

        resp = MagicMock()
        resp.ok = True
        resp.tool_calls = [
            ToolCall(
                id="submit-1",
                name="submit_output",
                arguments={
                    "objective": "Check the latest news on Iran",
                    "steps": [
                        "call tool:web.search, query: latest news on iran",
                        {
                            "kind": "finish",
                            "title": "Close",
                            "final_message": "Done.",
                        },
                    ],
                    "stop_conditions": ["done"],
                    "assumptions": [],
                    "risk_summary": "low",
                    "success_criteria": {"status": "success"},
                },
            )
        ]
        resp.output_text = ""
        mock_client = fake_llm_client(response=resp)
        adapter = LlmctlAdapter(mock_client)

        result = adapter.call_structured(
            model="google/gemini-2.5-flash-lite",
            purpose="plan",
            context={
                "messages": [{"role": "user", "content": "check latest Iran news"}]
            },
            schema=Plan,
        )

        self.assertTrue(result.get("_structured_retryable"))
        self.assertEqual(
            result.get("_structured_failure_kind"), "invalid_structured_output"
        )

    def test_llm_adapter_decide_fails_closed_on_native_tool_calls(self) -> None:
        try:
            from openminion.modules.llm.schemas import ToolCall
            from openminion.modules.brain.adapters.llm import LlmctlAdapter
            from openminion.modules.brain.retry import call_structured_with_retry
            from openminion.modules.brain.schemas import DecisionAdapter
        except ImportError:
            self.skipTest("openminion modules not installed")

        first_resp = MagicMock()
        first_resp.ok = True
        first_resp.tool_calls = [
            ToolCall(
                id="tool-1",
                name="weather.openmeteo.current",
                arguments={"location": "Tokyo"},
            )
        ]
        first_resp.output_text = ""

        second_resp = MagicMock()
        second_resp.ok = True
        second_resp.tool_calls = [
            ToolCall(
                id="tool-2",
                name="web.search",
                arguments={"query": "tokyo weather"},
            )
        ]
        second_resp.output_text = ""

        third_resp = MagicMock()
        third_resp.ok = True
        third_resp.tool_calls = [
            ToolCall(
                id="tool-3",
                name="location.get",
                arguments={},
            )
        ]
        third_resp.output_text = ""

        mock_client = fake_llm_client(responses=[first_resp, second_resp, third_resp])
        adapter = LlmctlAdapter(mock_client)

        result = call_structured_with_retry(
            adapter,
            model="test",
            purpose="decide",
            context={"messages": [{"role": "user", "content": "what's weather?"}]},
            schema=DecisionAdapter,
        )

        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("route"), "respond")
        self.assertEqual(result.get("reason_code"), "invalid_decide_tool_call")
        self.assertIn("internal decision error", str(result.get("answer", "")).lower())
        self.assertEqual(mock_client.call.call_count, 3)

    def test_llm_adapter_decide_retry_accepts_submit_output(self) -> None:
        try:
            from openminion.modules.llm.schemas import ToolCall
            from openminion.modules.brain.adapters.llm import LlmctlAdapter
            from openminion.modules.brain.retry import call_structured_with_retry
            from openminion.modules.brain.schemas import DecisionAdapter
        except ImportError:
            self.skipTest("openminion modules not installed")

        invalid_resp = MagicMock()
        invalid_resp.ok = True
        invalid_resp.tool_calls = [
            ToolCall(
                id="tool-1",
                name="weather.openmeteo.current",
                arguments={"location": "Tokyo"},
            )
        ]
        invalid_resp.output_text = ""

        valid_resp = MagicMock()
        valid_resp.ok = True
        valid_resp.tool_calls = [
            ToolCall(
                id="submit-1",
                name="submit_output",
                arguments={
                    "mode": "respond",
                    "confidence": 0.8,
                    "reason_code": "needs_clarification",
                    "respond_kind": "clarify",
                    "question": "Which city?",
                },
            )
        ]
        valid_resp.output_text = ""

        mock_client = fake_llm_client(responses=[invalid_resp, valid_resp])
        adapter = LlmctlAdapter(mock_client)

        result = call_structured_with_retry(
            adapter,
            model="test",
            purpose="decide",
            context={"messages": [{"role": "user", "content": "weather"}]},
            schema=DecisionAdapter,
        )

        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("route"), "respond")
        self.assertEqual(result.get("respond_kind"), "clarify")
        self.assertEqual(result.get("reason_code"), "needs_clarification")
        self.assertEqual(mock_client.call.call_count, 2)

    def test_llm_adapter_decide_progressive_retry_preserves_empty_answer_when_respond_answer_is_missing(
        self,
    ) -> None:
        try:
            from openminion.modules.brain.adapters.llm import LlmctlAdapter
            from openminion.modules.brain.retry import call_structured_with_retry
            from openminion.modules.brain.schemas import DecisionAdapter
            from openminion.modules.llm.schemas import ToolCall
        except ImportError:
            self.skipTest("openminion modules not installed")

        response = MagicMock()
        response.ok = True
        response.tool_calls = [
            ToolCall(
                id="submit-1",
                name="submit_output",
                arguments={
                    "mode": "respond",
                    "confidence": 1.0,
                    "reason_code": "greeting",
                    "respond_kind": "answer",
                    "sub_intents": ["greet_user"],
                    "rationale": "",
                    "answer": None,
                },
            )
        ]
        response.output_text = "<think>Simple greeting.</think>\n\n"
        mock_client = fake_llm_client(response=response)
        adapter = LlmctlAdapter(mock_client)

        result = call_structured_with_retry(
            adapter,
            model="MiniMax-M2.7",
            purpose="decide",
            context={"messages": [{"role": "user", "content": "hi"}]},
            schema=DecisionAdapter,
        )

        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("route"), "respond")
        self.assertEqual(result.get("reason_code"), "greeting")
        self.assertEqual(result.get("respond_kind"), "answer")
        self.assertEqual(result.get("answer"), "")
        self.assertEqual(mock_client.call.call_count, 2)

    def test_llm_adapter_decide_does_not_recover_answer_from_visible_output_text(
        self,
    ) -> None:
        try:
            from openminion.modules.brain.adapters.llm import LlmctlAdapter
            from openminion.modules.brain.retry import call_structured_with_retry
            from openminion.modules.brain.schemas import DecisionAdapter
            from openminion.modules.llm.schemas import ToolCall
        except ImportError:
            self.skipTest("openminion modules not installed")

        response = MagicMock()
        response.ok = True
        response.tool_calls = [
            ToolCall(
                id="submit-1",
                name="submit_output",
                arguments={
                    "mode": "respond",
                    "confidence": 1.0,
                    "reason_code": "greeting",
                    "respond_kind": "answer",
                    "sub_intents": ["greet_user"],
                    "rationale": "",
                    "answer": None,
                },
            )
        ]
        response.output_text = "<think>Simple greeting.</think>\n\nHello there!"
        mock_client = fake_llm_client(response=response)
        adapter = LlmctlAdapter(mock_client)

        result = call_structured_with_retry(
            adapter,
            model="MiniMax-M2.7",
            purpose="decide",
            context={"messages": [{"role": "user", "content": "hi"}]},
            schema=DecisionAdapter,
        )

        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("route"), "respond")
        self.assertEqual(result.get("reason_code"), "greeting")
        self.assertEqual(result.get("respond_kind"), "answer")
        self.assertEqual(result.get("answer"), "")
        self.assertEqual(mock_client.call.call_count, 2)

    def test_llm_adapter_decide_retry_recovers_from_invalid_submit_output_shape(
        self,
    ) -> None:
        try:
            from openminion.modules.brain.adapters.llm import LlmctlAdapter
            from openminion.modules.brain.retry import call_structured_with_retry
            from openminion.modules.brain.schemas import DecisionAdapter
            from openminion.modules.llm.schemas import ToolCall
        except ImportError:
            self.skipTest("openminion modules not installed")

        invalid_resp = MagicMock()
        invalid_resp.ok = True
        invalid_resp.tool_calls = [
            ToolCall(
                id="submit-1", name="submit_output", arguments={"message": "Hi there!"}
            )
        ]
        invalid_resp.output_text = ""

        valid_resp = MagicMock()
        valid_resp.ok = True
        valid_resp.tool_calls = [
            ToolCall(
                id="submit-2",
                name="submit_output",
                arguments={
                    "mode": "respond",
                    "confidence": 0.95,
                    "reason_code": "greeting",
                    "respond_kind": "answer",
                    "sub_intents": [],
                    "rationale": "",
                    "answer": "Hi there!",
                },
            )
        ]
        valid_resp.output_text = ""

        mock_client = fake_llm_client(responses=[invalid_resp, valid_resp])
        adapter = LlmctlAdapter(mock_client)

        result = call_structured_with_retry(
            adapter,
            model="test",
            purpose="decide",
            context={"messages": [{"role": "user", "content": "hi"}]},
            schema=DecisionAdapter,
        )

        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("route"), "respond")
        self.assertEqual(result.get("reason_code"), "greeting")
        self.assertEqual(mock_client.call.call_count, 2)

    def test_llm_adapter_decide_normalizes_legacy_decompose_subtask_wrappers(
        self,
    ) -> None:
        try:
            from openminion.modules.brain.adapters.llm import LlmctlAdapter
            from openminion.modules.brain.schemas import DecisionAdapter
            from openminion.modules.llm.schemas import ToolCall
        except ImportError:
            self.skipTest("openminion modules not installed")

        response = MagicMock()
        response.ok = True
        response.tool_calls = [
            ToolCall(
                id="submit-1",
                name="submit_output",
                arguments={
                    "mode": "plan",
                    "confidence": 0.92,
                    "reason_code": "compound_trip_plan",
                    "plan_strategy": "decomposed",
                    "subtasks": [
                        {
                            "intent_id": "research",
                            "description": "Research current travel requirements",
                            "kind": "research",
                        },
                        {
                            "id": "1",
                            "goal": "Plan Tokyo days",
                            "subtasks": [{"id": "1.1", "goal": "Nested detail"}],
                        },
                    ],
                },
            )
        ]
        response.output_text = ""
        mock_client = fake_llm_client(response=response)
        adapter = LlmctlAdapter(mock_client)

        result = adapter.call_structured(
            model="MiniMax-M2.7",
            purpose="decide",
            context={"messages": [{"role": "user", "content": "plan japan trip"}]},
            schema=DecisionAdapter,
        )

        self.assertEqual(result.get("route"), "act")
        self.assertEqual(result.get("act_profile"), "orchestrate")
        subtasks = result.get("subtasks")
        assert isinstance(subtasks, list)
        self.assertEqual(subtasks[0].get("subtask_id"), "research")
        self.assertEqual(
            subtasks[0].get("goal"), "Research current travel requirements"
        )
        self.assertNotIn("description", subtasks[0])
        self.assertNotIn("kind", subtasks[0])
        self.assertNotIn("intent_id", subtasks[0])
        self.assertEqual(subtasks[1].get("subtask_id"), "1")
        self.assertEqual(subtasks[1].get("goal"), "Plan Tokyo days")
        self.assertNotIn("id", subtasks[0])
        self.assertNotIn("subtasks", subtasks[1])

    def test_llm_adapter_decide_accepts_minimal_act_payload_without_retry(self) -> None:
        try:
            from openminion.modules.brain.adapters.llm import LlmctlAdapter
            from openminion.modules.brain.retry import call_structured_with_retry
            from openminion.modules.brain.schemas import DecisionAdapter
            from openminion.modules.llm.schemas import ToolCall
        except ImportError:
            self.skipTest("openminion modules not installed")

        invalid_structured = MagicMock()
        invalid_structured.ok = True
        invalid_structured.tool_calls = [
            ToolCall(
                id="submit-1",
                name="submit_output",
                arguments={
                    "mode": "act",
                    "confidence": 1.0,
                    "reason_code": "simple_weather_query",
                },
            )
        ]
        invalid_structured.output_text = ""

        valid_act = MagicMock()
        valid_act.ok = True
        valid_act.tool_calls = [
            ToolCall(
                id="submit-2",
                name="submit_output",
                arguments={
                    "mode": "act",
                    "confidence": 0.9,
                    "reason_code": "simple_weather_query",
                    "act_profile": "general",
                    "execution_target": {"kind": "local"},
                    "sub_intents": ["check_weather"],
                    "rationale": "Use the shared act loop.",
                },
            )
        ]
        valid_act.output_text = ""

        mock_client = fake_llm_client(responses=[invalid_structured, valid_act])
        adapter = LlmctlAdapter(mock_client)

        result = call_structured_with_retry(
            adapter,
            model="test",
            purpose="decide",
            context={
                "messages": [{"role": "user", "content": "what's weather at sf?"}],
                "hints": {"tool_aware": True},
            },
            schema=DecisionAdapter,
        )

        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("route"), "act")
        self.assertEqual(mock_client.call.call_count, 1)
        self.assertIsNone(result.get("execution_target"))

    def test_llm_adapter_decide_accepts_missing_execution_target_without_retry(
        self,
    ) -> None:
        try:
            from openminion.modules.brain.adapters.llm import LlmctlAdapter
            from openminion.modules.brain.retry import call_structured_with_retry
            from openminion.modules.brain.schemas import DecisionAdapter
            from openminion.modules.llm.schemas import ToolCall
        except ImportError:
            self.skipTest("openminion modules not installed")

        invalid_structured = MagicMock()
        invalid_structured.ok = True
        invalid_structured.tool_calls = [
            ToolCall(
                id="submit-1",
                name="submit_output",
                arguments={
                    "mode": "act",
                    "confidence": 1.0,
                    "reason_code": "simple_weather_query",
                    "act_profile": "general",
                    "rationale": "Missing execution target should trigger retry.",
                },
            )
        ]
        invalid_structured.output_text = ""

        valid_act = MagicMock()
        valid_act.ok = True
        valid_act.tool_calls = [
            ToolCall(
                id="submit-2",
                name="submit_output",
                arguments={
                    "mode": "act",
                    "confidence": 0.9,
                    "reason_code": "simple_weather_query",
                    "act_profile": "general",
                    "execution_target": {"kind": "local"},
                    "sub_intents": ["check_weather"],
                    "rationale": "Use the shared act loop.",
                },
            )
        ]
        valid_act.output_text = ""

        mock_client = fake_llm_client(responses=[invalid_structured, valid_act])
        adapter = LlmctlAdapter(mock_client)

        result = call_structured_with_retry(
            adapter,
            model="test",
            purpose="decide",
            context={
                "messages": [
                    {"role": "user", "content": "what's weather at san francisco?"}
                ],
                "hints": {"tool_aware": True},
            },
            schema=DecisionAdapter,
        )

        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("route"), "act")
        self.assertEqual(mock_client.call.call_count, 1)
        self.assertIsNone(result.get("execution_target"))

    def test_llm_adapter_decide_semantic_tool_denial_no_longer_retries(
        self,
    ) -> None:
        try:
            from openminion.modules.brain.adapters.llm import LlmctlAdapter
            from openminion.modules.brain.retry import call_structured_with_retry
            from openminion.modules.brain.schemas import DecisionAdapter
            from openminion.modules.llm.schemas import ToolCall
        except ImportError:
            self.skipTest("openminion modules not installed")

        semantic_respond = MagicMock()
        semantic_respond.ok = True
        semantic_respond.tool_calls = [
            ToolCall(
                id="submit-1",
                name="submit_output",
                arguments={
                    "mode": "respond",
                    "confidence": 1.0,
                    "reason_code": "simple_factual_question",
                    "respond_kind": "answer",
                    "sub_intents": ["check_weather"],
                    "rationale": "",
                    "answer": (
                        "No action taken. Request interpreted as conversational input: "
                        "what's weather at sf?"
                    ),
                },
            )
        ]
        semantic_respond.output_text = ""

        mock_client = fake_llm_client(response=semantic_respond)
        adapter = LlmctlAdapter(mock_client)

        result = call_structured_with_retry(
            adapter,
            model="test",
            purpose="decide",
            context={
                "messages": [{"role": "user", "content": "what's weather at sf?"}],
                "hints": {"tool_aware": True},
            },
            schema=DecisionAdapter,
        )

        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("route"), "respond")
        self.assertEqual(mock_client.call.call_count, 1)

    def test_llm_adapter_decide_tool_denial_without_tool_aware_does_not_retry(
        self,
    ) -> None:
        try:
            from openminion.modules.brain.adapters.llm import LlmctlAdapter
            from openminion.modules.brain.schemas import DecisionAdapter
            from openminion.modules.llm.schemas import ToolCall
        except ImportError:
            self.skipTest("openminion modules not installed")

        direct_respond = MagicMock()
        direct_respond.ok = True
        direct_respond.tool_calls = [
            ToolCall(
                id="submit-1",
                name="submit_output",
                arguments={
                    "mode": "respond",
                    "confidence": 1.0,
                    "reason_code": "simple_factual_question",
                    "respond_kind": "answer",
                    "sub_intents": [],
                    "rationale": "",
                    "answer": "I don't have access to real-time weather data.",
                },
            )
        ]
        direct_respond.output_text = ""

        mock_client = fake_llm_client(response=direct_respond)
        adapter = LlmctlAdapter(mock_client)

        result = adapter.call_structured(
            model="test",
            purpose="decide",
            context={
                "messages": [{"role": "user", "content": "what's weather at sf?"}]
            },
            schema=DecisionAdapter,
        )

        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("route"), "respond")
        self.assertEqual(mock_client.call.call_count, 1)

    def test_llm_adapter_decide_retry_prompt_uses_existing_results_on_replan(
        self,
    ) -> None:
        try:
            from openminion.modules.brain.adapters.llm import LlmctlAdapter
            from openminion.modules.brain.retry import call_structured_with_retry
            from openminion.modules.brain.schemas import DecisionAdapter
            from openminion.modules.llm.schemas import ToolCall
        except ImportError:
            self.skipTest("openminion modules not installed")

        invalid_structured = MagicMock()
        invalid_structured.ok = True
        invalid_structured.tool_calls = [
            ToolCall(
                id="submit-1",
                name="submit_output",
                arguments={
                    "mode": "act",
                    "confidence": 1.0,
                    "reason_code": "simple_weather_query",
                },
            )
        ]
        invalid_structured.output_text = ""

        valid_respond = MagicMock()
        valid_respond.ok = True
        valid_respond.tool_calls = [
            ToolCall(
                id="submit-2",
                name="submit_output",
                arguments={
                    "mode": "respond",
                    "confidence": 0.9,
                    "reason_code": "existing_result_sufficient",
                    "respond_kind": "answer",
                    "sub_intents": ["check_weather"],
                    "rationale": "",
                    "answer": "San Diego is 16C and cloudy.",
                },
            )
        ]
        valid_respond.output_text = ""

        mock_client = fake_llm_client(responses=[invalid_structured, valid_respond])
        adapter = LlmctlAdapter(mock_client)

        result = call_structured_with_retry(
            adapter,
            model="test",
            purpose="decide",
            context={
                "messages": [{"role": "user", "content": "what's weather at sf?"}],
                "hints": {"tool_aware": True, "has_prior_results": True},
            },
            schema=DecisionAdapter,
        )

        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("route"), "act")
        self.assertEqual(mock_client.call.call_count, 1)

    def test_llm_adapter_retry_inserts_system_prompt_before_first_user_message(
        self,
    ) -> None:
        try:
            from openminion.modules.brain.adapters.llm import LlmctlAdapter
            from openminion.modules.brain.retry import call_structured_with_retry
            from openminion.modules.brain.schemas import DecisionAdapter
            from openminion.modules.llm.schemas import ToolCall
        except ImportError:
            self.skipTest("openminion modules not installed")

        invalid_structured = MagicMock()
        invalid_structured.ok = True
        invalid_structured.tool_calls = [
            ToolCall(
                id="submit-1",
                name="submit_output",
                arguments={"mode": "act", "confidence": 1.0},
            )
        ]
        invalid_structured.output_text = ""

        valid_response = MagicMock()
        valid_response.ok = True
        valid_response.tool_calls = [
            ToolCall(
                id="submit-2",
                name="submit_output",
                arguments={
                    "mode": "respond",
                    "confidence": 0.8,
                    "reason_code": "fallback_answer",
                    "respond_kind": "answer",
                    "answer": "hello",
                },
            )
        ]
        valid_response.output_text = ""

        mock_client = fake_llm_client(responses=[invalid_structured, valid_response])
        adapter = LlmctlAdapter(mock_client)

        result = call_structured_with_retry(
            adapter,
            model="test",
            purpose="decide",
            context={
                "messages": [
                    {"role": "system", "content": "System contract."},
                    {"role": "user", "content": "hello"},
                ]
            },
            schema=DecisionAdapter,
        )

        self.assertEqual(result.get("route"), "act")
        self.assertEqual(mock_client.call.call_count, 1)

    def test_llm_adapter_decide_plain_text_retry_fails_closed(self) -> None:
        try:
            from openminion.modules.brain.adapters.llm import LlmctlAdapter
            from openminion.modules.brain.retry import call_structured_with_retry
            from openminion.modules.brain.schemas import DecisionAdapter
        except ImportError:
            self.skipTest("openminion modules not installed")

        first_resp = MagicMock()
        first_resp.ok = True
        first_resp.tool_calls = []
        first_resp.output_text = "hello"

        second_resp = MagicMock()
        second_resp.ok = True
        second_resp.tool_calls = []
        second_resp.output_text = "hello again"

        third_resp = MagicMock()
        third_resp.ok = True
        third_resp.tool_calls = []
        third_resp.output_text = "still wrong"

        mock_client = fake_llm_client(responses=[first_resp, second_resp, third_resp])
        adapter = LlmctlAdapter(mock_client)

        result = call_structured_with_retry(
            adapter,
            model="test",
            purpose="decide",
            context={"messages": [{"role": "user", "content": "hi"}]},
            schema=DecisionAdapter,
        )

        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("route"), "respond")
        self.assertEqual(result.get("reason_code"), "invalid_decide_structured_output")
        self.assertIn("internal decision error", str(result.get("answer", "")).lower())
        self.assertEqual(mock_client.call.call_count, 3)

    def test_llm_adapter_decide_invalid_submit_output_args_fail_closed(self) -> None:
        try:
            from openminion.modules.brain.adapters.llm import LlmctlAdapter
            from openminion.modules.brain.retry import call_structured_with_retry
            from openminion.modules.brain.schemas import DecisionAdapter
            from openminion.modules.llm.schemas import ToolCall
        except ImportError:
            self.skipTest("openminion modules not installed")

        first_resp = MagicMock()
        first_resp.ok = True
        first_resp.tool_calls = [
            ToolCall(
                id="submit-1",
                name="submit_output",
                arguments={"title": "Greeting response", "kind": "finish"},
            )
        ]
        first_resp.output_text = ""

        second_resp = MagicMock()
        second_resp.ok = True
        second_resp.tool_calls = [
            ToolCall(
                id="submit-2",
                name="submit_output",
                arguments={"command_id": "bad-shape", "kind": "decision"},
            )
        ]
        second_resp.output_text = ""

        third_resp = MagicMock()
        third_resp.ok = True
        third_resp.tool_calls = [
            ToolCall(
                id="submit-3",
                name="submit_output",
                arguments={"question": "still wrong"},
            )
        ]
        third_resp.output_text = ""

        mock_client = fake_llm_client(responses=[first_resp, second_resp, third_resp])
        adapter = LlmctlAdapter(mock_client)

        result = call_structured_with_retry(
            adapter,
            model="test",
            purpose="decide",
            context={"messages": [{"role": "user", "content": "hi"}]},
            schema=DecisionAdapter,
        )

        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("route"), "respond")
        self.assertEqual(result.get("reason_code"), "invalid_decide_tool_call")
        self.assertIn("internal decision error", str(result.get("answer", "")).lower())
        self.assertEqual(mock_client.call.call_count, 3)

    def test_llm_adapter_decide_tool_choice_survives_through_wrapper(self) -> None:
        try:
            from openminion.modules.brain.adapters.llm import LlmctlAdapter
            from openminion.modules.brain.schemas import DecisionAdapter
            from openminion.services.brain.client import OpenMinionLLMClient
        except ImportError:
            self.skipTest("openminion modules not installed")

        class _Provider:
            name = "test-provider"

            def __init__(self) -> None:
                self.last_request = None

            async def generate(self, req):
                self.last_request = req
                return {
                    "text": "",
                    "model": "test-model",
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                    },
                    "tool_calls": [
                        {
                            "id": "submit-1",
                            "name": "submit_output",
                            "arguments": {
                                "mode": "respond",
                                "confidence": 0.8,
                                "reason_code": "clarify",
                                "respond_kind": "clarify",
                                "question": "Which one?",
                            },
                        }
                    ],
                    "finish_reason": "tool_calls",
                }

        provider = _Provider()
        wrapper = OpenMinionLLMClient(provider)
        adapter = LlmctlAdapter(wrapper)

        result = adapter.call_structured(
            model="test",
            purpose="decide",
            context={"messages": [{"role": "user", "content": "hi"}]},
            schema=DecisionAdapter,
        )

        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("route"), "respond")
        self.assertEqual(result.get("respond_kind"), "clarify")
        self.assertIsNotNone(provider.last_request)
        assert provider.last_request is not None
        self.assertEqual(
            provider.last_request.tool_choice,
            {"type": "function", "function": {"name": "submit_output"}},
        )


class RealToolAndArtifactAdapterTests(unittest.TestCase):
    def test_os_adapter_rejects_incompatible_policy_objects(self) -> None:
        try:
            from openminion.modules.brain.adapters.tool import ToolAdapter
        except ImportError:
            self.skipTest("openminion_tool not installed")

        class _IncompatiblePolicy:
            pass

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError) as ctx:
                ToolAdapter(workspace_root=Path(tmp), policy=_IncompatiblePolicy())
            self.assertIn("policy_mismatch", str(ctx.exception))

    def test_os_adapter_executes_openminion_runtime_registry_tools(self) -> None:
        try:
            from openminion.modules.tool.base import Tool, ToolExecutionResult
            from openminion.modules.brain.adapters.tool import ToolAdapter
        except ImportError:
            self.skipTest("openminion runtime tools not installed")

        class _FakeTool(Tool):
            name = "fake_tool"
            description = "fake tool for adapter compatibility"
            parameters = {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            }

            def execute(self, arguments, context):
                del arguments, context
                return ToolExecutionResult(
                    tool_name=self.name,
                    ok=True,
                    content="ok-from-runtime-tool",
                    verified=True,
                    data={"result": "ok"},
                )

        class _RuntimeRegistry:
            def __init__(self) -> None:
                self._tools = {"fake_tool": _FakeTool()}

        from unittest.mock import patch

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch(
                "openminion.modules.tool.build_default_tool_registry",
                return_value=_RuntimeRegistry(),
            ),
        ):
            adapter = ToolAdapter(workspace_root=Path(tmp))
            res = adapter.execute(
                command={"tool_name": "fake_tool", "args": {}},
                session_id="s1",
                trace_id="t1",
            )

        self.assertEqual(res["status"], "success")
        self.assertEqual(res["summary"], "ok-from-runtime-tool")
        self.assertEqual(res["outputs"]["result"], "ok")

    def test_os_adapter_reuses_prebuilt_runtime_registry(self) -> None:
        try:
            from openminion.modules.brain.adapters.tool import ToolAdapter
            from openminion.modules.tool.registry import ToolRegistry
        except ImportError:
            self.skipTest("openminion runtime tools not installed")

        from unittest.mock import patch

        runtime_registry = ToolRegistry()

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch(
                "openminion.modules.tool.build_default_tool_registry",
                side_effect=AssertionError("should not rebuild runtime registry"),
            ),
        ):
            adapter = ToolAdapter(
                workspace_root=Path(tmp),
                runtime_registry=runtime_registry,
            )

        self.assertIs(adapter.registry, runtime_registry)

    def test_os_adapter(self) -> None:
        try:
            from openminion.modules.brain.adapters.tool import ToolAdapter
        except ImportError:
            self.skipTest(
                "openminion_tool (or its dependencies like pyyaml) not installed"
            )

        with tempfile.TemporaryDirectory() as tmp:
            adapter = ToolAdapter(workspace_root=Path(tmp))
            res = adapter.execute(
                command={"tool_name": "os.exec.run", "args": {"command": "echo hello"}},
                session_id="s1",
                trace_id="t1",
            )
            self.assertIsInstance(res, dict)
            self.assertIn("status", res)

    def test_os_adapter_reactions_accepts_message_context_from_command_inputs(
        self,
    ) -> None:
        try:
            from openminion.modules.tool.runtime.policy import Policy
            from openminion.tools.reaction.plugin import (
                clear_channel_adapters,
                register_channel_adapter,
            )
        except ImportError:
            self.skipTest("openminion.tools.reaction not installed")

        from openminion.modules.brain.adapters.tool import ToolAdapter

        class _Adapter:
            def __init__(self) -> None:
                self.calls: list[tuple] = []

            def react_add(self, message, emoji) -> None:
                self.calls.append(
                    (
                        "add",
                        message.channel,
                        message.conversation_id,
                        message.message_id,
                        emoji,
                    )
                )

        with tempfile.TemporaryDirectory() as tmp:
            reaction_adapter = _Adapter()
            register_channel_adapter("discord", reaction_adapter)
            try:
                adapter = ToolAdapter(workspace_root=Path(tmp), policy=Policy(raw={}))
                res = adapter.execute(
                    command={
                        "tool_name": "reactions.set",
                        "args": {"emoji": "✅"},
                        "inputs": {
                            "message": {
                                "channel": "discord",
                                "conversation_id": "conv-1",
                                "message_id": "msg-1",
                            }
                        },
                    },
                    session_id="s1",
                    trace_id="t1",
                )
            finally:
                clear_channel_adapters()

            self.assertEqual(res["status"], "success")
            self.assertEqual(res["outputs"]["applied"]["action"], "added")
            self.assertEqual(
                reaction_adapter.calls,
                [("add", "discord", "conv-1", "msg-1", "✅")],
            )

    def test_artifact_adapter(self) -> None:
        try:
            from openminion.modules.artifact.control import ArtifactCtl
            from openminion.modules.brain.adapters.artifact import (
                ArtifactctlAdapter,
            )
        except ImportError:
            self.skipTest("openminion.modules.artifact not installed")

        home_root_env = str(os.getenv("OPENMINION_HOME", "")).strip()
        data_root_env = str(os.getenv("OPENMINION_DATA_ROOT", "")).strip()
        home_root = (
            Path(home_root_env).expanduser().resolve()
            if home_root_env
            else Path.cwd().resolve()
        )
        data_root = (
            Path(data_root_env).expanduser().resolve()
            if data_root_env
            else (home_root / ".openminion").resolve()
        )
        temp_root = (data_root / "runtime" / "tests" / "brain-adapters").resolve()
        temp_root.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory(dir=temp_root) as tmp:
            config = {
                "blob_store": {
                    "backend": "filesystem_cas",
                    "root_dir": str(Path(tmp) / "blobs"),
                },
                "index": {
                    "backend": "sqlite",
                    "sqlite_path": str(Path(tmp) / "index.db"),
                    "wal": False,
                },
                "security": {"store_original_path": False, "redaction_enabled": False},
                "retention": {
                    "keep_days": 30,
                    "delete_unreferenced_after_days": 7,
                    "purge_grace_days": 3,
                },
                "aliases": {"expire_default_days": 0},
                "views": {
                    "auto_generate": [],
                    "digest_max_lines": 50,
                    "digest_max_chars": 2000,
                    "table_max_chars": 50000,
                    "table_max_rows": 100,
                },
            }
            actl = ArtifactCtl(config)
            adapter = ArtifactctlAdapter(actl)

            res = adapter.execute(
                command={
                    "tool_name": "create_artifact",
                    "args": {"content": "hello world", "mime": "text/plain"},
                },
                session_id="s1",
                trace_id="t1",
            )
            self.assertEqual(res["status"], "success")
            self.assertIn("id", res["outputs"])

            ref_id = res["outputs"]["id"]
            res_read = adapter.execute(
                command={
                    "tool_name": "read_artifact",
                    "args": {"id": ref_id, "view_type": "text"},
                },
                session_id="s1",
                trace_id="t1",
            )
            self.assertEqual(res_read["status"], "success")
            self.assertEqual(res_read["outputs"]["content"], "hello world")

    def test_artifact_adapter_unknown_tool_contract(self) -> None:
        from unittest.mock import MagicMock

        from openminion.modules.brain.adapters.artifact import ArtifactctlAdapter

        adapter = ArtifactctlAdapter(MagicMock())
        res = adapter.execute(
            command={"tool_name": "missing_tool", "args": {}},
            session_id="s1",
            trace_id="t1",
        )

        self.assertEqual(res["status"], "error")
        self.assertEqual(res["summary"], "Unknown artifact tool: missing_tool")
        self.assertEqual(res["error"]["code"], "NOT_FOUND")
        self.assertIn("missing_tool", res["error"]["message"])
        self.assertIn("latency_ms", res["metrics"])

    def test_tool_adapter_fallback_when_optional_modules_missing(self) -> None:
        from unittest.mock import patch
        from openminion.modules.brain.adapters.tool import ToolAdapter

        with tempfile.TemporaryDirectory() as tmp:

            def failing_build_default_tool_registry(**kwargs):
                raise RuntimeError(
                    "Module-only runtime requires openminion-tool-search-tavily. Module import failed"
                )

            with patch(
                "openminion.modules.tool.build_default_tool_registry",
                side_effect=failing_build_default_tool_registry,
            ):
                adapter = ToolAdapter(workspace_root=Path(tmp))

                self.assertIsNotNone(adapter.registry)

                res = adapter.execute(
                    command={"tool_name": "unknown_test_tool", "args": {}},
                    session_id="s1",
                    trace_id="t1",
                )

                self.assertIn("status", res)
                self.assertIn("error", res)
                self.assertEqual(res["status"], "error")

    def test_tool_adapter_accepts_specs_without_args_model(self) -> None:
        from types import SimpleNamespace
        from openminion.modules.brain.adapters.tool import ToolAdapter

        with tempfile.TemporaryDirectory() as tmp:
            adapter = ToolAdapter(workspace_root=Path(tmp))

            def _handler(arguments, _ctx):
                return {
                    "status": "ok",
                    "summary": f"path={arguments.get('path', '')}",
                    "outputs": {"path": arguments.get("path", "")},
                }

            class _Registry:
                def get(self, name):
                    if name != "file.list":
                        raise KeyError(name)
                    return SimpleNamespace(handler=_handler)

            adapter.registry = _Registry()
            res = adapter.execute(
                command={"tool_name": "file.list", "args": {"path": "."}},
                session_id="s1",
                trace_id="t1",
            )
            self.assertEqual(res["status"], "success")
            self.assertIn("path=.", str(res.get("summary", "")))

    def test_tool_adapter_error_summary_uses_handler_error_message(self) -> None:
        from types import SimpleNamespace
        from openminion.modules.brain.adapters.tool import ToolAdapter

        with tempfile.TemporaryDirectory() as tmp:
            adapter = ToolAdapter(workspace_root=Path(tmp))

            def _handler(_arguments, _ctx):
                return {
                    "ok": False,
                    "error": {
                        "code": "DEPENDENCY_MISSING",
                        "message": "Playwright browser runtime is not ready",
                    },
                }

            class _Registry:
                def get(self, name):
                    if name != "browser.playwright.health":
                        raise KeyError(name)
                    return SimpleNamespace(handler=_handler)

            adapter.registry = _Registry()
            res = adapter.execute(
                command={"tool_name": "browser.playwright.health", "args": {}},
                session_id="s1",
                trace_id="t1",
            )
            self.assertEqual(res["status"], "error")
            self.assertIn("not ready", str(res.get("summary", "")).lower())
            self.assertEqual(
                str(res.get("error", {}).get("code", "")), "DEPENDENCY_MISSING"
            )

    def test_tool_adapter_success_summary_uses_content_when_summary_missing(
        self,
    ) -> None:
        from types import SimpleNamespace
        from openminion.modules.brain.adapters.tool import ToolAdapter

        with tempfile.TemporaryDirectory() as tmp:
            adapter = ToolAdapter(workspace_root=Path(tmp))

            def _handler(_arguments, _ctx):
                return {
                    "ok": True,
                    "content": "search results content",
                    "source": "tavily",
                    "data": {"results": [{"title": "example"}]},
                }

            class _Registry:
                def get(self, name):
                    if name != "web.search":
                        raise KeyError(name)
                    return SimpleNamespace(handler=_handler)

            adapter.registry = _Registry()
            res = adapter.execute(
                command={"tool_name": "web.search", "args": {"query": "korea news"}},
                session_id="s1",
                trace_id="t1",
            )
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["summary"], "search results content")

    def test_tool_adapter_success_summary_uses_data_when_content_missing(self) -> None:
        from types import SimpleNamespace
        from openminion.modules.brain.adapters.tool import ToolAdapter

        with tempfile.TemporaryDirectory() as tmp:
            adapter = ToolAdapter(workspace_root=Path(tmp))

            def _handler(_arguments, _ctx):
                return {
                    "ok": True,
                    "data": {"content": "Fetched https://example.com (200)"},
                }

            class _Registry:
                def get(self, name):
                    if name != "web.fetch":
                        raise KeyError(name)
                    return SimpleNamespace(handler=_handler)

            adapter.registry = _Registry()
            res = adapter.execute(
                command={
                    "tool_name": "web.fetch",
                    "args": {"url": "https://example.com"},
                },
                session_id="s1",
                trace_id="t1",
            )
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["summary"], "Fetched https://example.com (200)")

    def test_tool_adapter_success_summary_synthesizes_from_data_payload(self) -> None:
        from types import SimpleNamespace
        from openminion.modules.brain.adapters.tool import ToolAdapter

        with tempfile.TemporaryDirectory() as tmp:
            adapter = ToolAdapter(workspace_root=Path(tmp))

            def _handler(_arguments, _ctx):
                return {
                    "ok": True,
                    "data": {
                        "city": "San Francisco",
                        "timezone": "America/Los_Angeles",
                    },
                }

            class _Registry:
                def get(self, name):
                    if name != "location":
                        raise KeyError(name)
                    return SimpleNamespace(handler=_handler)

            adapter.registry = _Registry()
            res = adapter.execute(
                command={"tool_name": "location", "args": {"city": "San Francisco"}},
                session_id="s1",
                trace_id="t1",
            )
            self.assertEqual(res["status"], "success")
            self.assertIn("San Francisco", res["summary"])

    def test_tool_adapter_success_summary_synthesizes_from_payload_fields(self) -> None:
        from types import SimpleNamespace
        from openminion.modules.brain.adapters.tool import ToolAdapter

        with tempfile.TemporaryDirectory() as tmp:
            adapter = ToolAdapter(workspace_root=Path(tmp))

            def _handler(_arguments, _ctx):
                return {
                    "ok": True,
                    "task_id": "task-123",
                    "name": "Joke Generator",
                    "schedule": {"kind": "every", "every_ms": 60000},
                }

            class _Registry:
                def get(self, name):
                    if name != "task.schedule":
                        raise KeyError(name)
                    return SimpleNamespace(handler=_handler)

            adapter.registry = _Registry()
            res = adapter.execute(
                command={
                    "tool_name": "task.schedule",
                    "args": {"name": "Joke Generator"},
                },
                session_id="s1",
                trace_id="t1",
            )
            self.assertEqual(res["status"], "success")
            self.assertIn("task-123", str(res.get("summary", "")))
            self.assertNotEqual(res.get("summary"), "Tool executed successfully")

    def test_tool_adapter_handles_runtime_tool_from_registry_get(self) -> None:
        from openminion.modules.brain.adapters.tool import ToolAdapter
        from openminion.modules.tool.base import ToolExecutionResult

        with tempfile.TemporaryDirectory() as tmp:
            adapter = ToolAdapter(workspace_root=Path(tmp))

            class _RuntimeTool:
                name = "list_files"

                def execute(self, arguments, context):
                    del context
                    return ToolExecutionResult(
                        tool_name="list_files",
                        ok=True,
                        content=f"path={arguments.get('path', '')}",
                        verified=True,
                    )

            class _Registry:
                def get(self, name):
                    if name != "list_files":
                        raise KeyError(name)
                    return _RuntimeTool()

            adapter.registry = _Registry()
            res = adapter.execute(
                command={"tool_name": "list_files", "args": {"path": "."}},
                session_id="s1",
                trace_id="t1",
            )
            self.assertEqual(res["status"], "success")
            self.assertIn("path=.", str(res.get("summary", "")))

    def test_tool_adapter_runtime_tool_receives_policy_replay_metadata(self) -> None:
        from openminion.modules.brain.adapters.tool import ToolAdapter
        from openminion.modules.tool.base import ToolExecutionResult

        with tempfile.TemporaryDirectory() as tmp:
            adapter = ToolAdapter(workspace_root=Path(tmp))
            captured: dict[str, object] = {}

            class _RuntimeTool:
                name = "file.write"

                def execute(self, arguments, context):
                    del arguments
                    captured["metadata"] = dict(context.metadata)
                    return ToolExecutionResult(
                        tool_name="file.write",
                        ok=True,
                        content="ok",
                        verified=True,
                    )

            class _Registry:
                def get(self, name):
                    if name != "file.write":
                        raise KeyError(name)
                    return _RuntimeTool()

            adapter.registry = _Registry()
            res = adapter.execute(
                command={
                    "tool_name": "file.write",
                    "args": {"path": "x.txt", "content": "x"},
                    "inputs": {
                        "confirmation_source": "policy_replay",
                        "confirmation_grant_id": "grant-test",
                    },
                },
                session_id="s1",
                trace_id="t1",
            )

            self.assertEqual(res["status"], "success")
            self.assertEqual(
                captured["metadata"]["confirmation_source"], "policy_replay"
            )
            self.assertEqual(
                captured["metadata"]["confirmation_grant_id"], "grant-test"
            )


class RealPolicyAndA2aAdapterTests(unittest.TestCase):
    def test_a2a_adapter(self) -> None:
        from openminion.modules.brain.adapters.a2a import A2actlAdapter

        with tempfile.TemporaryDirectory() as tmp:
            adapter = A2actlAdapter(home_root=Path(tmp))
            try:
                res_sync = adapter.call(
                    command={
                        "target_agent_id": "agent.echo",
                        "method": "echo.ping",
                        "params": {"message": "hi"},
                    },
                    session_id="s1",
                    trace_id="t1",
                )
                self.assertEqual(res_sync["status"], "success")
                self.assertEqual(res_sync["outputs"]["agent"], "agent.echo")
                self.assertEqual(res_sync["outputs"]["method"], "echo.ping")

                res_async = adapter.call(
                    command={
                        "target_agent_id": "agent.worker",
                        "method": "job.sleep",
                        "params": {"seconds": 0.01},
                        "expect_async": True,
                    },
                    session_id="s1",
                    trace_id="t1",
                )
                self.assertEqual(res_async["status"], "running")
                self.assertIn("task_id", res_async)
            finally:
                adapter.close()

    def test_policy_adapter(self) -> None:
        try:
            from openminion.modules.policy.runtime.service import PolicyCtl
            from openminion.modules.policy.adapters.brain import PolicyCtlBrainAdapter
            from openminion.modules.brain.schemas import (
                ToolCommand,
                WorkingState,
                BudgetCounters,
            )
        except ImportError:
            self.skipTest("openminion.modules.policy not installed")

        with tempfile.TemporaryDirectory() as tmp:
            actl = PolicyCtl.with_sqlite(Path(tmp) / "policy.db")
            adapter = PolicyCtlBrainAdapter(actl)

            state = WorkingState(
                session_id="s1",
                agent_id="a1",
                budgets_remaining=BudgetCounters(
                    ticks=10, tool_calls=5, a2a_calls=5, tokens=100, time_ms=1000
                ),
                status="active",
                trace_id="t1",
            )
            cmd = ToolCommand(
                title="x",
                tool_name="echo",
                args={"message": "hello"},
                risk_level="low",
                idempotency_key="i1",
                success_criteria={},
            )

            res = adapter.evaluate(
                command=cmd,
                working_state=state,
                session_context={"subject_id": "user1"},
            )

            self.assertEqual(res.outcome, "ALLOW")

    def test_policy_adapter_maps_require_clarification_decision(self) -> None:
        from openminion.modules.policy.adapters.brain import PolicyCtlBrainAdapter
        from openminion.modules.brain.schemas import (
            ToolCommand,
            WorkingState,
            BudgetCounters,
        )

        class _PolicyCtl:
            def check(self, invocation, ctx, *, risk_override=None):
                del invocation, ctx, risk_override
                return SimpleNamespace(
                    decision="REQUIRE_CLARIFICATION",
                    reason_code="MISSING_REQUIRED_FIELD",
                    reason="Missing required field.",
                    details={"clarification_question": "Which location should I use?"},
                )

        adapter = PolicyCtlBrainAdapter(_PolicyCtl())
        state = WorkingState(
            session_id="s1",
            agent_id="a1",
            budgets_remaining=BudgetCounters(
                ticks=10, tool_calls=5, a2a_calls=5, tokens=100, time_ms=1000
            ),
            status="active",
            trace_id="t1",
        )
        cmd = ToolCommand(
            title="weather",
            tool_name="weather.openmeteo.current",
            args={},
            risk_level="low",
            idempotency_key="i1",
            success_criteria={},
        )

        result = adapter.evaluate(
            command=cmd,
            working_state=state,
            session_context={},
        )
        self.assertEqual(result.outcome, "REQUIRE_CLARIFICATION")
        self.assertTrue(result.require_clarification)
        self.assertEqual(result.clarification_question, "Which location should I use?")

    def test_policy_adapter_promotes_details_require_clarification(self) -> None:
        from openminion.modules.policy.adapters.brain import PolicyCtlBrainAdapter
        from openminion.modules.brain.schemas import (
            ToolCommand,
            WorkingState,
            BudgetCounters,
        )

        class _PolicyCtl:
            def check(self, invocation, ctx, *, risk_override=None):
                del invocation, ctx, risk_override
                return SimpleNamespace(
                    decision="ALLOW",
                    reason_code="ALLOW_WITH_CLARIFY",
                    reason="Need one missing argument before execution.",
                    details={
                        "require_clarification": True,
                        "clarification_question": "What value should `path` use?",
                    },
                )

        adapter = PolicyCtlBrainAdapter(_PolicyCtl())
        state = WorkingState(
            session_id="s1",
            agent_id="a1",
            budgets_remaining=BudgetCounters(
                ticks=10, tool_calls=5, a2a_calls=5, tokens=100, time_ms=1000
            ),
            status="active",
            trace_id="t1",
        )
        cmd = ToolCommand(
            title="read",
            tool_name="read_file",
            args={},
            risk_level="low",
            idempotency_key="i2",
            success_criteria={},
        )

        result = adapter.evaluate(
            command=cmd,
            working_state=state,
            session_context={},
        )
        self.assertEqual(result.outcome, "REQUIRE_CLARIFICATION")
        self.assertTrue(result.require_clarification)
        self.assertEqual(result.clarification_question, "What value should `path` use?")


class RealMemctlAndSafetyctlAdapterTests(unittest.TestCase):
    def test_safety_adapter(self) -> None:
        from openminion.modules.brain.adapters.safety import SafetyctlAdapter
        from openminion.modules.brain.schemas import (
            ToolCommand,
            WorkingState,
            BudgetCounters,
        )

        adapter = SafetyctlAdapter()
        state = WorkingState(
            session_id="s1",
            agent_id="a1",
            budgets_remaining=BudgetCounters(
                ticks=10, tool_calls=5, a2a_calls=5, tokens=100, time_ms=1000
            ),
            status="active",
            trace_id="t1",
        )
        cmd = ToolCommand(
            title="x",
            tool_name="rm",
            args={"path": "/"},
            risk_level="high",
            idempotency_key="i1",
            success_criteria={},
        )

        res = adapter.evaluate(
            command=cmd, working_state=state, session_context={"subject_id": "user1"}
        )

        self.assertEqual(res.outcome, "ALLOW")

    def test_memory_adapter(self) -> None:
        try:
            from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore
            from openminion.modules.brain.adapters.memory import MemctlAdapter
        except ImportError:
            self.skipTest("openminion_memory not installed")

        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteMemoryStore(Path(tmp) / "memory.db")
            adapter = MemctlAdapter(store)

            rec_id = adapter.put_record(
                scope="brain", record_type="fact", title="test", content={"x": 1}
            )
            self.assertTrue(rec_id.startswith("mem_"))

            cand_id = adapter.stage_candidate(
                scope="brain", record_type="fact", title="test cand", content={"y": 2}
            )
            self.assertTrue(cand_id.startswith("cand_"))
