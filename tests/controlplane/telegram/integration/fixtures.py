from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from openminion.modules.controlplane.contracts.models import (
    InboundMessage,
)
from openminion.modules.controlplane.runtime import (
    AuditLogger,
    EchoBrain,
    InMemoryControlPlaneStore,
    RuntimeCoordinator,
)
from openminion.modules.controlplane.runtime.channels import (
    ChannelRegistry as ControlPlaneChannelRegistry,
)
from openminion.modules.controlplane.runtime.router import Router
from openminion.modules.controlplane.runtime.worker.outbox import OutboxWorker
from openminion.modules.controlplane.runtime.worker.inbox import InboxWorker
from openminion.modules.controlplane.runtime.security import ScopeAuthorizer


def attach_outbox_worker(
    runner: Any,
    *,
    store: Any,
    audit_logger: Any | None = None,
) -> OutboxWorker:

    registry = ControlPlaneChannelRegistry()
    registry.register(runner)
    worker = OutboxWorker(
        store=store,
        registry=registry,
        audit_logger=audit_logger,
    )
    runner._outbox_worker = worker
    return worker


def drain_outbox(worker: OutboxWorker, *, max_iters: int = 32) -> int:

    drained = 0
    for _ in range(max_iters):
        result = worker.run_once()
        if result is None:
            break
        drained += 1
    return drained


def drain_inbox(worker: InboxWorker, *, max_iters: int = 32) -> int:

    drained = 0
    for _ in range(max_iters):
        result = worker.run_once()
        if result is None:
            break
        drained += 1
    return drained


def attach_inbox_worker(
    runner: Any,
    *,
    store: Any,
    audit_logger: Any | None = None,
) -> InboxWorker:
    worker = InboxWorker(
        store=store,
        dispatcher=runner._runtime,
        authorizer=ScopeAuthorizer(store=store),
        rate_limiter=getattr(runner, "_rate_limiter", None),
        audit_logger=audit_logger,
    )
    runner._inbox_worker = worker
    return worker


@dataclass
class CapturedOutbound:
    payload: dict[str, Any]
    timestamp: float = field(default_factory=lambda: __import__("time").time())


class MockCommandParser:
    def parse(self, text: str) -> None:
        return None


class MockCommandRegistry:
    def __init__(self):
        self.executed_commands: list[tuple[Any, Any]] = []

    def execute(self, command: Any, ctx: Any) -> Any:
        self.executed_commands.append((command, ctx))
        from openminion.modules.controlplane.contracts.models import CommandResult

        return CommandResult(ok=False, text="Command not implemented")


class InMemoryRouter(Router):
    def __init__(self, default_agent_id: str = "test-agent"):
        self._default_agent_id = default_agent_id
        self._session_counter = 0

    def resolve(self, inbound: InboundMessage) -> Any:
        from openminion.modules.controlplane.contracts.models import ResolvedContext
        import uuid

        self._session_counter += 1
        session_id = f"test-session-{self._session_counter}"
        return ResolvedContext(
            user_key=inbound.user_key,
            chat_key=inbound.chat_key,
            session_id=session_id,
            agent_id=self._default_agent_id,
            role="user",
            trace_id=str(uuid.uuid4()),
            span_id="test-span",
        )


class CapturingOutboundSender:
    def __init__(self):
        self.captured: list[CapturedOutbound] = []
        self._lock = threading.Lock()

    def __call__(self, payload: dict[str, Any]) -> None:
        with self._lock:
            self.captured.append(CapturedOutbound(payload=payload))

    def clear(self) -> None:
        with self._lock:
            self.captured.clear()

    def get_all(self) -> list[dict[str, Any]]:
        with self._lock:
            return [c.payload for c in self.captured]


