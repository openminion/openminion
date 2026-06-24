from __future__ import annotations

from pathlib import Path
from typing import Any

from openminion.modules.controlplane.runtime.audit import AuditLogger
from openminion.modules.controlplane.runtime.auth import AuthEvaluator
from openminion.modules.controlplane.runtime.parser import SlashCommandParser
from openminion.modules.controlplane.commands.registry import CommandRegistry
from openminion.modules.controlplane.runtime.dispatcher import ControlPlaneDispatcher
from openminion.modules.controlplane.contracts.models import InboundMessage
from openminion.modules.controlplane.runtime.router import Router
from openminion.modules.controlplane.storage.sqlite import SQLiteControlPlaneStore


class _ClarifyBrain:
    contract_version = "v1"

    def __init__(self) -> None:
        self.calls = 0
        self.trace_ids: list[str] = []
        self.session_ids: list[str] = []

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
        self.trace_ids.append(trace_id)
        self.session_ids.append(session_id)
        if self.calls == 1:
            return {
                "text": "Which city should I check weather for?",
                "status": "waiting_user",
                "trace_id": trace_id,
                "clarify_request": {
                    "clarify_id": "clarify-restart-1",
                    "trace_id": trace_id,
                    "session_id": session_id,
                    "blocking": True,
                    "questions": [
                        {
                            "id": "q-city",
                            "type": "missing_field",
                            "question": "Which city?",
                            "is_blocking": True,
                        }
                    ],
                },
            }
        return {
            "text": f"Weather for {user_text}: 68F and clear.",
            "status": "completed",
            "trace_id": trace_id,
        }


def _build_dispatcher(
    db_path: Path, brain: _ClarifyBrain
) -> tuple[
    ControlPlaneDispatcher,
    SQLiteControlPlaneStore,
    list[dict[str, Any]],
    AuditLogger,
]:
    store = SQLiteControlPlaneStore(db_path)
    audit = AuditLogger(sink=store.put_audit)
    outbound: list[dict[str, Any]] = []
    dispatcher = ControlPlaneDispatcher(
        store=store,
        router=Router(store),
        parser=SlashCommandParser(),
        command_registry=CommandRegistry(
            store=store, auth=AuthEvaluator(admin_user_keys=[])
        ),
        brain_client=brain,
        audit_logger=audit,
        outbound_sender=outbound.append,
    )
    return dispatcher, store, outbound, audit


def test_session_resume_across_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "cp.db"

    brain = _ClarifyBrain()
    dispatcher_1, store_1, outbound_1, _audit_1 = _build_dispatcher(db_path, brain)

    first_payload = dispatcher_1.handle_inbound(
        InboundMessage(
            user_key="telegram:77",
            chat_key="telegram:200",
            text="what's the weather today?",
            channel="telegram",
            user_id="77",
            chat_id="200",
        )
    )
    assert first_payload["type"] == "chat"
    assert first_payload["status"] == "waiting_user"
    assert first_payload["clarify"] is not None
    assert first_payload["clarify"]["clarify_id"] == "clarify-restart-1"

    original_session_id = first_payload["session_id"]
    original_trace_id = brain.trace_ids[0]

    persisted = store_1.get_pending_clarify(original_session_id)
    assert persisted is not None, (
        "dispatcher must persist pending clarifies through the store"
    )
    assert persisted["clarify_id"] == "clarify-restart-1"

    store_1.close()

    dispatcher_2, store_2, outbound_2, _audit_2 = _build_dispatcher(db_path, brain)

    sessions = store_2.list_sessions("telegram:77", "telegram:200")
    assert any(s["session_id"] == original_session_id for s in sessions), (
        f"session row did not survive restart: {sessions}"
    )
    pending = store_2.get_pending_clarify(original_session_id)
    assert pending is not None
    assert pending["clarify_id"] == "clarify-restart-1"
    assert pending["trace_id"] == original_trace_id

    assert original_session_id in dispatcher_2._pending_clarify_by_session
    assert (
        dispatcher_2._pending_clarify_by_session[original_session_id]["clarify_id"]
        == "clarify-restart-1"
    )

    resumed_payload = dispatcher_2.handle_inbound(
        InboundMessage(
            user_key="telegram:77",
            chat_key="telegram:200",
            text="San Diego",
            channel="telegram",
            user_id="77",
            chat_id="200",
            metadata={
                "trace_id": original_trace_id,
                "clarify_answer": {
                    "clarify_id": "clarify-restart-1",
                    "question_id": "q-city",
                    "answer": "San Diego",
                },
            },
        )
    )

    assert resumed_payload["session_id"] == original_session_id
    assert resumed_payload["status"] == "completed"
    assert "San Diego" in resumed_payload["text"]
    assert outbound_2[-1]["session_id"] == original_session_id

    assert brain.calls == 2
    assert brain.trace_ids[0] == brain.trace_ids[1] == original_trace_id

    assert len(outbound_1) == 1
    assert len(outbound_2) == 1

    store_2.close()
