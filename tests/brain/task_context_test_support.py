"""Local source-level harness: real SQLite, context selection and research child loop."""

from collections import defaultdict, deque
from pathlib import Path
from unittest.mock import patch

from openminion.modules.brain.adapters.context import ContextCtlAdapter
from openminion.modules.brain.adapters.context.bridges.session import (
    BridgeSessionClient,
)
from openminion.modules.brain.adapters.session.runtime import SessctlAdapter
from openminion.modules.brain.loop.strategies.research import ResearchMode
from tests.brain.test_unified_turn_entry import _build_runner, _tool_response
from tests.context.test_service_behavior import _make_service


class RequestCaptured(BaseException):
    """Abort before any real provider inference or task-tool execution."""


class ScriptedProvider:
    def __init__(self):
        self.responses = defaultdict(deque)
        self.requests = []
        self.actual = {}
        self.entry_session = None

    def estimate_tokens(self, *, model, context):
        return 1

    def call(self, request):
        self.requests.append(request)
        sid = request.metadata.get("session_id") or self.entry_session
        purpose = request.metadata.get("purpose")
        if purpose == "entry":
            assert self.responses[sid], f"Unexpected entry request for {sid}"
            return self.responses[sid].popleft().model_copy(deep=True)
        assert purpose == "act", f"Unexpected provider purpose: {purpose}"
        self.actual[sid] = request
        raise RequestCaptured()


class Harness:
    def __init__(self, root):
        self.root = Path(root)
        self.provider = ScriptedProvider()
        self.queries = {}
        self.observations = {}
        self.statuses = []
        self.connect()

    def connect(self):
        self.runner = _build_runner(self.root / "support", llm_api=self.provider)
        self.runner.profile.budgets.max_ticks_per_user_turn = 1000
        self.runner.profile.budgets.max_total_llm_tokens = 50000
        self.runner.session_api = SessctlAdapter(self.root / "sessions.db")
        self.runner.context_api = ContextCtlAdapter(
            _make_service(session=BridgeSessionClient(self.runner.session_api.store))
        )

    def initialize(self, sid):
        state = self.runner._load_or_init_state(sid)
        state.llm_calls_max = 1000
        self.runner._save_state(state)

    def restart(self):
        self.runner.session_api.close()
        self.connect()

    def close(self):
        self.runner.session_api.close()

    def plan(self, sid, questions=(), final="research", prefix=()):
        self.provider.responses[sid].extend(prefix)
        for question in questions:
            # Existing entry reconsideration retries a clarify route once.
            for _ in range(2):
                self.provider.responses[sid].append(
                    _tool_response("clarify", {"question": question})
                )
        response = _tool_response(
            final,
            {"sub_intents": []} if final == "research" else {"name": "file.write"},
        )
        response.tool_calls[0].id = f"{sid}-entry-call"
        self.provider.responses[sid].append(response)

    def send(self, sid, text, expected):
        self.provider.entry_session = sid
        original = ResearchMode.execute

        def observe(mode, ctx):
            self.queries[ctx.state.session_id] = ctx.decision.research_query
            return original(mode, ctx)

        try:
            with patch.object(ResearchMode, "execute", observe):
                result = self.runner.step(session_id=sid, user_input=text)
        except RequestCaptured:
            assert expected == "request", f"Unexpected actual request for {sid}"
            self.statuses.append({"session": sid, "result": "actual_request"})
            return self.provider.actual[sid]
        assert result.status == expected, result.message
        self.statuses.append({"session": sid, "result": result.status})
        return result

    def text(self, sid):
        return "\n".join(m.content for m in self.provider.actual[sid].messages)

    def persisted(self, sid):
        return "\n".join(
            str(t.get("content") or t.get("text") or "")
            for t in self.runner.session_api.list_turns(sid)
        )

    def report(self):
        import openminion.modules.brain.loop.adaptive.modes as modes

        return {
            "source": modes.__file__,
            "observations": self.observations,
            "statuses": self.statuses,
            "queries": self.queries,
            "actual_requests": {
                sid: {
                    "purpose": req.metadata.get("purpose"),
                    "messages": [
                        {
                            "role": m.role,
                            "content": m.content,
                            "block_kind": m.meta.get("block_kind"),
                            "tool_call_id": m.tool_call_id,
                            "calls": [
                                {"id": c.id, "name": c.name} for c in m.tool_calls
                            ],
                        }
                        for m in req.messages
                    ],
                }
                for sid, req in self.provider.actual.items()
            },
        }
