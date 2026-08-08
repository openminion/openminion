from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Protocol

from openminion.base.config.env import EnvironmentConfig, resolve_environment_config

from ...contracts.inbound import canonicalize_inbound_message, inbound_metadata
from ...contracts.models import (
    BrainClient,
    CommandParser,
    InboundMessage,
    ResolvedContext,
)
from ...contracts.outbound import (
    OutboundPayload,
    from_legacy_payload,
    payload_type,
    to_legacy_payload,
)
from ...interfaces import (
    CONTROLPLANE_INTERFACE_VERSION,
    ensure_controlplane_component_compatibility,
)
from ..audit import emit_audit_event
from ..router import Router
from .chat import ChatDispatcher
from .clarify import ClarifyStateManager, extract_clarify_answer
from .command import CommandDispatcher, CommandRegistry
from .wizard import WizardDispatcher

_LOG = logging.getLogger(__name__)
JsonDict = dict[str, Any]


class InboundStore(Protocol):
    def persist_inbound(self, inbound: InboundMessage, session_id: str) -> None: ...


def _verify_controlplane_contracts(
    *,
    env: EnvironmentConfig,
    store: InboundStore,
    router: Router,
    parser: CommandParser,
    brain_client: BrainClient,
) -> None:
    strict_raw = env.get("OPENMINION_STRICT_CONTROLPLANE_CONTRACTS", "").strip().lower()
    strict = strict_raw not in {"", "0", "false", "no", "off"}
    components = (
        ("session_store", store),
        ("router", router),
        ("command_parser", parser),
        ("brain_client", brain_client),
    )
    for component_type, component in components:
        try:
            ensure_controlplane_component_compatibility(
                component, component_type=component_type
            )
        except Exception as exc:
            if strict:
                raise
            _LOG.warning("controlplane contract warning (%s): %s", component_type, exc)


def _ctx_with_inbound_trace(
    inbound: InboundMessage, ctx: ResolvedContext
) -> ResolvedContext:
    inbound_trace = str(inbound_metadata(inbound).get("trace_id", "")).strip()
    if inbound_trace and inbound_trace != ctx.trace_id:
        return replace(ctx, trace_id=inbound_trace)
    return ctx


def _inbound_with_metadata_trace(
    inbound: InboundMessage, *, fallback_trace: str
) -> InboundMessage:
    metadata = inbound_metadata(inbound)
    if str(metadata.get("trace_id", "")).strip():
        return inbound
    metadata["trace_id"] = fallback_trace
    return replace(inbound, metadata=metadata, meta=dict(metadata))


def _clarify_answer_rejection_or_none(
    *,
    chat: ChatDispatcher,
    clarify: ClarifyStateManager,
    audit: Callable[..., None],
    ctx: ResolvedContext,
    answer: dict[str, str],
) -> JsonDict | None:
    unknown_payload = chat.maybe_unknown_clarify_payload(
        ctx=ctx,
        clarify_answer=answer,
        pending=clarify.get(ctx.session_id),
    )
    if unknown_payload is not None:
        return unknown_payload
    audit(
        "cp.clarify.answered",
        session_id=ctx.session_id,
        trace_id=ctx.trace_id,
        clarify_id=answer.get("clarify_id", ""),
        question_id=answer.get("question_id", ""),
    )
    audit(
        "cp.resume.dispatched",
        session_id=ctx.session_id,
        trace_id=ctx.trace_id,
        clarify_id=answer.get("clarify_id", ""),
    )
    return None


def _outbound_kind(outbound_payload: OutboundPayload) -> str:
    outbound_type = payload_type(outbound_payload)
    if outbound_type == "command_result":
        return "command"
    if outbound_type == "clarify_error":
        return "clarify_error"
    return "chat"


