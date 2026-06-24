import tempfile
import unittest
import logging
from pathlib import Path

from openminion.modules.session.storage.sqlite_store import SQLiteSessionStore
from openminion.modules.brain.runner import BrainRunner, RunnerOptions
from openminion.modules.brain.schemas import (
    AgentProfile,
    AgentBudgets,
    LLMProfiles,
    AgentDefaults,
)
from openminion.modules.brain.adapters.tool import LocalToolAdapter
from openminion.modules.brain.adapters.a2a import LocalA2AAdapter
from openminion.modules.brain.adapters.memory import LocalMemoryAdapter
from openminion.modules.brain.adapters.policy import LocalPolicyAdapter
from openminion.modules.brain.adapters.session import SessctlAdapter
from openminion.modules.brain.adapters.context import ContextCtlAdapter
from openminion.modules.context.service import ContextCtlService
from openminion.modules.context.contracts import (
    IdentityClient,
    MemoryClient,
    ArtifactClient,
)

logging.basicConfig(level=logging.ERROR)


class MockIdentityClient(IdentityClient):
    contract_version = "v1"

    def render(
        self, agent_id, purpose, max_tokens, provider_pref=None, query_text=None
    ):
        from openminion.modules.context.schemas import RenderMessage

        return type(
            "MockIdentity",
            (),
            {
                "text": "You are a test helper",
                "profile_version": "v1",
                "render_version": "v1",
                "system_messages": [
                    RenderMessage(
                        role="system",
                        content="You are a test helper",
                        bucket="static_prefix",
                    )
                ],
            },
        )()


class MockMemoryClient(MemoryClient):
    contract_version = "v1"

    def query_facts(self, **kwargs):
        return []

    def query_memory_cards(self, **kwargs):
        return []

    def recall_session_start_memory(self, **kwargs):
        return []

    def recall_mid_session_memory(self, **kwargs):
        return []

    def recall_recent_session_artifacts(self, **kwargs):
        return []

    def get_procedure(self, **kwargs):
        return None


class MockArtifactClient(ArtifactClient):
    contract_version = "v1"

    def fetch_manifest(self, **kwargs):
        return type("MockArtManifest", (), {"items": []})()

    def query_digests(self, **kwargs):
        return []


class E2ESessionClientWrapper:
    contract_version = "v1"

    def __init__(self, adapter):
        self.adapter = adapter

    def get_slice(self, session_id, purpose, limits):
        from openminion.modules.context.schemas import SessionSlice

        raw = self.adapter.get_slice(
            session_id=session_id, purpose=purpose, limits=limits
        )

        # Map fields to what ContextCtlService / SessionSlice expects
        turns = []
        for t in raw.get("recent_turns", []):
            role = t.get("turn_type", "user")
            turns.append(
                {
                    "turn_id": t.get("turn_id"),
                    "role": role,
                    "content": t.get("text", ""),
                    "tool_events": t.get("tool_events", []),
                    "ui_hints": t.get("ui_hints", {}),
                }
            )
        raw["recent_turns"] = turns

        if "summary_short" not in raw:
            raw["summary_short"] = ""

        return SessionSlice.model_validate(raw)

    def emit_canonical_event(self, **kwargs):
        if hasattr(self.adapter, "emit_canonical_event"):
            self.adapter.emit_canonical_event(**kwargs)

    def append_event(self, **kwargs):
        if hasattr(self.adapter, "append_event"):
            self.adapter.append_event(**kwargs)


class MockLLMAPI:
    def estimate_tokens(self, **kwargs):
        return 10

    def call_structured(self, **kwargs):
        # depending on purpose, return mock schema
        purpose = kwargs.get("purpose")
        if purpose == "decide":
            return {"mode": "plan", "confidence": "high", "reason_code": "test"}
        if purpose == "plan":
            return {
                "steps": [
                    {
                        "title": "t",
                        "command": {
                            "kind": "finish",
                            "command_id": "1",
                            "final_message": "done",
                        },
                    }
                ]
            }
        return {}


class T17IntegrationTests(unittest.TestCase):
    def test_e2e_ctxctl_brain_sessctl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = SQLiteSessionStore(root / "sessions.db")
            session_adapter = SessctlAdapter(root / "sessions.db")

            ctx_service = ContextCtlService(
                identityctl=MockIdentityClient(),
                sessctl=E2ESessionClientWrapper(
                    session_adapter
                ),  # Wiring sessctl into ctxctl!
                memctl=MockMemoryClient(),
                artifactctl=MockArtifactClient(),
            )
            context_adapter = ContextCtlAdapter(ctx_service)

            budgets = AgentBudgets(
                max_ticks_per_user_turn=2,
                max_tool_calls=1,
                max_a2a_calls=0,
                max_total_llm_tokens=1000,
                max_elapsed_ms=5000,
            )
            llm_profiles = LLMProfiles(
                decide_model="mock",
                plan_model="mock",
                reflect_model="mock",
                summarize_model="mock",
            )
            profile = AgentProfile(
                agent_id="agent-e2e",
                role="test",
                llm_profiles=llm_profiles,
                tool_policy=None,
                memory_read_scopes=[],
                memory_write_scopes={},
                budgets=budgets,
                defaults=AgentDefaults(),
            )

            runner = BrainRunner(
                profile=profile,
                session_api=session_adapter,
                context_api=context_adapter,  # Wiring ctxctl into brain!
                llm_api=MockLLMAPI(),
                tool_api=LocalToolAdapter(),
                a2a_api=LocalA2AAdapter(),
                memory_api=LocalMemoryAdapter(root / "memory"),
                policy_api=LocalPolicyAdapter(),
                options=RunnerOptions(reflection_enabled=False, metactl_enabled=False),
            )

            store.create_session(session_id="s-e2e")

            # Since LLM is None, runner evaluates heuristics or raises.
            # But the goal of T17 is to prove context + session exchange works and manifest runs.
            # A heuristic "plan" input will trigger plan which builds context!
            res = runner.step(session_id="s-e2e", user_input="plan ahead")
            print("RUNNER STEP RESULT:", res)

            # Check if canonical events were written
            events = session_adapter.list_events("s-e2e")
            print(
                "EVENTS IN DB:",
                [(e.get("type", e.get("event_type")), e.get("error")) for e in events],
            )
            event_types = {e.get("type", e.get("event_type")) for e in events}

            self.assertIn("turn.user", event_types)
            self.assertTrue(
                "context.manifest.created" in event_types
                or "context.manifest" in event_types
            )

            # Verify the manifest content
            manifest_event = next(
                e
                for e in events
                if e.get("type", e.get("event_type"))
                in {"context.manifest.created", "context.manifest"}
            )
            if "payload" in manifest_event:  # append_event
                pass
            self.assertEqual(
                manifest_event.get("actor_id", manifest_event.get("agent_id")),
                "agent-e2e",
            )
