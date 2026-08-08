from __future__ import annotations

from pathlib import Path

from openminion.base.config import OpenMinionConfig, save_config
from openminion.modules.controlplane.adapters.client import OpenMinionBrainClient
from openminion.modules.controlplane.commands.registry import CommandRegistry
from openminion.modules.controlplane.contracts.models import InboundMessage
from openminion.modules.controlplane.runtime.audit import AuditLogger
from openminion.modules.controlplane.runtime.auth import AuthEvaluator
from openminion.modules.controlplane.runtime.dispatcher import ControlPlaneDispatcher
from openminion.modules.controlplane.runtime.parser import SlashCommandParser
from openminion.modules.controlplane.runtime.router import Router
from openminion.modules.controlplane.storage.sqlite import SQLiteControlPlaneStore
from openminion.services.runtime.composition import OpenMinionRuntime
from tests._csc_fixtures import _csc_install_default_agent


def _write_echo_profile_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "openminion.json"
    config = OpenMinionConfig()
    config.runtime.log_level = "ERROR"
    _csc_install_default_agent(config, provider="echo")
    config.storage.path = str(tmp_path / "state" / "runtime.db")
    save_config(config, str(config_path))
    return config_path


def test_controlplane_dispatches_through_real_openminion_runtime(
    tmp_path: Path,
) -> None:
    config_path = _write_echo_profile_config(tmp_path)
    data_root = tmp_path / "data"
    store = SQLiteControlPlaneStore(tmp_path / "cp.db")
    audit = AuditLogger(sink=store.put_audit)
    outbound: list[dict[str, object]] = []
    brain = OpenMinionBrainClient(
        config_path=str(config_path),
        home_root=str(tmp_path),
        data_root=str(data_root),
        runtime_factory=lambda path: OpenMinionRuntime.from_config_path(
            path, home_root=str(tmp_path), data_root=str(data_root)
        ),
    )
    dispatcher = ControlPlaneDispatcher(
        store=store,
        router=Router(store),
        parser=SlashCommandParser(),
        command_registry=CommandRegistry(
            store=store,
            auth=AuthEvaluator(admin_user_keys=[]),
        ),
        brain_client=brain,
        audit_logger=audit,
        outbound_sender=outbound.append,
    )
    session_id = store.resolve_session(
        "telegram:user:real-runtime",
        "telegram:chat:real-runtime",
    )
    store.ensure_agent("openminion")
    store.set_agent(session_id, "openminion")

    try:
        payload = dispatcher.handle_inbound(
            InboundMessage(
                channel="telegram",
                user_key="telegram:user:real-runtime",
                chat_key="telegram:chat:real-runtime",
                user_id="real-runtime-user",
                chat_id="real-runtime-chat",
                text="hello from the real runtime path",
                metadata={"trace_id": "trace-real-runtime"},
            )
        )

        assert payload["type"] == "chat"
        assert payload["session_id"] == session_id
        assert outbound[-1] == payload
        assert "hello from the real runtime path" in str(payload["text"])

        trace_events = [
            event for event in audit.events if event.trace_id == "trace-real-runtime"
        ]
        assert [event.event_type for event in trace_events] == [
            "inbound.received",
            "inbound.resolved",
            "cp.chat.dispatched",
            "outbound.sent",
        ]
    finally:
        brain.close()
        store.close()
