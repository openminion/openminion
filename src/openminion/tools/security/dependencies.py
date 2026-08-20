"""External scanner dependencies owned by the security family."""

from openminion.modules.tool.contracts.dependencies import ToolDependencySetupHint
from openminion.modules.tool import binary_dependency

from .config import SEMGREP_EXECUTABLE_ENV, TRIVY_EXECUTABLE_ENV

_SEMGREP_URL = "https://semgrep.dev/docs/getting-started/"
_TRIVY_URL = "https://trivy.dev/latest/getting-started/installation/"

SEMGREP_DEPENDENCY = binary_dependency(
    dependency_id="binary:semgrep",
    executable=lambda context: context.env.get(SEMGREP_EXECUTABLE_ENV, "semgrep"),
    setup_hints=(
        ToolDependencySetupHint(
            platform="darwin",
            label="Install Semgrep with Homebrew",
            official_url=_SEMGREP_URL,
            command=("brew", "install", "semgrep"),
        ),
        ToolDependencySetupHint(
            platform="any",
            label="Follow the official Semgrep installation guide",
            official_url=_SEMGREP_URL,
        ),
    ),
)

TRIVY_DEPENDENCY = binary_dependency(
    dependency_id="binary:trivy",
    executable=lambda context: context.env.get(TRIVY_EXECUTABLE_ENV, "trivy"),
    setup_hints=(
        ToolDependencySetupHint(
            platform="darwin",
            label="Install Trivy with Homebrew",
            official_url=_TRIVY_URL,
            command=("brew", "install", "trivy"),
        ),
        ToolDependencySetupHint(
            platform="any",
            label="Follow the official Trivy installation guide",
            official_url=_TRIVY_URL,
        ),
    ),
)

__all__ = ["SEMGREP_DEPENDENCY", "TRIVY_DEPENDENCY"]