class ControlplaneRuntimeFixture:
    def __init__(
        self,
        default_agent_id: str = "test-agent",
        enable_audit: bool = True,
    ):
        self._default_agent_id = default_agent_id
        self._enable_audit = enable_audit
        self._running = False
        self._lock = threading.Lock()

        # Core components
        self.store: InMemoryControlPlaneStore | None = None
        self.router: InMemoryRouter | None = None
        self.coordinator: RuntimeCoordinator | None = None
        self.outbound_sender: CapturingOutboundSender | None = None
        self.audit_logger: AuditLogger | None = None

    def start(self) -> None:
        with self._lock:
            if self._running:
                return

            self.store = InMemoryControlPlaneStore()
            self.router = InMemoryRouter(default_agent_id=self._default_agent_id)
            self.outbound_sender = CapturingOutboundSender()

            self.audit_logger = AuditLogger() if self._enable_audit else None

            self.coordinator = RuntimeCoordinator(
                store=self.store,
                router=self.router,
                parser=MockCommandParser(),
                command_registry=MockCommandRegistry(),
                brain_client=EchoBrain(),
                outbound=self.outbound_sender,
                audit_logger=self.audit_logger,
            )

            self._running = True

    def stop(self) -> None:
        with self._lock:
            if not self._running:
                return

            self.coordinator = None
            self.store = None
            self.router = None
            self.outbound_sender = None
            self.audit_logger = None
            self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def captured_outbounds(self) -> list[dict[str, Any]]:
        if self.outbound_sender is None:
            return []
        return self.outbound_sender.get_all()

    @property
    def audit_events(self) -> list[dict[str, Any]]:
        if self.audit_logger is None:
            return []
        return list(self.audit_logger.events)

    def clear_captures(self) -> None:
        if self.outbound_sender:
            self.outbound_sender.clear()
        if self.audit_logger:
            self.audit_logger.events.clear()

    def inject_update(self, telegram_update: dict[str, Any]) -> dict[str, Any]:
        if not self._running or self.coordinator is None:
            raise RuntimeError("Fixture not started. Call start() first.")

        # Convert Telegram update to InboundMessage
        from openminion.modules.controlplane.channels.telegram.normalization import (
            extract_envelope,
            to_inbound_message,
            to_control_event,
        )

        envelope = extract_envelope(telegram_update)
        if envelope is None:
            raise ValueError("Invalid Telegram update format")

        control_event = to_control_event(envelope)
        inbound = to_inbound_message(
            envelope,
            normalized_text=envelope.text,
            control_event=control_event,
        )

        # Dispatch to runtime coordinator
        result = self.coordinator.handle_inbound(inbound)
        return result

    def get_session_ids(self) -> list[str]:
        if self.store is None:
            return []
        # Access the internal sessions dict
        return (
            list(self.store._sessions.keys())
            if hasattr(self.store, "_sessions")
            else []
        )

    def __enter__(self) -> "ControlplaneRuntimeFixture":
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.stop()


@contextmanager
def runtime_fixture(
    default_agent_id: str = "test-agent",
    enable_audit: bool = True,
):
    fixture = ControlplaneRuntimeFixture(
        default_agent_id=default_agent_id,
        enable_audit=enable_audit,
    )
    try:
        fixture.start()
        yield fixture
    finally:
        fixture.stop()


