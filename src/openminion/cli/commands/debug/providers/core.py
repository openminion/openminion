from __future__ import annotations

from typing import Any

from openminion.api.runtime import APIRuntime
from openminion.base.version import OPENMINION_VERSION
from openminion.cli.bootstrap.loader import load_config
from openminion.services.diagnostics.debug import (
    DebugProvider,
    DebugStatus,
    ModuleDebugPayload,
    WiringSource,
)


def _runtime_failure_payload(
    module: str,
    exc: BaseException,
    *,
    details: dict[str, Any] | None = None,
) -> ModuleDebugPayload:
    return ModuleDebugPayload(
        module=module,
        status=DebugStatus.FAIL,
        mode="runtime",
        wiring_source=WiringSource.UNKNOWN,
        last_error=str(exc),
        details=details,
    )


def _close_runtime(runtime: APIRuntime | None) -> None:
    if runtime is not None:
        runtime.close()


class _CoreDebugProvider(DebugProvider):
    MODULE_NAME: str = ""

    def __init__(self) -> None:
        super().__init__(
            module_name=self.MODULE_NAME,
            probe_fn=self._probe,
            wiring_check_fn=None,
        )


class OpenMinionDebugProvider(_CoreDebugProvider):
    MODULE_NAME = "openminion"

    def _check_dependencies(self) -> list[dict[str, Any]]:
        failures = []

        try:
            import openminion.modules.brain  # noqa: F401
        except ImportError as exc:
            failures.append(
                {
                    "module": "openminion-brain",
                    "type": "import_error",
                    "error": str(exc),
                    "impact": "brain runtime mode unavailable",
                }
            )

        try:
            import openminion.modules.brain.meta  # noqa: F401
        except ImportError:
            try:
                import openminion.modules.brain.meta as openminion_brain_meta  # noqa: F401
            except ImportError as exc:
                failures.append(
                    {
                        "module": "openminion-brain-meta",
                        "type": "import_error",
                        "error": str(exc),
                        "impact": "meta-evaluation unavailable",
                    }
                )

        try:
            import openminion.modules.session  # noqa: F401
        except ImportError as exc:
            failures.append(
                {
                    "module": "openminion-session",
                    "type": "import_error",
                    "error": str(exc),
                    "impact": "session persistence unavailable",
                }
            )

        try:
            import openminion.modules.telemetry  # noqa: F401
        except ImportError as exc:
            failures.append(
                {
                    "module": "openminion-telemetry",
                    "type": "import_error",
                    "error": str(exc),
                    "impact": "telemetry disabled",
                }
            )

        return failures

    def _probe(self) -> ModuleDebugPayload:
        runtime = None
        try:
            runtime = APIRuntime.from_config_path(None)
            dependency_failures = self._check_dependencies()
            status = DebugStatus.OK if not dependency_failures else DebugStatus.WARN

            if dependency_failures:
                self._emit_debug_events(dependency_failures)

            legacy_blocked_reason = getattr(runtime, "_last_bridge_fallback_reason", "")
            legacy_blocked: bool | str = (
                legacy_blocked_reason if legacy_blocked_reason else False
            )

            return ModuleDebugPayload(
                module=self.MODULE_NAME,
                status=status,
                mode="runtime",
                wiring_source=WiringSource.REAL,
                dependency_failures=dependency_failures,
                details={
                    "version": OPENMINION_VERSION,
                    "agent_runtime_mode": getattr(runtime, "_runtime_mode", "unknown"),
                    "brain_bridge_active": getattr(
                        runtime, "_brain_bridge_active", False
                    ),
                    "last_bridge_fallback_reason": getattr(
                        runtime, "_last_bridge_fallback_reason", ""
                    ),
                    "legacy_blocked": legacy_blocked,
                },
            )
        except Exception as exc:
            legacy_blocked_fail: bool | str = str(exc) or False
            return _runtime_failure_payload(
                self.MODULE_NAME,
                exc,
                details={"legacy_blocked": legacy_blocked_fail},
            )
        finally:
            _close_runtime(runtime)

    def _emit_debug_events(self, dependency_failures: list[dict[str, Any]]) -> None:
        try:
            from openminion.services.session_store import SessionStore

            config = load_config(None)
            storage_path = getattr(config.runtime, "storage_path", ".openminion")

            store = SessionStore(root=storage_path)

            for failure in dependency_failures:
                event_payload = {
                    "module": failure.get("module"),
                    "failure_type": failure.get("type"),
                    "error": failure.get("error"),
                    "impact": failure.get("impact"),
                    "probe": "dependency_check",
                }
                store.append_event(
                    session_id="__debug_events__",
                    event_type="module.debug.failure",
                    payload=event_payload,
                )
        except Exception:
            pass


class OpenMinionToolsDebugProvider(_CoreDebugProvider):
    MODULE_NAME = "openminion-tool"

    def _probe(self) -> ModuleDebugPayload:
        runtime = None
        try:
            runtime = APIRuntime.from_config_path(None)
            tool_count = len(runtime.tools.provider_specs())
            return ModuleDebugPayload(
                module=self.MODULE_NAME,
                status=DebugStatus.OK,
                mode="runtime",
                wiring_source=WiringSource.REAL,
                details={"tool_count": tool_count},
            )
        except Exception as exc:
            return _runtime_failure_payload(self.MODULE_NAME, exc)
        finally:
            _close_runtime(runtime)


class OpenMinionPluginsDebugProvider(_CoreDebugProvider):
    MODULE_NAME = "openminion-plugins"

    def _probe(self) -> ModuleDebugPayload:
        runtime = None
        try:
            runtime = APIRuntime.from_config_path(None)
            plugin_names = runtime.plugins.names()
            return ModuleDebugPayload(
                module=self.MODULE_NAME,
                status=DebugStatus.OK,
                mode="runtime",
                wiring_source=WiringSource.REAL if plugin_names else WiringSource.STUB,
                details={"loaded_plugins": plugin_names},
            )
        except Exception as exc:
            return _runtime_failure_payload(self.MODULE_NAME, exc)
        finally:
            _close_runtime(runtime)