@dataclass
class ControlPlaneDispatcher:
    contract_version: str = field(default=CONTROLPLANE_INTERFACE_VERSION, init=False)
    store: InboundStore
    router: Router
    parser: CommandParser
    command_registry: CommandRegistry
    brain_client: BrainClient
    audit_logger: object | None = None
    outbound_sender: Callable[[JsonDict], None] | None = None
    identity_api: object | None = None
    env: EnvironmentConfig = field(default_factory=resolve_environment_config)

    def __post_init__(self) -> None:
        _verify_controlplane_contracts(
            env=self.env,
            store=self.store,
            router=self.router,
            parser=self.parser,
            brain_client=self.brain_client,
        )
        self._clarify = ClarifyStateManager(
            store=self.store, audit_logger=self.audit_logger
        )
        self._clarify.hydrate_from_store()
        self._wizard = WizardDispatcher(
            router=self.router, audit_logger=self.audit_logger
        )
        self._command = CommandDispatcher(
            registry=self.command_registry, audit_logger=self.audit_logger
        )
        self._chat = ChatDispatcher(
            store=self.store,
            brain_client=self.brain_client,
            audit_logger=self.audit_logger,
            clarify=self._clarify,
        )

    @property
    def _pending_clarify_by_session(self) -> dict[str, JsonDict]:
        return self._clarify.pending_by_session

    def handle_inbound(self, inbound: InboundMessage) -> JsonDict:
        inbound = canonicalize_inbound_message(inbound)
        ctx = self.router.resolve(inbound)
        pending = self._clarify.get(ctx.session_id)
        inbound = self._chat.apply_pending_trace(inbound, pending)
        inbound = _inbound_with_metadata_trace(inbound, fallback_trace=ctx.trace_id)
        ctx = _ctx_with_inbound_trace(inbound, ctx)
        self._audit(
            "inbound.received",
            channel=inbound.channel,
            trace_id=str(inbound_metadata(inbound).get("trace_id", "")),
        )
        self._audit(
            "inbound.resolved",
            session_id=ctx.session_id,
            agent_id=ctx.agent_id,
            trace_id=ctx.trace_id,
        )
        self.store.persist_inbound(inbound, ctx.session_id)
        outbound_payload, _ = self.dispatch(inbound)
        payload = to_legacy_payload(outbound_payload)
        if self.outbound_sender is not None:
            self.outbound_sender(payload)
        self._audit(
            "outbound.sent",
            kind=_outbound_kind(outbound_payload),
            session_id=ctx.session_id,
            agent_id=ctx.agent_id,
            trace_id=ctx.trace_id,
        )
        return payload

    def dispatch(
        self, inbound: InboundMessage
    ) -> tuple[OutboundPayload, ResolvedContext]:
        inbound = canonicalize_inbound_message(inbound)
        wizard_result = self._wizard.try_dispatch(inbound)
        if wizard_result is not None:
            payload, ctx = wizard_result
            return self._to_outbound(payload, ctx), ctx

        ctx = self.router.resolve(inbound)
        pending = self._clarify.get(ctx.session_id)
        inbound = self._chat.apply_pending_trace(inbound, pending)
        ctx = _ctx_with_inbound_trace(inbound, ctx)

        clarify_answer = extract_clarify_answer(inbound)
        if clarify_answer is not None:
            unknown_payload = _clarify_answer_rejection_or_none(
                chat=self._chat,
                clarify=self._clarify,
                audit=self._audit,
                ctx=ctx,
                answer=clarify_answer,
            )
            if unknown_payload is not None:
                return self._to_outbound(unknown_payload, ctx), ctx

        command = self.parser.parse(inbound.text)
        if command is not None:
            payload = self._command.dispatch(command, ctx)
        else:
            payload = self._chat.dispatch(inbound, ctx)

        return self._to_outbound(payload, ctx), ctx

    def _audit(self, event_type: str, **details: object) -> None:
        emit_audit_event(self.audit_logger, event_type, **details)

    def _to_outbound(self, payload: JsonDict, ctx: ResolvedContext) -> OutboundPayload:
        return from_legacy_payload(payload, ctx=ctx)
