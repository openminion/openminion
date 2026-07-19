from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from openminion.modules.controlplane.contracts.policy_client import PolicyClient
from openminion.modules.controlplane.runtime.client import RuntimeClient
from openminion.modules.controlplane.runtime.store import InMemoryControlPlaneStore

from .broken_module import BrokenModuleTracker
from .module import CommandSpec, Handler
from .registry_base import CommandRegistryBaseMixin
from .registry_memory import CommandRegistryMemorySkillMixin
from .registry_pairing import CommandRegistryPairingMixin
from .registry_runs import CommandRegistryRuntimeMixin
from .registry_session import CommandRegistrySessionMixin


@dataclass
class CommandRegistry(
    CommandRegistrySessionMixin,
    CommandRegistryRuntimeMixin,
    CommandRegistryPairingMixin,
    CommandRegistryMemorySkillMixin,
    CommandRegistryBaseMixin,
):
    store: InMemoryControlPlaneStore
    auth: object | None = None
    audit_logger: object | None = None
    runtime_client: RuntimeClient | None = None
    policy_client: PolicyClient | None = None
    memory_client: Any | None = None

    def __post_init__(self) -> None:
        self._handlers: Dict[str, Handler] = {}
        self._command_specs: Dict[str, CommandSpec] = {}
        self.shadowed_commands: Dict[str, CommandSpec] = {}
        self.loaded_modules: Dict[str, str] = {}
        self.broken_module_tracker: BrokenModuleTracker = BrokenModuleTracker()
        self._register_builtin_commands()
