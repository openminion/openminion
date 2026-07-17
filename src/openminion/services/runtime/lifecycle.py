from dataclasses import dataclass
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any, Callable
import logging

from openminion.base.channel import ChannelRegistry, ConsoleChannel
from openminion.base.config import OpenMinionConfig
from openminion.base.config.core import resolve_default_agent_id
from openminion.base.config.paths import resolve_data_root, resolve_home_root
from openminion.services.runtime.catalog import ExtensionCatalog
from openminion.services.runtime.composition import OpenMinionRuntime
from openminion.services.runtime.plugins import (
    PluginContext,
    PluginRegistry,
    build_default_plugin_registry_with_activation_guard,
)
from openminion.modules.llm.providers import (
    ProviderRegistry,
    load_plugin_providers,
    register_builtin_providers,
)
from openminion.modules.tool import ToolRegistry, build_default_tool_registry
from openminion.modules.tool.runtime.plugins import load_plugins as load_tool_plugins
from openminion.modules.policy import SecurityPolicyEngine
from openminion.services.runtime.sidecars import (
    SidecarManager,
    default_sidecar_manager,
    ensure_sidecar_autostart,
)
from openminion.modules.policy import (
    SecurityPolicyContext,
    default_internal_actor,
)
from openminion.modules.tool.exposure import get_visible_tool_specs_and_dispatch_map

ExtensionEventSink = Callable[[str, dict[str, Any]], None]


@dataclass(frozen=True)
class ExtensionRuntime:
    catalog: ExtensionCatalog
    channels: ChannelRegistry
    plugins: PluginRegistry
    plugin_context: PluginContext
    tools: ToolRegistry
    providers: ProviderRegistry
    provider_statuses: list[dict[str, Any]]
    tool_plugin_statuses: list[dict[str, Any]]
    plugin_statuses: list[dict[str, Any]]
    sidecar_manager: SidecarManager | None


