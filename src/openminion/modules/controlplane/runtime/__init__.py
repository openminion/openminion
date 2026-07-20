import logging
from dataclasses import dataclass, field
from typing import Callable

from openminion.base.config.env import EnvironmentConfig, resolve_environment_config
from openminion.modules.controlplane.runtime.dispatcher import ControlPlaneDispatcher
from openminion.modules.controlplane.runtime.health_probe import (
    ControlPlaneHealthProbeConfig,
    ControlPlaneHealthProbeSidecar,
)
from openminion.modules.controlplane.runtime.janitor import (
    ControlPlaneJanitor,
    ControlPlaneJanitorSidecar,
    ControlPlaneRetentionPolicy,
)
from openminion.modules.controlplane.runtime.metrics import (
    MetricsAuditSink,
    MetricsRegistry,
    compose_audit_sinks,
)
from openminion.modules.controlplane.runtime.sidecar_specs import (
    ControlPlaneSidecarSpec,
    build_controlplane_sidecar_specs,
)
from openminion.modules.controlplane.interfaces import (
    CONTROLPLANE_INTERFACE_VERSION,
    ensure_controlplane_component_compatibility,
)
from openminion.modules.controlplane.contracts.models import (
    BrainClient,
    CommandParser,
    InboundMessage,
)
from openminion.modules.controlplane.runtime.router import Router
from openminion.modules.controlplane.runtime.store import InMemoryControlPlaneStore

_LOG = logging.getLogger(__name__)


class AuditLogger:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def log(self, event: str, **payload: object) -> None:
        self.events.append({"event": event, **payload})


@dataclass
class RuntimeCoordinator:
    contract_version: str = field(default=CONTROLPLANE_INTERFACE_VERSION, init=False)
    store: InMemoryControlPlaneStore
    router: Router
    parser: CommandParser
    command_registry: any
    brain_client: BrainClient
    outbound: Callable[[dict], None]
    audit_logger: AuditLogger | None = None
    env: EnvironmentConfig = field(default_factory=resolve_environment_config)
    dispatcher: ControlPlaneDispatcher = field(init=False)

    def __post_init__(self) -> None:
        self._validate_component_contracts()
        self.dispatcher = ControlPlaneDispatcher(
            store=self.store,
            router=self.router,
            parser=self.parser,
            command_registry=self.command_registry,
            brain_client=self.brain_client,
            audit_logger=self.audit_logger,
            outbound_sender=self.outbound,
            env=self.env,
        )

    def _validate_component_contracts(self) -> None:
        strict_raw = (
            self.env.get("OPENMINION_STRICT_CONTROLPLANE_CONTRACTS", "0")
            .strip()
            .lower()
        )
        strict = strict_raw not in {"", "0", "false", "no", "off"}
        components = (
            ("session_store", self.store),
            ("router", self.router),
            ("command_parser", self.parser),
            ("brain_client", self.brain_client),
            ("outbound_sender", self.outbound),
        )
        for component_type, component in components:
            if (
                component_type == "outbound_sender"
                and not strict
                and not hasattr(component, "contract_version")
                and callable(component)
            ):
                # Transitional compatibility: existing runtimes often use plain
                # function callables (e.g., list.append) as outbound sinks.
                continue
            try:
                ensure_controlplane_component_compatibility(
                    component, component_type=component_type
                )
            except (
                Exception
            ) as exc:  # pragma: no cover - exercised by strict mode integration tests
                if strict:
                    raise
                _LOG.warning(
                    "controlplane contract warning (%s): %s",
                    component_type,
                    exc,
                )

    def handle_inbound(self, inbound: InboundMessage) -> dict:
        return self.dispatcher.handle_inbound(inbound)


class EchoBrain(BrainClient):  # pragma: no cover - trivial behavior
    contract_version = CONTROLPLANE_INTERFACE_VERSION

    def run(
        self,
        *,
        session_id: str,
        agent_id: str,
        user_text: str | None,
        attachment_refs: list[str],
        trace_id: str,
    ) -> dict:
        text = user_text or ""
        return {
            "text": f"[{agent_id}] {text}",
            "session_id": session_id,
            "trace_id": trace_id,
            "attachments": attachment_refs,
        }


__all__ = [
    "ControlPlaneDispatcher",
    "ControlPlaneHealthProbeConfig",
    "ControlPlaneHealthProbeSidecar",
    "ControlPlaneJanitor",
    "ControlPlaneJanitorSidecar",
    "ControlPlaneRetentionPolicy",
    "ControlPlaneSidecarSpec",
    "EchoBrain",
    "MetricsAuditSink",
    "MetricsRegistry",
    "RuntimeCoordinator",
    "build_controlplane_sidecar_specs",
    "compose_audit_sinks",
]
