"""Concrete security scanner adapters."""

from .semgrep import scan_code
from .trivy import scan_artifact, scan_dependencies, scan_secrets

__all__ = ["scan_artifact", "scan_code", "scan_dependencies", "scan_secrets"]
