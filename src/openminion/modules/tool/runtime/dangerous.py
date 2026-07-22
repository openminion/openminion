import re
from dataclasses import dataclass
from collections.abc import Sequence
from pathlib import Path


@dataclass(frozen=True)
class DangerousMatch:
    dangerous: bool
    pattern_id: str = ""
    reason: str = ""


_DANGEROUS_PATTERNS = [
    ("rm_rf", re.compile(r"\brm\b.*\s-rf\b", re.IGNORECASE)),
    ("shutdown", re.compile(r"\bshutdown\b", re.IGNORECASE)),
    ("reboot", re.compile(r"\breboot\b|\bhalt\b|\bpoweroff\b", re.IGNORECASE)),
    ("curl_pipe_sh", re.compile(r"\b(curl|wget)\b.*\|\s*sh\b", re.IGNORECASE)),
    ("mkfs", re.compile(r"\bmkfs\b", re.IGNORECASE)),
    ("dd", re.compile(r"\bdd\b", re.IGNORECASE)),
    ("diskutil_erase", re.compile(r"\bdiskutil\b.*\berase\b", re.IGNORECASE)),
    ("format", re.compile(r"\bformat\b|\bchkdsk\b", re.IGNORECASE)),
]


def detect_dangerous_command(
    argv: Sequence[str], cwd: str | Path | None = None
) -> DangerousMatch:
    if not argv:
        return DangerousMatch(dangerous=False)
    text = " ".join(str(x) for x in argv)
    for pattern_id, regex in _DANGEROUS_PATTERNS:
        if regex.search(text):
            return DangerousMatch(
                dangerous=True,
                pattern_id=pattern_id,
                reason=f"matched pattern {pattern_id}",
            )
    return DangerousMatch(dangerous=False)
