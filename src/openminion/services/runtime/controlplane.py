"""Shared controlplane runtime composition for channel adapters."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Any, Callable

from openminion.base.config import OpenMinionConfig
from openminion.modules.controlplane.adapters.client import OpenMinionBrainClient
from openminion.modules.controlplane.commands.registry import CommandRegistry
from openminion.modules.controlplane.config import (
    ControlPlaneConfig,
    from_base_config as controlplane_from_base_config,
)
from openminion.modules.controlplane.constants import PRINCIPAL_BINDING_STATUS_ACTIVE
from openminion.modules.controlplane.runtime import EchoBrain, MetricsAuditSink
from openminion.modules.controlplane.runtime import MetricsRegistry, compose_audit_sinks
from openminion.modules.controlplane.runtime import build_controlplane_sidecar_specs
from openminion.modules.controlplane.runtime.audit import AuditLogger
from openminion.modules.controlplane.runtime.auth import AuthEvaluator
from openminion.modules.controlplane.runtime.channels import ChannelRegistry
from openminion.modules.controlplane.runtime.dispatcher import ControlPlaneDispatcher
from openminion.modules.controlplane.runtime.identity import StoreBackedIdentityAPI
from openminion.modules.controlplane.runtime.parser import SlashCommandParser
from openminion.modules.controlplane.runtime.rate_limit import (
    ControlPlaneRateLimiter,
    RateLimitPolicy,
)
from openminion.modules.controlplane.runtime.router import Router
from openminion.modules.controlplane.runtime.worker.outbox import OutboxWorker
from openminion.modules.controlplane import InboxWorker, ScopeAuthorizer
from openminion.modules.controlplane.storage import (
    SQLiteControlPlaneStore,
    build_controlplane_store,
)
from openminion.modules.controlplane.wizard.store import (
    SqliteWizardStore,
    register_store as register_wizard_store,
)
from openminion.modules.storage.engine import StorageEngineConfig
from openminion.services.runtime.composition import OpenMinionRuntime


@dataclass
class ControlPlaneRuntimeComponents:
    config: ControlPlaneConfig
    store: Any
    audit_logger: AuditLogger
    auth: AuthEvaluator
    identity_api: StoreBackedIdentityAPI
    router: Router
    parser: SlashCommandParser
    command_registry: CommandRegistry
    brain_client: Any
    dispatcher: ControlPlaneDispatcher
    rate_limiter: ControlPlaneRateLimiter
    delivery_registry: ChannelRegistry
    inbox_worker: InboxWorker
    outbox_worker: OutboxWorker
    metrics: MetricsRegistry
    sidecar_specs: list[Any]

    def close(self) -> None:
        closer = getattr(self.brain_client, "close", None)
        if callable(closer):
            closer()
        store_closer = getattr(self.store, "close", None)
        if callable(store_closer):
            store_closer()


def build_controlplane_runtime_components(
    *,
    config: OpenMinionConfig,
    home_root: Path,
    data_root: Path,
    logger: logging.Logger,
) -> ControlPlaneRuntimeComponents:
    cp_cfg = controlplane_from_base_config(
        base_config=config,
        home_root=home_root,
        data_root=data_root,
    )
    store = _build_store(config=config, cp_cfg=cp_cfg)
    _initialize_store(store=store, cp_cfg=cp_cfg)

    metrics = MetricsRegistry()
    metrics_sink = MetricsAuditSink(metrics)
    audit_logger = AuditLogger(
        sink=compose_audit_sinks(store.put_audit, metrics_sink.observe),
        schema_validation_enabled=cp_cfg.audit_schema_validation_enabled,
    )
    auth = AuthEvaluator(admin_user_keys=cp_cfg.admin_user_keys)
    router = Router(store, auth=auth, audit_logger=audit_logger)
    parser = SlashCommandParser()
    command_registry = CommandRegistry(
        store=store,
        auth=auth,
        audit_logger=audit_logger,
    )
    brain = _build_brain(
        cp_cfg=cp_cfg,
        home_root=home_root,
        data_root=data_root,
    )
    identity_api = StoreBackedIdentityAPI(store)
    dispatcher = ControlPlaneDispatcher(
        store=store,
        router=router,
        parser=parser,
        command_registry=command_registry,
        brain_client=brain,
        outbound_sender=lambda _payload: None,
        audit_logger=audit_logger,
        identity_api=identity_api,
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
    delivery_registry = ChannelRegistry()
    inbox_worker = InboxWorker(
        store=store,
        dispatcher=dispatcher,
        authorizer=ScopeAuthorizer(store=store),
        rate_limiter=rate_limiter,
        audit_logger=audit_logger,
    )
    outbox_worker = OutboxWorker(
        store=store,
        registry=delivery_registry,
        audit_logger=audit_logger,
        max_attempts=cp_cfg.outbox_max_attempts,
        max_backoff_s=cp_cfg.outbox_max_backoff_s,
    )
    return ControlPlaneRuntimeComponents(
        config=cp_cfg,
        store=store,
        audit_logger=audit_logger,
        auth=auth,
        identity_api=identity_api,
        router=router,
        parser=parser,
        command_registry=command_registry,
        brain_client=brain,
        dispatcher=dispatcher,
        rate_limiter=rate_limiter,
        delivery_registry=delivery_registry,
        inbox_worker=inbox_worker,
        outbox_worker=outbox_worker,
        metrics=metrics,
        sidecar_specs=build_controlplane_sidecar_specs(
            config=cp_cfg,
            store=store,
            audit_logger=audit_logger,
            metrics=metrics,
        ),
    )


def _build_store(*, config: OpenMinionConfig, cp_cfg: ControlPlaneConfig) -> Any:
    cp_db_path = Path(cp_cfg.sqlite_path).expanduser().resolve(strict=False)
    return build_controlplane_store(
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


def _initialize_store(*, store: Any, cp_cfg: ControlPlaneConfig) -> None:
    if hasattr(store, "backfill_pairings_to_principals"):
        store.backfill_pairings_to_principals(status=PRINCIPAL_BINDING_STATUS_ACTIVE)
    if isinstance(store, SQLiteControlPlaneStore):
        cp_db_path = Path(cp_cfg.sqlite_path).expanduser().resolve(strict=False)
        wizard_db_path = cp_db_path.parent / "wizard.db"
        wizard_db_path.parent.mkdir(parents=True, exist_ok=True)
        register_wizard_store("sqlite", SqliteWizardStore(wizard_db_path))


def _runtime_factory(
    *, home_root: Path, data_root: Path
) -> Callable[[str | None], OpenMinionRuntime]:
    def build(config_path: str | None) -> OpenMinionRuntime:
        return OpenMinionRuntime.from_config_path(
            config_path,
            home_root=str(home_root),
            data_root=str(data_root),
        )

    return build


def _build_brain(
    *,
    cp_cfg: ControlPlaneConfig,
    home_root: Path,
    data_root: Path,
) -> Any:
    if not cp_cfg.openminion_enabled:
        return EchoBrain()
    return OpenMinionBrainClient(
        config_path=cp_cfg.openminion_config_path,
        home_root=str(home_root),
        data_root=str(data_root),
        runtime_factory=_runtime_factory(home_root=home_root, data_root=data_root),
        channel=cp_cfg.openminion_channel,
        target=cp_cfg.openminion_target,
        deliver=cp_cfg.openminion_deliver,
    )
