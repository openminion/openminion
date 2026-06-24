"""Compatibility and hostile-fixture matrix for MCP release evidence."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class MCPCompatibilityCase:
    family: str
    representative_servers: tuple[str, ...]
    required_primitives: tuple[str, ...]
    ci_safe_fixture: str
    optional_real_smoke: str = ""


@dataclass(frozen=True)
class MCPSecurityFuzzCase:
    case_id: str
    target: str
    threat: str
    expected_result: str
    fixture_hint: str
    tags: tuple[str, ...] = field(default_factory=tuple)


_COMPATIBILITY_MATRIX: tuple[MCPCompatibilityCase, ...] = (
    MCPCompatibilityCase(
        family="filesystem",
        representative_servers=("@modelcontextprotocol/server-filesystem",),
        required_primitives=("tools/list", "tools/call", "resources/list"),
        ci_safe_fixture="stdio fixture with read-only resource/tool wrappers",
        optional_real_smoke="Run against a temp-dir scoped filesystem server.",
    ),
    MCPCompatibilityCase(
        family="git/github",
        representative_servers=(
            "@modelcontextprotocol/server-git",
            "github-mcp-server",
        ),
        required_primitives=("tools/list", "tools/call", "authorization"),
        ci_safe_fixture="streamable HTTP auth-challenge fixture",
        optional_real_smoke="Run GitHub smoke only with token refs configured.",
    ),
    MCPCompatibilityCase(
        family="browser",
        representative_servers=("playwright-mcp",),
        required_primitives=("tools/list", "tools/call", "approval"),
        ci_safe_fixture="tool-risk approval fixture",
        optional_real_smoke="Run browser server smoke with sandbox approval enabled.",
    ),
    MCPCompatibilityCase(
        family="database",
        representative_servers=("postgres-mcp", "sqlite-mcp"),
        required_primitives=("tools/list", "tools/call", "approval", "outputSchema"),
        ci_safe_fixture="structured-content/output-schema fixture",
        optional_real_smoke="Run against a disposable local database only.",
    ),
    MCPCompatibilityCase(
        family="docs/search",
        representative_servers=("fetch-mcp", "search-mcp"),
        required_primitives=("tools/list", "tools/call", "prompts/list"),
        ci_safe_fixture="prompt/resource/template fixture",
        optional_real_smoke="Run with network-enabled operator approval.",
    ),
    MCPCompatibilityCase(
        family="everything",
        representative_servers=("@modelcontextprotocol/server-everything",),
        required_primitives=(
            "tools/list",
            "prompts/list",
            "resources/list",
            "resources/templates/list",
            "completion/complete",
            "logging/setLevel",
        ),
        ci_safe_fixture="local breadth fixture set",
        optional_real_smoke="Optional npm-installed reference server smoke.",
    ),
)

_SECURITY_FUZZ_CASES: tuple[MCPSecurityFuzzCase, ...] = (
    MCPSecurityFuzzCase(
        case_id="mcp-fuzz-malformed-frame",
        target="transport",
        threat="Malformed NDJSON/LSP/SSE frame should not crash the runtime.",
        expected_result="typed protocol error with reason_code",
        fixture_hint="tests/mcp/test_http_sse_transport.py malformed SSE fixture",
        tags=("frame", "protocol"),
    ),
    MCPSecurityFuzzCase(
        case_id="mcp-fuzz-bad-schema",
        target="schema",
        threat="Unsupported or invalid input schema should be rejected/skipped.",
        expected_result="schema validation failure without tool registration",
        fixture_hint="tests/mcp/test_schema_subset.py bad-schema fixture",
        tags=("schema", "registration"),
    ),
    MCPSecurityFuzzCase(
        case_id="mcp-fuzz-auth-challenge",
        target="auth",
        threat="401/403 challenge should not leak tokens or retry forever.",
        expected_result="redacted auth error or one bounded refresh retry",
        fixture_hint="tests/mcp/test_http_transport_breadth.py auth fixture",
        tags=("auth", "redaction"),
    ),
    MCPSecurityFuzzCase(
        case_id="mcp-fuzz-hostile-content",
        target="tool-result",
        threat="Hostile tool text/UI resource must stay structured and explicit.",
        expected_result="no hidden context injection; visible text fallback",
        fixture_hint="tests/mcp/test_mcp_tui_ux.py ui:// fallback fixture",
        tags=("content", "ui"),
    ),
)


def default_mcp_compatibility_matrix() -> tuple[MCPCompatibilityCase, ...]:
    return _COMPATIBILITY_MATRIX


def default_mcp_security_fuzz_cases() -> tuple[MCPSecurityFuzzCase, ...]:
    return _SECURITY_FUZZ_CASES


def validate_mcp_compatibility_matrix() -> list[str]:
    """Return structural issues in the release compatibility matrix."""

    issues: list[str] = []
    families = {case.family for case in _COMPATIBILITY_MATRIX}
    required_families = {
        "filesystem",
        "git/github",
        "browser",
        "database",
        "docs/search",
        "everything",
    }
    missing = sorted(required_families - families)
    if missing:
        issues.append("missing_families:" + ",".join(missing))
    for case in _COMPATIBILITY_MATRIX:
        if not case.representative_servers:
            issues.append(f"{case.family}:missing_representative_servers")
        if not case.required_primitives:
            issues.append(f"{case.family}:missing_required_primitives")
        if not case.ci_safe_fixture:
            issues.append(f"{case.family}:missing_ci_safe_fixture")
    fuzz_ids = {case.case_id for case in _SECURITY_FUZZ_CASES}
    for required_id in (
        "mcp-fuzz-malformed-frame",
        "mcp-fuzz-bad-schema",
        "mcp-fuzz-auth-challenge",
        "mcp-fuzz-hostile-content",
    ):
        if required_id not in fuzz_ids:
            issues.append(f"missing_fuzz_case:{required_id}")
    return issues


__all__ = [
    "MCPCompatibilityCase",
    "MCPSecurityFuzzCase",
    "default_mcp_compatibility_matrix",
    "default_mcp_security_fuzz_cases",
    "validate_mcp_compatibility_matrix",
]
