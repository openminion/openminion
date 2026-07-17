import logging
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from openminion.base.config import (
    OpenMinionConfig,
    bootstrap_home_paths,
    resolve_module_storage_path,
)
from openminion.base.config import ConfigManager
from openminion.base.config.core import resolve_default_agent_id
from openminion.base.config.env import EnvironmentConfig
from openminion.services.runtime.plugins import PluginRegistry
from openminion.services.agent import AgentService
from openminion.services.agent.memory import (  # noqa: F401  (re-export for test patches + canonical runner_metadata resolution)
    build_memory_policy_snapshot,
)
from openminion.modules.policy import SecurityPolicyEngine
from openminion.services.lifecycle.self_improvement import SelfImprovementEngine
from openminion.modules.tool import ToolRegistry

from openminion.modules.brain.runner import BrainRunner
from openminion.modules.brain.interfaces import (
    ensure_adapter_compatibility,
    ensure_runner_compatibility,
)
from openminion.modules.brain.adapters.factory import create_llm_adapter
from openminion.modules.brain.retry import call_structured_with_retry
from openminion.services.brain.factory.adapter import (
    create_a2a_api,
    create_compress_api,
    create_context_api,
    create_memory_api,
    create_policy_api,
    create_safety_api,
    create_session_api,
    create_skill_api,
    create_tool_api,
)
from openminion.services.brain.context import BrainBridgeContext
from openminion.services.brain.post_execution import BrainBridgeTurnMixin
from openminion.services.brain.factory.retrieve import init_retrieve_adapter  # noqa: F401  (re-export for test patches + bootstrap helper)
from openminion.services.brain.factory.rlm import init_rlm_adapter
from openminion.services.config import resolve_services_env
from openminion.modules.brain.schemas import (
    ModeProfileConfig,
)
from openminion.modules.brain.config import (
    from_base_config as derive_brain_runtime_config,
)

try:
    from openminion.modules.telemetry.adapter import create_telemetry_adapter
    from openminion.modules.telemetry.service import TelemetryCtl

    TELEMETRY_AVAILABLE = True
except ImportError:
    TELEMETRY_AVAILABLE = False
    TelemetryCtl = None


class _SessionSummaryStructureReport(BaseModel):
    class ActiveThread(BaseModel):
        topic: str = ""
        status: Literal["open", "paused", "done"] = "open"
        next_step: str = ""

    outcome: Literal[
        "succeeded",
        "blocked",
        "no_prior_context",
        "abandoned",
        "unknown",
    ] = "unknown"
    summary_text: str = ""
    decisions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    corrections: list[str] = Field(default_factory=list)
    topic_keywords: list[str] = Field(default_factory=list)
    active_threads: list[ActiveThread] = Field(default_factory=list)


# replaced 9 pass-through wrappers with module-attribute aliases.
create_session_adapter = create_session_api
create_context_adapter = create_context_api
create_tool_adapter = create_tool_api
create_a2a_adapter = create_a2a_api
create_memory_adapter = create_memory_api
create_policy_adapter = create_policy_api
create_safety_adapter = create_safety_api
create_skill_adapter = create_skill_api
create_rlm_adapter = init_rlm_adapter  # kept distinct source name by intent
create_compress_adapter = create_compress_api


def _runtime_mode_config_from_agent(
    config: OpenMinionConfig,
) -> dict[str, ModeProfileConfig]:

    raw_modes: dict[str, Any] = {}
    try:
        default_agent_id = resolve_default_agent_id(config)
        profile = config.agents.get(default_agent_id)
        if profile is not None:
            raw_modes = dict(getattr(profile, "modes", {}) or {})
    except Exception:  # noqa: BLE001
        raw_modes = {}
    mode_config: dict[str, ModeProfileConfig] = {}
    if not isinstance(raw_modes, dict):
        return mode_config
    for mode_name, entry in raw_modes.items():
        normalized_name = str(mode_name or "").strip().lower()
        if not normalized_name:
            continue
        mode_config[normalized_name] = ModeProfileConfig(
            enabled=bool(getattr(entry, "enabled", True)),
            parallel_enabled=getattr(entry, "parallel_enabled", None),
            parallel_writes_enabled=getattr(entry, "parallel_writes_enabled", None),
            max_parallel_workers=getattr(entry, "max_parallel_workers", None),
            checkpoint_interval=getattr(entry, "checkpoint_interval", None),
            max_resume_count=getattr(entry, "max_resume_count", None),
            max_depth=getattr(entry, "max_depth", None),
            priority_hint=getattr(entry, "priority_hint", None),
            max_commands_per_turn=getattr(entry, "max_commands_per_turn", None),
            max_adaptive_iterations=getattr(entry, "max_adaptive_iterations", None),
            max_adaptive_tool_calls_per_loop=getattr(
                entry, "max_adaptive_tool_calls_per_loop", None
            ),
            max_adaptive_llm_calls_per_loop=getattr(
                entry, "max_adaptive_llm_calls_per_loop", None
            ),
            adaptive_include_reflect=getattr(entry, "adaptive_include_reflect", None),
            max_self_corrections=getattr(entry, "max_self_corrections", None),
            max_subtasks=getattr(entry, "max_subtasks", None),
            max_decompose_depth=getattr(entry, "max_decompose_depth", None),
            max_research_iterations=getattr(entry, "max_research_iterations", None),
            tool_schema_shortlisting_enabled=getattr(
                entry, "tool_schema_shortlisting_enabled", None
            ),
        )
    return mode_config


