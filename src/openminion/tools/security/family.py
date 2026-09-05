"""Declarative security scanner family and exposure profile."""

from openminion.modules.tool.exposure import ToolExposureProfile
from openminion.modules.tool.framework import ToolDecl, ToolFamilySpec

from .interfaces import (
    ALL_SECURITY_TOOLS,
    TOOL_SECURITY_PUBLISH_REPORT,
    TOOL_SECURITY_SCAN_ARTIFACT,
    TOOL_SECURITY_SCAN_CODE,
    TOOL_SECURITY_SCAN_DEPENDENCIES,
    TOOL_SECURITY_SCAN_SECRETS,
)
from .dependencies import SEMGREP_DEPENDENCY, TRIVY_DEPENDENCY
from .plugin import (
    _h_scan_artifact,
    _h_scan_code,
    _h_scan_dependencies,
    _h_scan_secrets,
    _h_publish_report,
)
from .schemas import LocalScanArgs, SecurityAuditPublishArgs

SECURITY_FAMILY = ToolFamilySpec(
    module_id="security",
    min_scope_default="READ_ONLY",
    common_tags=("plugin", "security", "read_only"),
    common_capabilities=("read_only", "security", "evidence"),
    exposure_profiles=(
        ToolExposureProfile(
            profile_id="security_readonly",
            title="Security scanning",
            summary="Bounded local source, dependency, IaC, and secret scans.",
            tool_names=frozenset(ALL_SECURITY_TOOLS),
            evidence_expectations=(
                "return scanner identity, version, bounded findings, and evidence refs",
            ),
            stop_rules=("stop before remediation, network update, or remote scanning",),
            activation_hint="Activate for an approved local read-only security audit.",
        ),
    ),
    tools=(
        ToolDecl(
            TOOL_SECURITY_SCAN_CODE,
            LocalScanArgs,
            _h_scan_code,
            "Scan approved local source with configured Semgrep rules.",
            idempotent=True,
            dependencies=(SEMGREP_DEPENDENCY,),
        ),
        ToolDecl(
            TOOL_SECURITY_SCAN_DEPENDENCIES,
            LocalScanArgs,
            _h_scan_dependencies,
            "Scan approved local dependency manifests with Trivy.",
            idempotent=True,
            dependencies=(TRIVY_DEPENDENCY,),
        ),
        ToolDecl(
            TOOL_SECURITY_SCAN_ARTIFACT,
            LocalScanArgs,
            _h_scan_artifact,
            "Scan approved local filesystem or IaC artifacts with Trivy.",
            idempotent=True,
            dependencies=(TRIVY_DEPENDENCY,),
        ),
        ToolDecl(
            TOOL_SECURITY_SCAN_SECRETS,
            LocalScanArgs,
            _h_scan_secrets,
            "Scan approved local files for secret findings with Trivy.",
            idempotent=True,
            dependencies=(TRIVY_DEPENDENCY,),
        ),
        ToolDecl(
            TOOL_SECURITY_PUBLISH_REPORT,
            SecurityAuditPublishArgs,
            _h_publish_report,
            "Publish one canonical unreviewed candidate security-audit report.",
        ),
    ),
)

__all__ = ["SECURITY_FAMILY"]