class LifecycleService:
    EXPECTED_ENTRY_POINT_GROUPS = (
        "llmctl.providers",
        "openminion.modules.tool.runtime.plugins",
        "openminion.modules.controlplane.commands",
    )

    def __init__(
        self,
        *,
        config: OpenMinionConfig,
        config_path: str | None = None,
        home_root: Path | None = None,
        data_root: Path | None = None,
        logger: logging.Logger | None = None,
        catalog: ExtensionCatalog | None = None,
        event_sink: ExtensionEventSink | None = None,
    ) -> None:
        self._config = config
        self._config_path = config_path
        self._home_root = home_root or resolve_home_root(config_path=config_path)
        self._data_root = data_root or resolve_data_root(self._home_root)
        self._logger = logger or logging.getLogger("openminion.services.runtime")
        self._catalog = catalog or ExtensionCatalog.from_config(config)
        self._event_sink = event_sink
        self._last_runtime: ExtensionRuntime | None = None
        self._validate_contract()

    @classmethod
    def from_config(
        cls,
        config: OpenMinionConfig,
        *,
        config_path: str | None = None,
        home_root: Path | None = None,
        data_root: Path | None = None,
        logger: logging.Logger | None = None,
        event_sink: ExtensionEventSink | None = None,
    ) -> "LifecycleService":
        return cls(
            config=config,
            config_path=config_path,
            home_root=home_root,
            data_root=data_root,
            logger=logger,
            event_sink=event_sink,
        )

    @property
    def catalog(self) -> ExtensionCatalog:
        return self._catalog

    def build(
        self,
        *,
        security_policy: SecurityPolicyEngine,
        on_before_activate: Callable[[Any], None] | None = None,
        load_tool_plugins: bool = False,
    ) -> ExtensionRuntime:
        channels = build_channel_registry(
            config=self._config,
            home_root=self._home_root,
            data_root=self._data_root,
            logger=self._logger.getChild("channels"),
        )
        plugin_logger = self._logger.getChild("plugins")
        plugins = build_default_plugin_registry_with_activation_guard(
            config=self._config,
            logger=plugin_logger,
            on_before_activate=on_before_activate,
        )
        plugin_context = PluginContext(config=self._config, logger=plugin_logger)

        tools = build_default_tool_registry(config=self._config.runtime, strict=False)
        tools.bind_sidecar_autostart(ensure_sidecar_autostart)
        plugins.register_tool_extensions(tools, plugin_context)

        providers = ProviderRegistry()
        provider_statuses = register_builtin_providers(providers)
        provider_statuses.extend(load_plugin_providers(providers))

        tool_plugin_statuses: list[dict[str, Any]] = []
        if load_tool_plugins:
            tool_plugin_statuses = _load_tool_plugin_statuses()

        plugin_statuses = _plugin_statuses(self._catalog, plugins)
        sidecar_manager = _build_sidecar_manager(
            catalog=self._catalog,
            config_path=self._config_path,
            runtime_env=getattr(self._config.runtime, "env", None),
            policy=security_policy,
            agent_id=resolve_default_agent_id(self._config),
            logger=self._logger,
        )

        self._emit_event(
            "ext.discovery",
            {
                "plugins": len(self._catalog.plugins),
                "tool_plugins": len(self._catalog.tool_plugins),
                "providers": len(self._catalog.providers),
                "channels": len(self._catalog.channels),
            },
        )
        if self._catalog.errors:
            self._emit_event(
                "ext.discovery.error", {"errors": list(self._catalog.errors)}
            )
        self._emit_event(
            "ext.activation",
            {
                "plugins_loaded": len(plugins.names()),
                "tools_loaded": len(get_visible_tool_specs_and_dispatch_map(tools)[0]),
                "providers_loaded": len(providers.list()),
            },
        )

        runtime = ExtensionRuntime(
            catalog=self._catalog,
            channels=channels,
            plugins=plugins,
            plugin_context=plugin_context,
            tools=tools,
            providers=providers,
            provider_statuses=provider_statuses,
            tool_plugin_statuses=tool_plugin_statuses,
            plugin_statuses=plugin_statuses,
            sidecar_manager=sidecar_manager,
        )
        self._last_runtime = runtime
        return runtime

    def status_payload(
        self,
        runtime: ExtensionRuntime,
    ) -> dict[str, Any]:
        sidecar_statuses: list[dict[str, Any]] = []
        if runtime.sidecar_manager is not None:
            for name in runtime.sidecar_manager.list():
                sidecar_statuses.append(runtime.sidecar_manager.status(name))
        return {
            "ok": True,
            "catalog": runtime.catalog.to_dict(),
            "plugins": runtime.plugin_statuses,
            "tool_plugins": runtime.tool_plugin_statuses,
            "providers": runtime.provider_statuses,
            "channels": [record.to_dict() for record in runtime.catalog.channels],
            "audit_health": self.audit_health(runtime),
            "sidecars": sidecar_statuses,
            "errors": list(runtime.catalog.errors),
        }

    def audit_health(
        self,
        runtime: ExtensionRuntime | None = None,
    ) -> dict[str, Any]:
        current = runtime or self._last_runtime
        if current is None:
            return {
                "audit": {"healthy": True, "failures": 0, "last_error": None},
                "binding_warnings": 0,
            }

        seen_loggers: set[int] = set()
        failures = 0
        last_error: str | None = None
        binding_warnings = 0
        wizard_step_failures = 0

        for channel_name in current.channels.names():
            adapter = current.channels.get(channel_name)
            binding_warnings += int(getattr(adapter, "_binding_warning_count", 0) or 0)
            audit_logger = getattr(adapter, "_audit_logger", None)
            if audit_logger is None:
                audit_logger = getattr(
                    getattr(adapter, "_runtime", None), "audit_logger", None
                )
            if audit_logger is None or not hasattr(audit_logger, "health_status"):
                continue
            logger_id = id(audit_logger)
            if logger_id in seen_loggers:
                continue
            seen_loggers.add(logger_id)
            status = audit_logger.health_status()
            failures += int(status.get("failures", 0) or 0)
            wizard_step_failures += int(status.get("wizard_step_failures", 0) or 0)
            if status.get("last_error"):
                last_error = str(status["last_error"])

        return {
            "audit": {
                "healthy": failures == 0,
                "failures": failures,
                "last_error": last_error,
            },
            "binding_warnings": binding_warnings,
            "wizard_step_failures": wizard_step_failures,
        }

    def _emit_event(self, event: str, payload: dict[str, Any]) -> None:
        if self._event_sink is not None:
            self._event_sink(event, dict(payload))
        self._logger.info("extension event=%s payload=%s", event, payload)

    def _validate_contract(self) -> None:
        missing = [
            name
            for name in (
                "enabled_plugins",
                "enabled_channels",
                "channels",
                "agents",
                "security",
            )
            if not hasattr(self._config, name)
        ]
        if missing:
            raise RuntimeError(
                "LifecycleService requires config keys: " + ", ".join(missing)
            )

        groups = [
            name for name in self.EXPECTED_ENTRY_POINT_GROUPS if str(name).strip()
        ]
        if len(groups) != len(set(groups)):
            raise RuntimeError("LifecycleService entry point groups must be unique.")
        for name in groups:
            if not isinstance(name, str) or not name.strip():
                raise RuntimeError(
                    "LifecycleService entry point groups must be non-empty strings."
                )
        # Touch entry point groups so missing/renamed groups surface during tests.
        for group in groups:
            entry_points(group=group)