def _bootstrap_bridge_home_paths(
    *,
    config: OpenMinionConfig,
    workspace_root: str,
    home_root: str | Path | None,
    data_root: str | Path | None,
    config_path: str | Path | None,
    config_manager: ConfigManager | None,
) -> Any:
    home_root_override = home_root
    data_root_override = data_root
    config_path_override = config_path
    if config_manager is not None:
        home_root_override = config_manager.home_root
        data_root_override = config_manager.data_root
        if config_path_override is None:
            config_path_override = config_manager.config_path
    return bootstrap_home_paths(
        config_path=(
            str(config_path_override)
            if config_path_override is not None
            else getattr(config, "_config_path", None)
        ),
        workspace_root=(
            str(home_root_override)
            if home_root_override is not None
            else workspace_root
        ),
        data_root=str(data_root_override) if data_root_override is not None else None,
    )


class _RuntimeProviderAdapter:
    def __init__(self, service: "BrainBridgeService") -> None:
        self._service = service
        self.name = str(getattr(service, "_provider_name", lambda: "provider")())
        self.tool_call_strategy = str(
            getattr(getattr(service, "_provider", object()), "tool_call_strategy", "")
            or getattr(
                getattr(service, "_llm_runtime", object()), "tool_call_strategy", ""
            )
            or "hybrid"
        )

    async def generate(self, request: Any) -> Any:
        return await self._service._invoke_provider_request(request)


