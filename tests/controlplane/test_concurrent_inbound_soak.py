from __future__ import annotations

import threading
import time
from statistics import quantiles

import pytest

from openminion.modules.controlplane.contracts.models import InboundMessage
from openminion.modules.controlplane.runtime import EchoBrain
from openminion.modules.controlplane.runtime.audit import AuditLogger
from openminion.modules.controlplane.runtime.auth import AuthEvaluator
from openminion.modules.controlplane.runtime.dispatcher import ControlPlaneDispatcher
from openminion.modules.controlplane.runtime.parser import SlashCommandParser
from openminion.modules.controlplane.runtime.router import Router
from openminion.modules.controlplane.storage.sqlite import SQLiteControlPlaneStore
from openminion.modules.controlplane.commands.registry import CommandRegistry


@pytest.mark.slow
def test_dispatcher_handles_32_thread_inbound_soak(tmp_path) -> None:
    store = SQLiteControlPlaneStore(tmp_path / "cp.db")
    audit = AuditLogger(sink=store.put_audit)
    outbound: list[dict[str, object]] = []
    outbound_lock = threading.Lock()

    def _capture(payload: dict[str, object]) -> None:
        with outbound_lock:
            outbound.append(payload)

    dispatcher = ControlPlaneDispatcher(
        store=store,
        router=Router(store),
        parser=SlashCommandParser(),
        command_registry=CommandRegistry(
            store=store,
            auth=AuthEvaluator(admin_user_keys=[]),
        ),
        brain_client=EchoBrain(),
        audit_logger=audit,
        outbound_sender=_capture,
    )
    barrier = threading.Barrier(32)
    durations: list[float] = []
    duration_lock = threading.Lock()
    errors: list[BaseException] = []

    def _worker(worker_id: int) -> None:
        try:
            barrier.wait(timeout=5)
            for message_idx in range(10):
                started = time.perf_counter()
                dispatcher.handle_inbound(
                    InboundMessage(
                        channel="telegram",
                        user_key=f"telegram:user:{worker_id}",
                        chat_key=f"telegram:chat:{worker_id}",
                        user_id=str(worker_id),
                        chat_id=str(worker_id),
                        text=f"message {worker_id}-{message_idx}",
                        metadata={"trace_id": f"trace-soak-{worker_id}-{message_idx}"},
                    )
                )
                with duration_lock:
                    durations.append(time.perf_counter() - started)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [
        threading.Thread(target=_worker, args=(idx,), name=f"cp-soak-{idx}")
        for idx in range(32)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    try:
        assert errors == []
        assert all(not thread.is_alive() for thread in threads)
        assert len(outbound) == 320
        assert len({payload["session_id"] for payload in outbound}) == 32
        stored_sessions = {
            store.resolve_session(f"telegram:user:{idx}", f"telegram:chat:{idx}")
            for idx in range(32)
        }
        assert len(stored_sessions) == 32
        p99 = quantiles(durations, n=100)[98]
        assert p99 < 0.5
    finally:
        store.close()