def build_channel_registry(
    *,
    config: OpenMinionConfig,
    home_root: Path,
    data_root: Path,
    logger: logging.Logger,
) -> ChannelRegistry:
    registry = ChannelRegistry([ConsoleChannel()])
    enabled_channels = {
        str(item).strip().lower()
        for item in getattr(config, "enabled_channels", []) or []
        if str(item).strip()
    }
    if "telegram" in enabled_channels:
        registry.register(
            _build_telegram_adapter(
                config=config,
                home_root=home_root,
                data_root=data_root,
                logger=logger.getChild("telegram"),
            )
        )
    if "slack" in enabled_channels:
        registry.register(
            _build_slack_adapter(
                config=config,
                home_root=home_root,
                data_root=data_root,
                logger=logger.getChild("slack"),
            )
        )
    return registry


def _controlplane_runtime_factory(
    *, home_root: Path, data_root: Path
) -> Callable[[str | None], OpenMinionRuntime]:
    def build(config_path: str | None) -> OpenMinionRuntime:
        return OpenMinionRuntime.from_config_path(
            config_path,
            home_root=str(home_root),
            data_root=str(data_root),
        )

    return build


def _build_controlplane_brain(
    *, cp_cfg: Any, home_root: Path, data_root: Path, echo_brain: Any, client: Any
) -> Any:
    if not cp_cfg.openminion_enabled:
        return echo_brain()
    return client(
        config_path=cp_cfg.openminion_config_path,
        home_root=str(home_root),
        data_root=str(data_root),
        runtime_factory=_controlplane_runtime_factory(
            home_root=home_root,
            data_root=data_root,
        ),
        channel=cp_cfg.openminion_channel,
        target=cp_cfg.openminion_target,
        deliver=cp_cfg.openminion_deliver,
    )


