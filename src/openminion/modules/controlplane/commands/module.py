from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

from openminion.base.version import OPENMINION_VERSION
from openminion.modules.controlplane.contracts.models import (
    CommandResult,
    ParsedCommand,
    ResolvedContext,
)


class AuthRequirement(Enum):
    """Authentication requirement levels for commands."""

    NONE = "none"
    USER = "user"
    ADMIN = "admin"
    CUSTOM = "custom"


@dataclass
class CommandSchema:
    """Schema definition for command parameters."""

    name: str
    description: str
    usage: str
    args_schema: list[dict[str, Any]] | None = None
    required_args: list[str] | None = None
    optional_args: list[str] | None = None


@dataclass
class CommandSpec:
    """Specification for a command including schema, auth, and handler."""

    name: str
    schema: CommandSchema
    handler: Callable[[ParsedCommand, ResolvedContext], CommandResult]
    auth_requirement: AuthRequirement
    module_name: str
    version: str = OPENMINION_VERSION
    category: str | None = None
    tags: list[str] | None = None
    deprecated: bool = False
    deprecation_reason: str | None = None


@dataclass
class CommandContext:
    """Execution context for command handlers."""

    resolved_context: ResolvedContext
    command_spec: CommandSpec
    extra_data: dict[str, Any] | None = None


class CommandModule(Protocol):
    """Protocol for command modules discovered via entry points."""

    @property
    def name(self) -> str:
        """Module name (used in entry point discovery)."""
        ...

    @property
    def version(self) -> str:
        """Module version."""
        ...

    @property
    def description(self) -> str:
        """Module description."""
        ...

    def get_commands(self) -> list[CommandSpec]:
        """Return the command specifications provided by this module."""
        ...


Handler = Callable[[ParsedCommand, ResolvedContext], CommandResult]
CommandModuleFactory = Callable[[], CommandModule]
