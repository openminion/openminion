import re
from typing import TypedDict

MAX_MARKDOWN_CHARS = 100_000  # public: used by tests and callers
_SIZE_WARN_BYTES = MAX_MARKDOWN_CHARS  # internal alias used in scan()


class SkillInspectIssue(TypedDict):
    code: str
    message: str
    risk: str


class _Rule(TypedDict):
    code: str
    risk: str
    message: str
    pattern: re.Pattern[str]


_RULES: tuple[_Rule, ...] = (
    # --- Prompt injection ---
    {
        "code": "PI-001",
        "risk": "critical",
        "message": "Prompt-injection instruction detected (IGNORE/OVERRIDE INSTRUCTIONS variant).",
        "pattern": re.compile(
            r"ignore\s+(all\s+)?previous\s+instructions"
            r"|override\s+(all\s+)?(system|developer|prompt)\s+instructions"
            r"|disregard\s+(all\s+)?previous\s+instructions",
            re.IGNORECASE,
        ),
    },
    {
        "code": "PI-002",
        "risk": "critical",
        "message": "XML system-tag injection detected.",
        "pattern": re.compile(
            r"<\s*/?\s*(system|admin|prompt|context|instruction)\s*>",
            re.IGNORECASE,
        ),
    },
    {
        "code": "PI-003",
        "risk": "high",
        "message": "Safety-bypass or jailbreak instruction detected.",
        "pattern": re.compile(
            r"override\s+safety"
            r"|bypass\s+restrict"
            r"|disable\s+filter"
            r"|jailbreak"
            r"|developer\s+mode"
            r"|act\s+as\s+(?:an?\s+)?(unrestricted|unfiltered|evil|malicious)",
            re.IGNORECASE,
        ),
    },
    # --- Dangerous shell execution ---
    {
        "code": "EXEC-001",
        "risk": "critical",
        "message": "Destructive shell command pattern detected.",
        "pattern": re.compile(
            r"rm\s+-rf\s+/"
            r"|mkfs\.\w+"
            r"|dd\s+if=.*of=/dev/",
            re.IGNORECASE,
        ),
    },
    {
        "code": "EXEC-002",
        "risk": "critical",
        "message": "Remote-script execution pattern detected (curl/wget piped to shell).",
        "pattern": re.compile(
            r"curl\s+[^\n|]+?\|\s*(?:sh|bash)"
            r"|wget\s+[^\n|]+?\|\s*(?:sh|bash)",
            re.IGNORECASE,
        ),
    },
    # --- Exfiltration ---
    {
        "code": "EXFIL-001",
        "risk": "high",
        "message": "Credential or private-key exfiltration pattern detected.",
        "pattern": re.compile(
            r"cat\s+~?/?\.ssh/"
            r"|cat\s+/etc/passwd"
            r"|printenv\s+\w*(?:KEY|SECRET|TOKEN|PASS)"
            r"|export\s+\w*(?:SECRET|KEY|TOKEN)"
            r"|BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY",
            re.IGNORECASE,
        ),
    },
    {
        "code": "EXFIL-002",
        "risk": "medium",
        "message": "External upload or exfiltration template detected.",
        "pattern": re.compile(
            r"send\s+to\s+https?://"
            r"|upload\s+(?:all|env|secrets?)\s+to\s+https?://"
            r"|exfiltrat(?:e|ion)",
            re.IGNORECASE,
        ),
    },
    # --- Scope abuse ---
    {
        "code": "SCOPE-001",
        "risk": "medium",
        "message": "Skill declares a dangerous required scope.",
        "pattern": re.compile(
            r"scopes_required\s*:\s*(?:POWER_USER|DANGEROUS|ADMIN)",
            re.IGNORECASE,
        ),
    },
)

_RISK_ORDER: dict[str, int] = {
    "low": 0,
    "medium": 1,
    "high": 2,
    "critical": 3,
}


def _max_risk(current: str, incoming: str) -> str:
    if _RISK_ORDER.get(incoming, 0) > _RISK_ORDER.get(current, 0):
        return incoming
    return current


def scan(markdown: str) -> tuple[str, list[SkillInspectIssue]]:
    text = str(markdown or "")
    issues: list[SkillInspectIssue] = []
    risk_level = "low"

    # warn on large payloads (> 100 000 chars, medium risk)
    if len(text) > _SIZE_WARN_BYTES:
        size_k = len(text) // 1000
        issues.append(
            {
                "code": "SIZE-001",
                "message": f"Skill markdown is {size_k}K chars (limit {_SIZE_WARN_BYTES // 1000}K).",
                "risk": "medium",
            }
        )
        risk_level = _max_risk(risk_level, "medium")

    for rule in _RULES:
        if rule["pattern"].search(text):
            issues.append(
                {
                    "code": rule["code"],
                    "message": rule["message"],
                    "risk": rule["risk"],
                }
            )
            risk_level = _max_risk(risk_level, rule["risk"])

    return risk_level, issues


__all__ = ["MAX_MARKDOWN_CHARS", "SkillInspectIssue", "scan"]
