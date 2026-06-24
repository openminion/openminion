from __future__ import annotations

from pathlib import Path
from typing import Any

from openminion.modules.controlplane.runtime.parser import SlashCommandParser
from openminion.modules.controlplane.commands.registry import CommandRegistry
from openminion.modules.controlplane.runtime.auth import AuthEvaluator
from openminion.modules.controlplane.runtime.dispatcher import ControlPlaneDispatcher
from openminion.modules.controlplane.contracts.models import InboundMessage
from openminion.modules.controlplane.runtime.router import Router
from openminion.modules.controlplane.runtime.store import InMemoryControlPlaneStore
from openminion.modules.controlplane.storage.sqlite import SQLiteControlPlaneStore


class _CompletedBrain:
    contract_version = "v1"

    def __init__(self) -> None:
        self.calls = 0

    def run(
        self,
        *,
        session_id: str,
        agent_id: str,
        user_text: str | None,
        attachment_refs: list[str],
        trace_id: str,
    ) -> dict[str, Any]:
        self.calls += 1
        return {
            "text": f"resolved: {user_text}",
            "status": "completed",
            "trace_id": trace_id,
        }


def _build_dispatcher(store: Any, brain: Any | None = None) -> ControlPlaneDispatcher:
    return ControlPlaneDispatcher(
        store=store,
        router=Router(store),
        parser=SlashCommandParser(),
        command_registry=CommandRegistry(
            store=store, auth=AuthEvaluator(admin_user_keys=[])
        ),
        brain_client=brain or _CompletedBrain(),
    )


def test_empty_store_yields_empty_pending_map() -> None:
    store = InMemoryControlPlaneStore()
    dispatcher = _build_dispatcher(store)
    assert dispatcher._pending_clarify_by_session == {}


def test_two_pending_clarifies_seed_in_memory_map() -> None:
    store = InMemoryControlPlaneStore()
    store.set_pending_clarify(
        "sess-A",
        {
            "clarify_id": "clar-A",
            "trace_id": "trace-A",
            "session_id": "sess-A",
            "questions": [
                {
                    "id": "q1",
                    "question": "?",
                    "type": "ambiguous_input",
                    "is_blocking": True,
                }
            ],
        },
    )
    store.set_pending_clarify(
        "sess-B",
        {
            "clarify_id": "clar-B",
            "trace_id": "trace-B",
            "session_id": "sess-B",
            "questions": [],
        },
    )

    dispatcher = _build_dispatcher(store)
    pending = dispatcher._pending_clarify_by_session

    assert set(pending.keys()) == {"sess-A", "sess-B"}
    assert pending["sess-A"]["clarify_id"] == "clar-A"
    assert pending["sess-A"]["trace_id"] == "trace-A"
    assert pending["sess-B"]["clarify_id"] == "clar-B"


def test_dispatch_consumes_clarify_from_store_and_memory(tmp_path: Path) -> None:

    db_path = tmp_path / "cp.db"
    store = SQLiteControlPlaneStore(db_path)

    # Bind the chat -> session so the second dispatcher (and the dispatch
    # call below) resolves to the same session id we seeded with the
    # pending clarify row.
    session_id = store.resolve_session("telegram:1", "telegram:chat-1")
    trace_id = "trace-consume-1"
    store.set_pending_clarify(
        session_id,
        {
            "clarify_id": "clar-consume",
            "trace_id": trace_id,
            "session_id": session_id,
            "questions": [
                {
                    "id": "q-city",
                    "question": "Which city?",
                    "type": "missing_field",
                    "is_blocking": True,
                }
            ],
        },
    )

    brain = _CompletedBrain()
    dispatcher = _build_dispatcher(store, brain=brain)

    # Hydration kicked in: the rebuilt dispatcher already knows about it.
    assert session_id in dispatcher._pending_clarify_by_session

    # Feed the clarify answer with the matching clarify_id and trace_id.
    payload = dispatcher.handle_inbound(
        InboundMessage(
            user_key="telegram:1",
            chat_key="telegram:chat-1",
            text="San Diego",
            channel="telegram",
            user_id="1",
            chat_id="chat-1",
            metadata={
                "trace_id": trace_id,
                "clarify_answer": {
                    "clarify_id": "clar-consume",
                    "question_id": "q-city",
                    "answer": "San Diego",
                },
            },
        )
    )

    assert payload["status"] == "completed"
    assert brain.calls == 1

    # Both the durable store and the in-memory map must be empty for this
    # session — the dispatcher consumed the pending clarify on resolution.
    assert session_id not in dispatcher._pending_clarify_by_session
    assert store.get_pending_clarify(session_id) is None

    store.close()
