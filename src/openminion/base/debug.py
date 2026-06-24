"""Debug payloads and provider registry."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class DebugStatus(str, Enum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"
    UNKNOWN = "unknown"


class WiringSource(str, Enum):
    REAL = "real"
    STUB = "stub"
    FALLBACK = "fallback"
    DISABLED = "disabled"
    UNKNOWN = "unknown"


@dataclass
class ModuleDebugPayload:
    module: str
    status: DebugStatus
    mode: str
    wiring_source: WiringSource
    fallback: str | None = None
    last_error: str | None = None
    last_success_at: str | None = None
    evidence_refs: dict[str, Any] = field(default_factory=dict)
    dependency_failures: list[dict[str, Any]] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)
    resolved_path: str | None = None
    path_mode: str | None = None
    path_source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result = {
            "module": self.module,
            "status": self.status.value,
            "mode": self.mode,
            "wiring_source": self.wiring_source.value,
            "fallback": self.fallback,
            "last_error": self.last_error,
            "last_success_at": self.last_success_at,
            "evidence_refs": self.evidence_refs,
            "dependency_failures": self.dependency_failures,
            "details": self.details,
        }
        if self.resolved_path:
            result["resolved_path"] = self.resolved_path
        if self.path_mode:
            result["path_mode"] = self.path_mode
        if self.path_source:
            result["path_source"] = self.path_source
        return result


def create_path_debug_payload(
    module: str,
    *,
    resolved_path: str | None,
    path_mode: str,
    path_source: str,
    status: DebugStatus = DebugStatus.OK,
    **details: Any,
) -> ModuleDebugPayload:
    return ModuleDebugPayload(
        module=module,
        status=status,
        mode=path_mode,
        wiring_source=WiringSource.REAL,
        resolved_path=str(resolved_path) if resolved_path else None,
        path_mode=path_mode,
        path_source=path_source,
        details=details,
    )


@dataclass
class DebugProvider:
    module_name: str
    probe_fn: Callable[[], ModuleDebugPayload]
    wiring_check_fn: Callable[[], WiringSource] | None = None
    last_error: str | None = None
    last_success_at: str | None = None
    _debug_events: list[dict[str, Any]] = field(default_factory=list)

    def get_debug(self) -> ModuleDebugPayload:
        return self.probe_fn()

    def get_wiring(self) -> WiringSource:
        if self.wiring_check_fn:
            return self.wiring_check_fn()
        return WiringSource.UNKNOWN


class DebugRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, DebugProvider] = {}

    def register(self, provider: DebugProvider) -> None:
        self._providers[provider.module_name] = provider

    def unregister(self, module_name: str) -> None:
        self._providers.pop(module_name, None)

    def list_modules(self) -> list[str]:
        return sorted(self._providers.keys())

    def get_module(self, module_name: str) -> DebugProvider | None:
        return self._providers.get(module_name)

    def get_all_debug(self) -> list[ModuleDebugPayload]:
        results = []
        for module_name in sorted(self._providers.keys()):
            provider = self._providers[module_name]
            try:
                results.append(provider.get_debug())
            except Exception as exc:  # pragma: no cover - defensive
                results.append(
                    ModuleDebugPayload(
                        module=module_name,
                        status=DebugStatus.FAIL,
                        mode="unknown",
                        wiring_source=WiringSource.UNKNOWN,
                        last_error=str(exc),
                    )
                )
        return results


_global_registry: DebugRegistry | None = None


def get_debug_registry() -> DebugRegistry:
    global _global_registry
    if _global_registry is None:
        _global_registry = DebugRegistry()
    return _global_registry


def set_debug_registry(registry: DebugRegistry) -> None:
    global _global_registry
    _global_registry = registry
