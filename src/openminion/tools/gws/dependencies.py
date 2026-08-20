"""External CLI dependency owned by the Google Workspace family."""

from collections.abc import Mapping

from openminion.modules.tool.contracts.dependencies import (
    ToolDependencyProbeContext,
    ToolDependencySetupHint,
)
from openminion.modules.tool import binary_dependency
from openminion.tools.gws.constants import GWS_DEFAULT_EXECUTABLE
from openminion.tools.gws.schemas import GwsToolConfig


def _configured_executable(context: ToolDependencyProbeContext) -> str:
    raw = context.policy.get("runtime_tools", {})
    gws = raw.get("gws", {}) if isinstance(raw, Mapping) else {}
    return GwsToolConfig.model_validate(gws).gws_path


GWS_DEPENDENCY = binary_dependency(
    dependency_id="binary:gws",
    executable=_configured_executable,
    setup_hints=(
        ToolDependencySetupHint(
            platform="any",
            label="Install the official Google Workspace CLI",
            official_url="https://github.com/googleworkspace/cli",
            command=("npm", "install", "--global", "@googleworkspace/cli"),
            note=f"The executable defaults to {GWS_DEFAULT_EXECUTABLE!r}.",
        ),
    ),
)

__all__ = ["GWS_DEPENDENCY"]