def _build_telegram_adapter(
    *,
    config: OpenMinionConfig,
    home_root: Path,
    data_root: Path,
    logger: logging.Logger,
) -> Any:
    from openminion.modules.controlplane.runtime.audit import AuditLogger
    from openminion.modules.controlplane.runtime.auth import AuthEvaluator
    from openminion.modules.controlplane.runtime.parser import (
        SlashCommandParser,
    )
    from openminion.modules.controlplane.commands.registry import CommandRegistry
    from openminion.modules.controlplane.config import (
        from_base_config as controlplane_from_base_config,
    )
    from openminion.modules.controlplane.constants import (
        PRINCIPAL_BINDING_STATUS_ACTIVE,
    )
    from openminion.modules.controlplane.runtime.dispatcher import (
        ControlPlaneDispatcher,
    )
    from openminion.modules.controlplane.adapters.client import (
        OpenMinionBrainClient,
    )
    from openminion.modules.controlplane.runtime.channels import (
        ChannelRegistry as ControlPlaneChannelRegistry,
    )
    from openminion.modules.controlplane.runtime.rate_limit import (
        ControlPlaneRateLimiter,
        RateLimitPolicy,
    )
    from openminion.modules.controlplane.runtime.router import Router
    from openminion.modules.controlplane.runtime import EchoBrain
    from openminion.modules.controlplane.runtime.worker.outbox import OutboxWorker
    from openminion.modules.controlplane.storage import (
        SQLiteControlPlaneStore,
        build_controlplane_store,
    )
    from openminion.modules.controlplane.wizard.store import (
        SqliteWizardStore,
        register_store as register_wizard_store,
    )
    from openminion.modules.controlplane.channels.telegram.bot_api import TelegramBotAPI
    from openminion.modules.controlplane.channels.telegram.config import (
        from_base_config as telegram_from_base_config,
    )
    from openminion.modules.controlplane.channels.telegram.delivery import (
        TelegramDeliveryService,
    )
    from openminion.modules.controlplane.channels.telegram.polling import (
        TelegramPollingRunner,
    )
    from openminion.modules.controlplane.channels.telegram.state import (
        TelegramPollStateStore,
    )
    from openminion.modules.controlplane.channels.telegram.webhook import (
        TelegramWebhookRunner,
    )
    from openminion.modules.storage.engine import StorageEngineConfig

    cp_cfg = controlplane_from_base_config(
        base_config=config,
        home_root=home_root,
        data_root=data_root,
    )
    tg_cfg = telegram_from_base_config(
        base_config=config,
        home_root=home_root,
        data_root=data_root,
    ).telegram

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
    binding_warning_count, binding_scan_count = _audit_cross_owner_bindings(
        store=store,
        logger=logger,
    )
    router = Router(store, auth=auth, audit_logger=audit_logger)
    parser = SlashCommandParser()
    command_registry = CommandRegistry(
        store=store,
        auth=auth,
        audit_logger=audit_logger,
    )
    brain = _build_controlplane_brain(
        cp_cfg=cp_cfg,
        home_root=home_root,
        data_root=data_root,
        echo_brain=EchoBrain,
        client=OpenMinionBrainClient,
    )
    runtime = ControlPlaneDispatcher(
        store=store,
        router=router,
        parser=parser,
        command_registry=command_registry,
        brain_client=brain,
        outbound_sender=lambda _payload: None,
        audit_logger=audit_logger,
    )

    api = TelegramBotAPI(tg_cfg.bot_token)
    delivery = TelegramDeliveryService(
        api=api,
        delivery_config=tg_cfg.delivery,
        reply_config=tg_cfg.reply,
    )
    state_store = TelegramPollStateStore(tg_cfg.polling.state_sqlite_path)
    runner_cls = (
        TelegramWebhookRunner if tg_cfg.mode == "webhook" else TelegramPollingRunner
    )
    # build a controlplane ChannelRegistry that the OutboxWorker uses
    cp_channel_registry = ControlPlaneChannelRegistry()
    outbox_worker = OutboxWorker(
        store=store,
        registry=cp_channel_registry,
        audit_logger=audit_logger,
        max_attempts=cp_cfg.outbox_max_attempts,
        max_backoff_s=cp_cfg.outbox_max_backoff_s,
    )
    # rate limiter is checked between Router.resolve and outbox
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
    runner = runner_cls(
        config=tg_cfg,
        api=api,
        runtime=runtime,
        delivery=delivery,
        state_store=state_store,
        audit_logger=audit_logger,
        logger=logger,
        store=store,
        outbox_worker=outbox_worker,
        rate_limiter=rate_limiter,
        brain_client=brain,
    )
    cp_channel_registry.register(runner)
    setattr(runner, "_binding_warning_count", binding_warning_count)
    setattr(runner, "_binding_scan_count", binding_scan_count)
    return runner


def _audit_cross_owner_bindings(
    *,
    store: Any,
    logger: logging.Logger,
    limit: int = 1000,
) -> tuple[int, int]:
    if not hasattr(store, "list_session_bindings"):
        return 0, 0

    warnings = 0
    scanned = 0
    for binding in store.list_session_bindings(limit=limit):
        scanned += 1
        binding_chat_key = str(binding.get("chat_key") or "").strip()
        session_id = str(binding.get("session_id") or "").strip()
        owner_user_key = str(binding.get("owner_user_key") or "").strip()
        session_chat_key = str(binding.get("session_chat_key") or "").strip()
        if (
            not binding_chat_key
            or not session_id
            or not owner_user_key
            or not session_chat_key
        ):
            continue
        if binding_chat_key == session_chat_key:
            continue
        warnings += 1
        logger.warning(
            "controlplane.security.binding.crossowner.detected "
            "chat_key=%s session_id=%s owner_user_key=%s session_chat_key=%s",
            binding_chat_key,
            session_id,
            owner_user_key,
            session_chat_key,
        )
    return warnings, scanned