class MockSkillBrain:
    def __init__(self):
        self._account_counter = 0
        self._post_counter = 0
        self._call_history: list[dict[str, Any]] = []

    def run(
        self,
        *,
        session_id: str,
        agent_id: str,
        user_text: str | None,
        attachment_refs: list[str],
        trace_id: str,
    ) -> dict:
        text = (user_text or "").lower()
        self._call_history.append(
            {
                "session_id": session_id,
                "agent_id": agent_id,
                "user_text": user_text,
                "attachment_refs": attachment_refs,
            }
        )

        if "create account" in text and (
            "publish" in text or "share" in text or "post" in text
        ):
            self._account_counter += 1
            account_id = f"acc_{self._account_counter:04d}"
            self._post_counter += 1
            post_id = f"post_{self._post_counter:04d}"
            return {
                "text": f"Account created: {account_id}\nPost created: {post_id}\nShare URL: https://example.com/share/{post_id}",
                "session_id": session_id,
                "trace_id": trace_id,
                "attachments": attachment_refs,
                "skill_result": {
                    "account_id": account_id,
                    "post_id": post_id,
                    "share_url": f"https://example.com/share/{post_id}",
                },
            }
        elif "create account" in text:
            self._account_counter += 1
            account_id = f"acc_{self._account_counter:04d}"
            return {
                "text": f"Account created: {account_id}\nAPI Key: sk_test***",
                "session_id": session_id,
                "trace_id": trace_id,
                "attachments": attachment_refs,
                "skill_result": {
                    "account_id": account_id,
                    "api_key": "sk_test***",
                },
            }
        elif "create post" in text:
            self._post_counter += 1
            post_id = f"post_{self._post_counter:04d}"
            return {
                "text": f"Post created: {post_id}",
                "session_id": session_id,
                "trace_id": trace_id,
                "attachments": attachment_refs,
                "skill_result": {
                    "post_id": post_id,
                },
            }
        elif "share" in text:
            post_id = (
                f"post_{self._post_counter:04d}"
                if self._post_counter > 0
                else "post_0001"
            )
            return {
                "text": f"Share URL: https://example.com/share/{post_id}",
                "session_id": session_id,
                "trace_id": trace_id,
                "attachments": attachment_refs,
                "skill_result": {
                    "share_url": f"https://example.com/share/{post_id}",
                },
            }
        else:
            return {
                "text": f"[{agent_id}] {user_text or ''}",
                "session_id": session_id,
                "trace_id": trace_id,
                "attachments": attachment_refs,
            }

    @property
    def call_history(self) -> list[dict[str, Any]]:
        return list(self._call_history)

    def reset(self) -> None:
        self._account_counter = 0
        self._post_counter = 0
        self._call_history.clear()


class SkillFlowRuntimeFixture(ControlplaneRuntimeFixture):
    def __init__(
        self,
        default_agent_id: str = "test-agent",
        enable_audit: bool = True,
    ):
        super().__init__(default_agent_id=default_agent_id, enable_audit=enable_audit)
        self.skill_brain: MockSkillBrain | None = None

    def start(self) -> None:
        with self._lock:
            if self._running:
                return

            self.store = InMemoryControlPlaneStore()
            self.router = InMemoryRouter(default_agent_id=self._default_agent_id)
            self.outbound_sender = CapturingOutboundSender()
            self.skill_brain = MockSkillBrain()

            self.audit_logger = AuditLogger() if self._enable_audit else None

            self.coordinator = RuntimeCoordinator(
                store=self.store,
                router=self.router,
                parser=MockCommandParser(),
                command_registry=MockCommandRegistry(),
                brain_client=self.skill_brain,
                outbound=self.outbound_sender,
                audit_logger=self.audit_logger,
            )

            self._running = True

    def stop(self) -> None:
        with self._lock:
            if not self._running:
                return

            self.coordinator = None
            self.store = None
            self.router = None
            self.outbound_sender = None
            self.skill_brain = None
            self.audit_logger = None
            self._running = False

    def get_skill_result(self) -> dict[str, Any] | None:
        outbounds = self.captured_outbounds
        if not outbounds:
            return None
        data = outbounds[-1].get("data", {})
        return data.get("skill_result")

    def get_skill_call_history(self) -> list[dict[str, Any]]:
        if self.skill_brain is None:
            return []
        return self.skill_brain.call_history


@contextmanager
def skill_flow_fixture(
    default_agent_id: str = "test-agent",
    enable_audit: bool = True,
):
    fixture = SkillFlowRuntimeFixture(
        default_agent_id=default_agent_id,
        enable_audit=enable_audit,
    )
    try:
        fixture.start()
        yield fixture
    finally:
        fixture.stop()
