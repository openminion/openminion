"""Runtime sidecar consent, process adapters, and lifecycle management."""

from .manager import (
    PinchTabSidecarAdapter,
    SidecarAdapter,
    SidecarConsent,
    SidecarConsentStore,
    SidecarExecutor,
    SidecarManager,
    SidecarSpec,
    SubprocessExecutor,
    ToolExecExecutor,
    default_sidecar_manager,
    ensure_pinchtab_autostart,
    ensure_sidecar_autostart,
    ensure_sidecars_autostart,
)
from .prompts import (
    PINCHTAB_AUTOSTART_PROMPT,
    SIDECAR_POLICY_PROMPT_TEMPLATE,
    build_sidecar_policy_prompt,
)

__all__ = [
    "PINCHTAB_AUTOSTART_PROMPT",
    "SIDECAR_POLICY_PROMPT_TEMPLATE",
    "PinchTabSidecarAdapter",
    "SidecarAdapter",
    "SidecarConsent",
    "SidecarConsentStore",
    "SidecarExecutor",
    "SidecarManager",
    "SidecarSpec",
    "SubprocessExecutor",
    "ToolExecExecutor",
    "build_sidecar_policy_prompt",
    "default_sidecar_manager",
    "ensure_pinchtab_autostart",
    "ensure_sidecar_autostart",
    "ensure_sidecars_autostart",
]
