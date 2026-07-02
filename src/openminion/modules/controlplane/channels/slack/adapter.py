"""Slack adapter construction helpers."""

from __future__ import annotations

import logging
import queue
from typing import Any

from openminion.modules.controlplane.channels.slack.config import SlackChannelConfig
from openminion.modules.controlplane.channels.slack.delivery import SlackDeliveryService
from openminion.modules.controlplane.channels.slack.socket_mode import (
    MissingSlackSocketDependency,
    SlackSocketClientAPI,
    SlackSocketModeRunner,
)
from openminion.modules.controlplane.channels.slack.webhook import SlackHttpEventsRunner


def build_slack_runner(
    *,
    config: SlackChannelConfig,
    runtime: Any,
    delivery: SlackDeliveryService,
    state_store: Any | None = None,
    audit_logger: Any | None = None,
    logger: logging.Logger | None = None,
    store: Any | None = None,
    outbox_worker: Any | None = None,
    bot_user_id: str | None = None,
    socket_client: SlackSocketClientAPI | None = None,
) -> SlackSocketModeRunner | SlackHttpEventsRunner:
    if config.mode == "http":
        return SlackHttpEventsRunner(
            config=config,
            runtime=runtime,
            delivery=delivery,
            state_store=state_store,
            audit_logger=audit_logger,
            logger=logger,
            store=store,
            outbox_worker=outbox_worker,
            bot_user_id=bot_user_id,
        )
    client = socket_client or _maybe_sdk_socket_client(config)
    return SlackSocketModeRunner(
        config=config,
        runtime=runtime,
        delivery=delivery,
        socket_client=client,
        state_store=state_store,
        audit_logger=audit_logger,
        logger=logger,
        store=store,
        outbox_worker=outbox_worker,
        bot_user_id=bot_user_id,
    )


class SlackSdkSocketClient:
    """Tiny adapter around slack_sdk SocketModeClient.

    The import remains inside this Slack-local file so the core package never
    depends on Slack SDK classes unless the optional extra is installed.
    """

    def __init__(self, *, app_token: str, bot_token: str) -> None:
        try:
            from slack_sdk.socket_mode import SocketModeClient
            from slack_sdk.socket_mode.response import SocketModeResponse
            from slack_sdk.web import WebClient
        except Exception as exc:  # pragma: no cover - optional dependency
            raise MissingSlackSocketDependency(
                "Slack Socket Mode requires openminion[slack]."
            ) from exc
        self._SocketModeResponse = SocketModeResponse
        self._queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self._client = SocketModeClient(
            app_token=app_token,
            web_client=WebClient(token=bot_token),
        )
        self._client.socket_mode_request_listeners.append(self._on_request)

    def connect(self) -> None:
        self._client.connect()

    def recv(self, timeout: float = 1.0) -> dict[str, Any] | None:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def ack(self, envelope_id: str) -> None:
        response = self._SocketModeResponse(envelope_id=envelope_id)
        self._client.send_socket_mode_response(response)

    def close(self) -> None:
        self._client.close()

    def _on_request(self, _client: Any, request: Any) -> None:
        self._queue.put(
            {
                "envelope_id": getattr(request, "envelope_id", ""),
                "type": getattr(request, "type", ""),
                "payload": getattr(request, "payload", {}),
            }
        )


def _maybe_sdk_socket_client(config: SlackChannelConfig) -> SlackSocketClientAPI | None:
    if not config.app_token:
        return None
    return SlackSdkSocketClient(app_token=config.app_token, bot_token=config.bot_token)


