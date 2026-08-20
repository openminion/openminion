from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Literal

from openminion.base.config.env import EnvironmentConfig
from openminion.modules.tool.errors import ToolRuntimeError

DependencyState = Literal["ready", "missing", "unhealthy"]
DependencyProbe = Callable[["ToolDependencyProbeContext"], "ToolDependencyStatus"]

_DEPENDENCY_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*:[a-z0-9][a-z0-9._-]*$")
_SETUP_PLATFORMS = frozenset(
    {
        "darwin",
        "linux-debian",
        "linux-rhel",
        "linux-alpine",
        "linux-arch",
        "windows",
        "any",
    }
)


def _validate_dependency_id(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _DEPENDENCY_ID_RE.fullmatch(normalized):
        raise ToolRuntimeError(
            "INVALID_ARGUMENT", f"invalid tool dependency id: {value!r}"
        )
    return normalized


@dataclass(frozen=True)
class ToolDependencyProbeContext:
    workspace: Path
    env: EnvironmentConfig
    policy: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "workspace", Path(self.workspace).resolve(strict=False)
        )


@dataclass(frozen=True)
class ToolDependencySetupHint:
    platform: str
    label: str
    official_url: str
    command: tuple[str, ...] = ()
    note: str = ""

    def __post_init__(self) -> None:
        platform = str(self.platform or "").strip().lower()
        label = str(self.label or "").strip()
        url = str(self.official_url or "").strip()
        command = tuple(str(item or "").strip() for item in self.command)
        if platform not in _SETUP_PLATFORMS:
            raise ToolRuntimeError(
                "INVALID_ARGUMENT",
                f"unsupported setup platform: {self.platform!r}",
            )
        if not label:
            raise ToolRuntimeError("INVALID_ARGUMENT", "setup hint label is required")
        if not url.startswith("https://"):
            raise ToolRuntimeError(
                "INVALID_ARGUMENT", "setup hint official_url must use https"
            )
        if any(not item for item in command):
            raise ToolRuntimeError(
                "INVALID_ARGUMENT", "setup hint command entries must not be empty"
            )
        object.__setattr__(self, "platform", platform)
        object.__setattr__(self, "label", label)
        object.__setattr__(self, "official_url", url)
        object.__setattr__(self, "command", command)
        object.__setattr__(self, "note", str(self.note or "").strip())

    def as_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "label": self.label,
            "command": list(self.command),
            "official_url": self.official_url,
            "note": self.note,
        }


@dataclass(frozen=True)
class ToolDependencyStatus:
    dependency_id: str
    state: DependencyState
    resolved_path: str = ""
    version: str = ""
    reason_code: str = ""
    message: str = ""
    setup_hints: tuple[ToolDependencySetupHint, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "dependency_id", _validate_dependency_id(self.dependency_id)
        )
        if self.state not in {"ready", "missing", "unhealthy"}:
            raise ToolRuntimeError(
                "INVALID_ARGUMENT",
                f"unsupported dependency state: {self.state!r}",
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "dependency_id": self.dependency_id,
            "state": self.state,
            "resolved_path": self.resolved_path,
            "version": self.version,
            "reason_code": self.reason_code,
            "message": self.message,
            "setup_hints": [hint.as_dict() for hint in self.setup_hints],
        }


@dataclass(frozen=True)
class ToolDependencyDecl:
    dependency_id: str
    probe: DependencyProbe
    setup_hints: tuple[ToolDependencySetupHint, ...] = ()
    preflight: DependencyProbe | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "dependency_id", _validate_dependency_id(self.dependency_id)
        )
        if not callable(self.probe):
            raise ToolRuntimeError(
                "INVALID_ARGUMENT", "dependency probe must be callable"
            )
        if self.preflight is not None and not callable(self.preflight):
            raise ToolRuntimeError(
                "INVALID_ARGUMENT", "dependency preflight must be callable"
            )
        if any(
            not isinstance(hint, ToolDependencySetupHint) for hint in self.setup_hints
        ):
            raise ToolRuntimeError(
                "INVALID_ARGUMENT",
                "setup_hints must contain ToolDependencySetupHint values",
            )


__all__ = [
    "DependencyProbe",
    "DependencyState",
    "ToolDependencyDecl",
    "ToolDependencyProbeContext",
    "ToolDependencySetupHint",
    "ToolDependencyStatus",
]