class BrainBridgeService(BrainBridgeTurnMixin, AgentService):
    """
    A bridge adapter that implements the `AgentService` interface
    and processes turns using the `openminion-brain` `BrainRunner`.
    """

    def __init__(
        self,
        config: OpenMinionConfig,
        plugins: PluginRegistry,
        provider: object,
        logger: logging.Logger,
        tools: ToolRegistry | None = None,
        security_policy: SecurityPolicyEngine | None = None,
        self_improvement: SelfImprovementEngine | None = None,
        llm_runtime: object | None = None,
        *,
        mode: str = "auto",
        db_path: str = ".openminion/brain/sessions.db",
        workspace_root: str = ".",
        home_root: str | Path | None = None,
        data_root: str | Path | None = None,
        config_path: str | Path | None = None,
        config_manager: ConfigManager | None = None,
        retrieve_service: Any | None = None,
        action_policy_service: Any | None = None,
    ) -> None:
        super().__init__(
            config=config,
            plugins=plugins,
            provider=provider,
            llm_runtime=llm_runtime,
            logger=logger,
            tools=tools,
            security_policy=security_policy,
            self_improvement=self_improvement,
        )
        self._init_bridge_state(
            config=config,
            mode=mode,
            db_path=db_path,
            workspace_root=workspace_root,
            home_root=home_root,
            data_root=data_root,
            config_path=config_path,
            config_manager=config_manager,
            retrieve_service=retrieve_service,
            action_policy_service=action_policy_service,
        )
        self._init_bridge_telemetry(config=config)
        self._context = BrainBridgeContext(
            home_paths=self._home_paths,
            workspace_root=self.workspace_root,
            config_manager=self._config_manager,
            telemetryctl=self._telemetryctl,
            mode=self.mode,
        )

    def _init_bridge_state(
        self,
        *,
        config: OpenMinionConfig,
        mode: str,
        db_path: str,
        workspace_root: str,
        home_root: str | Path | None,
        data_root: str | Path | None,
        config_path: str | Path | None,
        config_manager: ConfigManager | None,
        retrieve_service: Any | None,
        action_policy_service: Any | None,
    ) -> None:
        self.mode = mode
        self.db_path = db_path
        self._config_manager = config_manager
        self._retrieve_service = retrieve_service
        self._action_policy_service = action_policy_service
        runtime_env = getattr(getattr(config, "runtime", None), "env", {})
        self._env = (
            config_manager.env
            if config_manager is not None
            else resolve_services_env(
                runtime_env=runtime_env if isinstance(runtime_env, dict) else {}
            )
        )
        self._home_paths = _bootstrap_bridge_home_paths(
            config=config,
            workspace_root=workspace_root,
            home_root=home_root,
            data_root=data_root,
            config_path=config_path,
            config_manager=config_manager,
        )

        self.workspace_root = str(self._home_paths.home_root)
        self._logger.info(
            "BrainBridgeService initialized with home_root=%s path_mode=%s path_source=%s",
            self._home_paths.home_root,
            self._home_paths.path_mode,
            self._home_paths.path_source,
        )

        self._runner: BrainRunner | None = None
        self._runtime_handle: Any | None = None

    def _init_bridge_telemetry(self, *, config: OpenMinionConfig) -> None:
        self._telemetryctl: TelemetryCtl | None = None
        self._telemetry_enabled = getattr(config.runtime, "telemetry_enabled", False)
        if self._telemetry_enabled and TELEMETRY_AVAILABLE:
            telemetry_path = self._resolve_telemetry_db_path(config)
            self._telemetryctl = create_telemetry_adapter(
                db_path=telemetry_path,
                otel_exporter_config=getattr(
                    config.runtime,
                    "telemetry_exporter",
                    None,
                ),
            )
            self._logger.info(
                "Telemetry enabled for BrainBridgeService: db_path=%s",
                telemetry_path,
            )

    def _resolve_telemetry_db_path(self, config: OpenMinionConfig) -> str | None:
        """RRPCU-03: Resolve telemetry DB path from OpenMinion Home or explicit config."""
        explicit_path = getattr(config.runtime, "telemetry_db_path", "")
        if explicit_path and str(explicit_path).strip():
            # Explicit override takes precedence
            if Path(explicit_path).is_absolute():
                return str(explicit_path)
            # Relative explicit path - resolve from OpenMinion Home
            return str(self._home_paths.home_root / explicit_path)

        # Default: use OpenMinion Home-derived path
        return str(
            resolve_module_storage_path(
                self._home_paths.home_root,
                "telemetry",
                filename="telemetry.db",
            )
        )

    def _get_manager_config(self, name: str) -> Any | None:
        if self._context.config_manager is None:
            return None
        try:
            return self._context.config_manager.get(name)
        except Exception as exc:  # noqa: BLE001
            self._logger.warning(
                "ConfigManager lookup failed for %s; falling back: %s",
                name,
                exc,
            )
            return None

    def bind_runtime_handle(self, runtime_handle: Any) -> None:
        self._runtime_handle = runtime_handle

    def bridge_diagnostics(self) -> dict[str, Any]:
        """BBSE-07: expose bridge canonical-parts posture for operators."""
        return {
            "config_manager_present": self._config_manager is not None,
            "retrieve_service_present": self._retrieve_service is not None,
            "action_policy_service_present": (self._action_policy_service is not None),
            "home_paths": self._home_paths.to_debug_dict(),
            "runner_assembled": self._runner is not None,
            # BBSE-04 regression: bridge ships zero behavior-changing
            "runner_method_overrides_present": False,
            "mode": self.mode,
        }

    def _resolve_llm_wrapper(self, llm_api: Any) -> Any | None:
        direct = getattr(llm_api, "llm", None)
        if direct is not None:
            return direct
        client = getattr(llm_api, "client", None)
        if client is not None and hasattr(client, "_set_context"):
            return client
        return None

    def _runtime_env_value(self, key: str) -> str:
        runtime_env = getattr(getattr(self, "_config", object()), "runtime", object())
        payload = getattr(runtime_env, "env", {})
        if isinstance(payload, dict):
            value = payload.get(key)
            if value is not None:
                return str(value).strip()
        return ""

    def _resolve_override_value(self, key: str) -> str:
        if isinstance(self._env, EnvironmentConfig):
            value = str(self._env.get(key, "") or "").strip()
            if value:
                return value
            return self._runtime_env_value(key)
        return self._runtime_env_value(key)

    def _resolve_override_bool(self, key: str, *, default: bool) -> bool:
        raw = self._resolve_override_value(key).lower()
        if not raw:
            return bool(default)
        return raw in {"1", "true", "yes", "on"}

    def _resolve_override_int(self, key: str, *, default: int) -> int:
        raw = self._resolve_override_value(key)
        if not raw:
            return int(default)
        try:
            return max(0, int(raw))
        except ValueError:
            return int(default)

    def build_session_summary_structurer(self) -> Any:
        def _structure_summary(
            rolling_summary: str,
            turn_count: int,
        ) -> dict[str, Any] | None:
            summary_text = str(rolling_summary or "").strip()
            if not summary_text:
                return None
            runner = self._get_runner()
            llm_api = getattr(runner, "llm_api", None)
            llm_profiles = getattr(
                getattr(runner, "profile", None), "llm_profiles", None
            )
            model = (
                str(getattr(llm_profiles, "summarize_model", "") or "").strip()
                or str(getattr(llm_profiles, "reflect_model", "") or "").strip()
                or str(getattr(llm_profiles, "act_model", "") or "").strip()
            )
            if llm_api is None or not model:
                return None
            context = {
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You structure prior-session memory for future recall. "
                            "Return JSON only. Summarize the session in 2-3 sentences, "
                            "extract reusable prior decisions, unresolved open questions, "
                            "corrections to earlier assumptions, short topic keywords, "
                            "and any active thread that remains open, paused, or done. "
                            "Set outcome to one of `succeeded`, `blocked`, "
                            "`no_prior_context`, `abandoned`, or `unknown`. Use "
                            "`no_prior_context` only when the assistant explicitly "
                            "failed because it lacked prior-session context. Use "
                            "`blocked` when useful work remains but progress stopped for "
                            "a concrete reason. Use `succeeded` when the session "
                            "advanced normally. Use `abandoned` when the work was "
                            "explicitly dropped. Use `unknown` when the source text "
                            "does not support a stronger classification. "
                            "For each active thread, return a short topic, a status "
                            "(`open`, `paused`, or `done`), and a brief next_step only "
                            "when the source text makes it explicit. "
                            "Do not invent facts that are not present in the source text."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Rolling summary text:\n{summary_text}\n\n"
                            f"Turn count: {max(0, int(turn_count))}\n\n"
                            "Return structured session-summary fields for later recall."
                        ),
                    },
                ],
                "hints": {
                    "user_input": summary_text,
                    "mode_name": "session_summary_structure",
                    "session_summary_turn_count": max(0, int(turn_count)),
                },
            }
            raw = call_structured_with_retry(
                llm_api,
                model=model,
                purpose="summarize",
                context=context,
                schema=_SessionSummaryStructureReport,
            )
            return _SessionSummaryStructureReport.model_validate(raw).model_dump(
                mode="json"
            )

        return _structure_summary

    def _get_runner(self) -> BrainRunner:
        # delegate adapter-graph + runner assembly to the canonical
        if self._runner is not None:
            if not hasattr(self, "_llm_wrapper") or self._llm_wrapper is None:
                llm_config = self._get_manager_config("llm")
                llm_payload = llm_config if llm_config is not None else {}
                llm_api = create_llm_adapter(
                    mode=self.mode,
                    config=llm_payload,
                    telemetryctl=self._telemetryctl,
                )
                self._llm_wrapper = self._resolve_llm_wrapper(llm_api)
            return self._runner

        from openminion.services.runtime.bootstrap import (
            build_brain_runner_bundle,
        )

        self._runner = build_brain_runner_bundle(self)
        return self._runner

    def _resolve_brain_config(self) -> Any | None:
        try:
            runtime_config = derive_brain_runtime_config(
                base_config=self._config,
                home_root=Path(self._home_paths.home_root),
                data_root=Path(self._home_paths.data_root),
            )
        except Exception as exc:  # noqa: BLE001
            self._logger.warning(
                "brain runtime config unavailable for bridge runner options: %s",
                exc,
            )
            return None
        return runtime_config.brain

    def _validate_runner_contract(self, runner: Any) -> None:
        strict = self._resolve_override_bool(
            "OPENMINION_STRICT_ADAPTER_CONTRACTS",
            default=True,
        )
        try:
            ensure_runner_compatibility(runner)
        except Exception as exc:  # noqa: BLE001
            message = f"runner contract check failed: {exc}"
            if strict:
                raise RuntimeError(message) from exc
            self._logger.warning("%s", message)

    def _validate_adapter_contracts(
        self,
        *,
        session_api: Any,
        context_api: Any,
        llm_api: Any,
        tool_api: Any,
        a2a_api: Any,
        memory_api: Any,
        policy_api: Any,
        safety_api: Any,
        rlm_api: Any | None,
        retrieve_api: Any | None,
    ) -> None:
        strict = self._resolve_override_bool(
            "OPENMINION_STRICT_ADAPTER_CONTRACTS",
            default=True,
        )
        checks: list[tuple[str, Any]] = [
            ("session", session_api),
            ("context", context_api),
            ("llm", llm_api),
            ("tool", tool_api),
            ("a2a", a2a_api),
            ("memory", memory_api),
            ("policy", policy_api),
            ("safety", safety_api),
        ]
        if rlm_api is not None:
            checks.append(("rlm", rlm_api))
        if retrieve_api is not None:
            checks.append(("retrieve", retrieve_api))

        for adapter_type, adapter in checks:
            try:
                ensure_adapter_compatibility(adapter, adapter_type=adapter_type)
            except Exception as exc:  # noqa: BLE001
                message = f"adapter contract check failed for {adapter_type}: {exc}"
                if strict:
                    raise RuntimeError(message) from exc
                self._logger.warning("%s", message)

        return
