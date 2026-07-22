from dataclasses import dataclass
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any, Callable, cast
import logging

from openminion.base.channel import ChannelRegistry, ConsoleChannel
from openminion.base.config import OpenMinionConfig
from openminion.base.config.core import resolve_default_agent_id
from openminion.base.config.paths import resolve_data_root, resolve_home_root
from openminion.services.runtime.catalog import ExtensionCatalog
from openminion.services.runtime.channel_supervisor import ChannelRuntimeSupervisor
from openminion.services.runtime.controlplane import (
    ControlPlaneRuntimeComponents,
    build_controlplane_runtime_components,
)
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
    controlplane_components: ControlPlaneRuntimeComponents | None = None
    channel_supervisor: ChannelRuntimeSupervisor | None = None


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
        channels, controlplane_components = build_channel_registry(
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
            extra_specs=controlplane_components.sidecar_specs
            if controlplane_components is not None
            else (),
        )
        channel_supervisor = None
        if controlplane_components is not None:
            channel_supervisor = ChannelRuntimeSupervisor(
                channels=channels,
                inbox_worker=controlplane_components.inbox_worker,
                outbox_worker=controlplane_components.outbox_worker,
                close_runtime=controlplane_components.close,
                logger=self._logger.getChild("channel_supervisor"),
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
            controlplane_components=controlplane_components,
            channel_supervisor=channel_supervisor,
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
            "channel_runtime": _channel_runtime_status(runtime.channel_supervisor),
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


def _channel_runtime_status(
    supervisor: ChannelRuntimeSupervisor | None,
) -> dict[str, Any]:
    if supervisor is None:
        return {"state": "not_observed", "channels": {}}
    return cast(dict[str, Any], supervisor.status().to_dict())


def build_channel_registry(
    *,
    config: OpenMinionConfig,
    home_root: Path,
    data_root: Path,
    logger: logging.Logger,
) -> tuple[ChannelRegistry, ControlPlaneRuntimeComponents | None]:
    registry = ChannelRegistry([ConsoleChannel()])
    enabled_channels = {
        str(item).strip().lower()
        for item in getattr(config, "enabled_channels", []) or []
        if str(item).strip()
    }
    needs_controlplane = bool({"telegram", "slack"} & enabled_channels)
    components = (
        build_controlplane_runtime_components(
            config=config,
            home_root=home_root,
            data_root=data_root,
            logger=logger.getChild("controlplane"),
        )
        if needs_controlplane
        else None
    )
    if "telegram" in enabled_channels:
        assert components is not None
        registry.register(
            _build_telegram_adapter(
                config=config,
                home_root=home_root,
                data_root=data_root,
                logger=logger.getChild("telegram"),
                components=components,
            )
        )
    if "slack" in enabled_channels:
        assert components is not None
        registry.register(
            _build_slack_adapter(
                config=config,
                home_root=home_root,
                data_root=data_root,
                logger=logger.getChild("slack"),
                components=components,
            )
        )
    return registry, components


def _build_telegram_adapter(
    *,
    config: OpenMinionConfig,
    home_root: Path,
    data_root: Path,
    logger: logging.Logger,
    components: ControlPlaneRuntimeComponents,
) -> Any:
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
    from openminion.modules.controlplane.pairing import ControlPlanePairingStore
    from openminion.modules.controlplane.pairing.migration import PairingMigrationJob

    tg_cfg = telegram_from_base_config(
        base_config=config,
        home_root=home_root,
        data_root=data_root,
    ).telegram
    binding_warning_count, binding_scan_count = _audit_cross_owner_bindings(
        store=components.store,
        logger=logger,
    )

    api = TelegramBotAPI(tg_cfg.bot_token)
    delivery = TelegramDeliveryService(
        api=api,
        delivery_config=tg_cfg.delivery,
        reply_config=tg_cfg.reply,
    )
    state_store = TelegramPollStateStore(tg_cfg.polling.state_sqlite_path)
    PairingMigrationJob(
        legacy_store=state_store,
        new_store=ControlPlanePairingStore(components.store),
        audit_logger=components.audit_logger,
        logger=logger,
    ).run_once()
    runner_cls = (
        TelegramWebhookRunner if tg_cfg.mode == "webhook" else TelegramPollingRunner
    )
    runner = runner_cls(
        config=tg_cfg,
        api=api,
        runtime=components.dispatcher,
        delivery=delivery,
        state_store=state_store,
        audit_logger=components.audit_logger,
        logger=logger,
        store=components.store,
        outbox_worker=components.outbox_worker,
        rate_limiter=components.rate_limiter,
        brain_client=components.brain_client,
    )
    components.delivery_registry.register(runner)
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
    components: ControlPlaneRuntimeComponents,
) -> Any:
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

    slack_cfg = slack_from_base_config(
        base_config=config,
        home_root=home_root,
        data_root=data_root,
    ).slack
    binding_warning_count, binding_scan_count = _audit_cross_owner_bindings(
        store=components.store,
        logger=logger,
    )
    api = SlackWebAPI(slack_cfg.bot_token)
    delivery = SlackDeliveryService(
        api=api,
        delivery_config=slack_cfg.delivery,
        audit_logger=components.audit_logger,
    )
    state_store = SlackStateStore(slack_cfg.state_sqlite_path)
    runner = build_slack_runner(
        config=slack_cfg,
        runtime=components.dispatcher,
        delivery=delivery,
        state_store=state_store,
        audit_logger=components.audit_logger,
        logger=logger,
        store=components.store,
        outbox_worker=components.outbox_worker,
    )
    setattr(runner, "_rate_limiter", components.rate_limiter)
    setattr(runner, "_brain_client", components.brain_client)
    setattr(runner, "_binding_warning_count", binding_warning_count)
    setattr(runner, "_binding_scan_count", binding_scan_count)
    components.delivery_registry.register(runner)
    return runner


def _load_tool_plugin_statuses() -> list[dict[str, Any]]:
    class _AllowAllPolicy:
        def is_plugin_enabled(self, name: str) -> bool:
            return True

    registry = ToolRegistry([])
    return cast(list[dict[str, Any]], load_tool_plugins(registry, _AllowAllPolicy()))


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
    extra_specs: list[Any] | tuple[Any, ...] = (),
) -> SidecarManager | None:
    if not catalog.sidecars and not extra_specs:
        return None
    sidecar_logger = logger.getChild("sidecars")
    specs = list(extra_specs)
    if catalog.sidecars:
        base_manager = default_sidecar_manager(
            config_path=config_path,
            runtime_env=runtime_env,
            policy=policy,
            actor=default_internal_actor(agent_id=agent_id, include_admin=True),
            context=SecurityPolicyContext(channel="runtime", target="sidecar"),
            logger=sidecar_logger,
        )
        allowed = {record.name for record in catalog.sidecars}
        specs.extend(spec for spec in base_manager.specs() if spec.name in allowed)
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