def build_slack_runner_from_base_config(
    *,
    config: Any,
    home_root: Any,
    data_root: Any,
    logger: logging.Logger,
) -> SlackSocketModeRunner | SlackHttpEventsRunner:
    """Build the Slack runner without crossing into service-layer lifecycle code."""

    from pathlib import Path

    from openminion.modules.controlplane.adapters.client import OpenMinionBrainClient
    from openminion.modules.controlplane.channels.slack.bot_api import SlackWebAPI
    from openminion.modules.controlplane.channels.slack.config import (
        from_base_config as slack_from_base_config,
    )
    from openminion.modules.controlplane.channels.slack.state import SlackStateStore
    from openminion.modules.controlplane.commands.registry import CommandRegistry
    from openminion.modules.controlplane.config import (
        from_base_config as controlplane_from_base_config,
    )
    from openminion.modules.controlplane.constants import (
        PRINCIPAL_BINDING_STATUS_ACTIVE,
    )
    from openminion.modules.controlplane.runtime import EchoBrain
    from openminion.modules.controlplane.runtime.audit import AuditLogger
    from openminion.modules.controlplane.runtime.auth import AuthEvaluator
    from openminion.modules.controlplane.runtime.channels import (
        ChannelRegistry as ControlPlaneChannelRegistry,
    )
    from openminion.modules.controlplane.runtime.dispatcher import (
        ControlPlaneDispatcher,
    )
    from openminion.modules.controlplane.runtime.parser import SlashCommandParser
    from openminion.modules.controlplane.runtime.rate_limit import (
        ControlPlaneRateLimiter,
        RateLimitPolicy,
    )
    from openminion.modules.controlplane.runtime.router import Router
    from openminion.modules.controlplane.runtime.worker.outbox import OutboxWorker
    from openminion.modules.controlplane.storage import (
        SQLiteControlPlaneStore,
        build_controlplane_store,
    )
    from openminion.modules.controlplane.wizard.store import (
        SqliteWizardStore,
        register_store as register_wizard_store,
    )
    from openminion.modules.storage.engine import StorageEngineConfig

    resolved_home = Path(home_root).expanduser().resolve(strict=False)
    resolved_data = Path(data_root).expanduser().resolve(strict=False)
    cp_cfg = controlplane_from_base_config(
        base_config=config,
        home_root=resolved_home,
        data_root=resolved_data,
    )
    slack_cfg = slack_from_base_config(
        base_config=config,
        home_root=resolved_home,
        data_root=resolved_data,
    ).slack

    cp_db_path = Path(cp_cfg.sqlite_path).expanduser().resolve(strict=False)
    store = build_controlplane_store(
        config=StorageEngineConfig(
            root_dir=cp_db_path.parent,
            sqlite_path=cp_db_path,
            fallback_root=cp_db_path.parent,
            wal=cp_cfg.wal,
            record_backend=config.storage.record_backend(),
            record_backend_options=config.storage.record_backend_options(),
        ),
        database_path=cp_db_path,
    )
    if hasattr(store, "backfill_pairings_to_principals"):
        store.backfill_pairings_to_principals(status=PRINCIPAL_BINDING_STATUS_ACTIVE)
    if isinstance(store, SQLiteControlPlaneStore):
        wizard_db_path = cp_db_path.parent / "wizard.db"
        wizard_db_path.parent.mkdir(parents=True, exist_ok=True)
        register_wizard_store("sqlite", SqliteWizardStore(wizard_db_path))

    audit_logger = AuditLogger(sink=store.put_audit)
    auth = AuthEvaluator(admin_user_keys=cp_cfg.admin_user_keys)
    router = Router(store, auth=auth, audit_logger=audit_logger)
    command_registry = CommandRegistry(
        store=store,
        auth=auth,
        audit_logger=audit_logger,
    )
    brain = (
        EchoBrain()
        if not cp_cfg.openminion_enabled
        else OpenMinionBrainClient(
            config_path=cp_cfg.openminion_config_path,
            home_root=str(resolved_home),
            data_root=str(resolved_data),
            channel=cp_cfg.openminion_channel,
            target=cp_cfg.openminion_target,
            deliver=cp_cfg.openminion_deliver,
        )
    )
    runtime = ControlPlaneDispatcher(
        store=store,
        router=router,
        parser=SlashCommandParser(),
        command_registry=command_registry,
        brain_client=brain,
        outbound_sender=lambda _payload: None,
        audit_logger=audit_logger,
    )
    api = SlackWebAPI(slack_cfg.bot_token)
    delivery = SlackDeliveryService(
        api=api,
        delivery_config=slack_cfg.delivery,
        audit_logger=audit_logger,
    )
    state_store = SlackStateStore(slack_cfg.state_sqlite_path)
    cp_channel_registry = ControlPlaneChannelRegistry()
    outbox_worker = OutboxWorker(
        store=store,
        registry=cp_channel_registry,
        audit_logger=audit_logger,
        max_attempts=cp_cfg.outbox_max_attempts,
        max_backoff_s=cp_cfg.outbox_max_backoff_s,
    )
    rate_limiter = ControlPlaneRateLimiter(
        store=store,
        policy=RateLimitPolicy(
            chat_window_s=cp_cfg.rate_limit_chat_window_s,
            chat_limit=cp_cfg.rate_limit_chat_limit,
            user_window_s=cp_cfg.rate_limit_user_window_s,
            user_limit=cp_cfg.rate_limit_user_limit,
            session_window_s=cp_cfg.rate_limit_session_window_s,
            session_limit=cp_cfg.rate_limit_session_limit,
        ),
    )
    runner = build_slack_runner(
        config=slack_cfg,
        runtime=runtime,
        delivery=delivery,
        state_store=state_store,
        audit_logger=audit_logger,
        logger=logger,
        store=store,
        outbox_worker=outbox_worker,
    )
    setattr(runner, "_rate_limiter", rate_limiter)
    setattr(runner, "_brain_client", brain)
    cp_channel_registry.register(runner)
    return runner
