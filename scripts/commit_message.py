#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_NAME = "openminion"
REQUIRE_SCOPE = True
ALLOWED_TYPES = ("feat", "fix", "docs", "refactor", "test", "chore", "style", "build")
SCOPE_EXAMPLES = ("agent", "api", "cli", "e2e", "gateway", "runtime", "telemetry", "tool", "tools", "tui")
FORBIDDEN_SUMMARIES = {"update"}
COMMIT_PATTERN = re.compile(
    r"^(?P<type>feat|fix|docs|refactor|test|chore|style|build)"
    r"(?:\((?P<scope>[a-z0-9][a-z0-9-]*)\))?: (?P<summary>.+)$"
)


def _read_subject(path: Path) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped
    return ""


def _is_special_case(subject: str) -> bool:
    lowered = subject.lower()
    return (
        subject.startswith("Merge ")
        or subject.startswith('Revert "')
        or lowered == "initial commit"
    )


def _validate_standard_subject(subject: str) -> str | None:
    match = COMMIT_PATTERN.fullmatch(subject)
    if match is None:
        return None
    summary = match.group("summary").strip()
    scope = match.group("scope")
    if REQUIRE_SCOPE and not scope:
        return f"{REPO_NAME} commits must include a scope."
    if summary.lower() in FORBIDDEN_SUMMARIES:
        return "Commit summary is too vague."
    return ""


def _validate_subject(subject: str) -> str | None:
    if not subject:
        return "Commit message is empty."
    if _is_special_case(subject):
        return ""
    for prefix in ("fixup! ", "squash! "):
        if subject.startswith(prefix):
            return _validate_subject(subject[len(prefix) :].strip())
    result = _validate_standard_subject(subject)
    if result == "":
        return ""
    if result is not None:
        return result
    return "Commit message must match the documented workspace format."


def _usage_message() -> str:
    allowed_types = ", ".join(ALLOWED_TYPES)
    scope_examples = ", ".join(SCOPE_EXAMPLES)
    return (
        "Allowed format:\n"
        "  <type>(<scope>): <summary>\n\n"
        f"Allowed types: {allowed_types}\n"
        f"Scope examples: {scope_examples}\n"
        "Special cases allowed: Merge..., Revert \"...\", Initial commit, fixup!, squash!\n\n"
        "Examples:\n"
        "  docs(cli): explain hook installation\n"
        "  feat(runtime): add turn route timing metadata"
    )


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: validate_commit_message.py <commit-message-file>", file=sys.stderr)
        return 2
    subject = _read_subject(Path(argv[1]))
    error = _validate_subject(subject)
    if error == "":
        return 0
    print(error, file=sys.stderr)
    print("", file=sys.stderr)
    print(_usage_message(), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