def _build_slack_adapter(
    *,
    config: OpenMinionConfig,
    home_root: Path,
    data_root: Path,
    logger: logging.Logger,
) -> Any:
    from openminion.modules.controlplane.adapters.client import OpenMinionBrainClient
    from openminion.modules.controlplane.channels.slack.adapter import (
        build_slack_runner,
    )
    from openminion.modules.controlplane.channels.slack.bot_api import SlackWebAPI
    from openminion.modules.controlplane.channels.slack.config import (
        from_base_config as slack_from_base_config,
    )
    from openminion.modules.controlplane.channels.slack.delivery import (
        SlackDeliveryService,
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

    cp_cfg = controlplane_from_base_config(
        base_config=config,
        home_root=home_root,
        data_root=data_root,
    )
    slack_cfg = slack_from_base_config(
        base_config=config,
        home_root=home_root,
        data_root=data_root,
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
    binding_warning_count, binding_scan_count = _audit_cross_owner_bindings(
        store=store,
        logger=logger,
    )
    router = Router(store, auth=auth, audit_logger=audit_logger)
    command_registry = CommandRegistry(
        store=store,
        auth=auth,
        audit_logger=audit_logger,
    )
    brain = _build_controlplane_brain(
        cp_cfg=cp_cfg,
        home_root=home_root,
        data_root=data_root,
        echo_brain=EchoBrain,
        client=OpenMinionBrainClient,
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
    # The Slack helper path currently applies adapter-level filtering. The
    # constructed limiter is attached for the next shared-helper pass and for
    # parity with Telegram's lifecycle wiring.
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
    setattr(runner, "_binding_warning_count", binding_warning_count)
    setattr(runner, "_binding_scan_count", binding_scan_count)
    cp_channel_registry.register(runner)
    return runner


def _load_tool_plugin_statuses() -> list[dict[str, Any]]:
    class _AllowAllPolicy:
        def is_plugin_enabled(self, name: str) -> bool:
            return True

    registry = ToolRegistry([])
    return load_tool_plugins(registry, _AllowAllPolicy())


def _plugin_statuses(
    catalog: ExtensionCatalog, registry: PluginRegistry
) -> list[dict[str, Any]]:
    loaded_ids = set(registry.manifest_ids())
    loaded_names = set(registry.names())
    statuses: list[dict[str, Any]] = []
    for record in catalog.plugins:
        loaded = record.name in loaded_ids or record.name in loaded_names
        statuses.append(
            {
                "name": record.name,
                "source": record.source,
                "enabled": record.enabled,
                "loaded": loaded,
                "installed": record.installed,
                "error": record.error,
                "metadata": dict(record.metadata),
            }
        )
    return statuses


def _build_sidecar_manager(
    *,
    catalog: ExtensionCatalog,
    config_path: str | None,
    runtime_env: dict[str, str] | None,
    policy: SecurityPolicyEngine,
    agent_id: str,
    logger: logging.Logger,
) -> SidecarManager | None:
    if not catalog.sidecars:
        return None
    sidecar_logger = logger.getChild("sidecars")
    base_manager = default_sidecar_manager(
        config_path=config_path,
        runtime_env=runtime_env,
        policy=policy,
        actor=default_internal_actor(agent_id=agent_id, include_admin=True),
        context=SecurityPolicyContext(channel="runtime", target="sidecar"),
        logger=sidecar_logger,
    )
    allowed = {record.name for record in catalog.sidecars}
    specs = [spec for spec in base_manager.specs() if spec.name in allowed]
    if not specs:
        return None
    return SidecarManager(
        specs=specs,
        config_path=config_path,
        runtime_env=runtime_env,
        policy=policy,
        actor=default_internal_actor(agent_id=agent_id, include_admin=True),
        context=SecurityPolicyContext(channel="runtime", target="sidecar"),
        logger=sidecar_logger,
    )
