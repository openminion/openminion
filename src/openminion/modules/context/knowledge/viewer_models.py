"""DTOs for OpenMinion's GraphFakos viewer surface."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class GraphViewerRequest:
    brain: str = "third"
    provider: str = ""
    current: bool = False
    agent_id: str = ""
    session_id: str = ""
    screen: str = "explore"
    query: str = ""
    focus_node_id: str = ""
    source_node_id: str = ""
    target_node_id: str = ""
    max_depth: int = 1
    limit: int = 100
    render_limit: int = 240
    render_engine: str = "svg"
    theme: str = "default"
    layout: str = "force"
    host: str = "127.0.0.1"
    port: int = 8767
    open_browser: bool = True
    dry_run: bool = False
    html_out: str = ""
    memory_db: str = ""


@dataclass(frozen=True)
class GraphViewerLaunchResult:
    provider: str
    layer: str
    graph_role: str
    mode: str
    url: str = ""
    html_path: str = ""
    opened: bool = False
    diagnostics: Mapping[str, object] | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "layer": self.layer,
            "graph_role": self.graph_role,
            "mode": self.mode,
            "url": self.url,
            "html_path": self.html_path,
            "opened": self.opened,
            "diagnostics": dict(self.diagnostics or {}),
        }


@dataclass(frozen=True)
class GraphViewerProviderStatus:
    provider: str
    layer: str
    adapter: str
    active: bool
    enabled: bool
    visual_ready: bool
    reason: str = ""
    next_command: str = ""
    tags: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    details: Mapping[str, object] | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "layer": self.layer,
            "adapter": self.adapter,
            "active": self.active,
            "enabled": self.enabled,
            "visual_ready": self.visual_ready,
            "reason": self.reason,
            "next_command": self.next_command,
            "tags": list(self.tags),
            "capabilities": list(self.capabilities),
            "details": dict(self.details or {}),
        }


@dataclass(frozen=True)
class GraphViewerStatusReport:
    graphfakos_installed: bool
    graphfakos_version: str
    second_brain: GraphViewerProviderStatus
    third_brain: tuple[GraphViewerProviderStatus, ...] = ()
    next_commands: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return bool(
            self.graphfakos_installed
            and (
                self.second_brain.visual_ready
                or any(provider.visual_ready for provider in self.third_brain)
            )
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "graphfakos": {
                "installed": self.graphfakos_installed,
                "version": self.graphfakos_version,
            },
            "second_brain": self.second_brain.to_dict(),
            "third_brain": [provider.to_dict() for provider in self.third_brain],
            "next_commands": list(self.next_commands),
        }


__all__ = [
    "GraphViewerLaunchResult",
    "GraphViewerProviderStatus",
    "GraphViewerRequest",
    "GraphViewerStatusReport",
]
