"""Audit stable runtime prompt literals outside the shared prompt owner.

The validator is warn-only by default for the OPSC migration lane. It focuses on
module-level stable prompt fragments and headings, not ordinary user-facing test
inputs or dynamic runtime strings. Use ``--strict`` once the prompt inventory has
complete dispositions and the allowlist is intentionally small.
"""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src" / "openminion"
PROMPT_OWNER = SRC_ROOT / "modules" / "prompting"

_PROSE_NAME_MARKERS = (
    "PROMPT",
    "DIRECTIVE",
    "CONSTRAINT",
    "GUIDANCE",
)
_TEXT_MARKERS = (
    "Reply 'continue'",
    "## Runtime Grounding",
    "## Project Context File",
    "## Third-brain graph context",
    "FRESHNESS_POLICY:",
    "You are the agent described",
)
_INLINE_TEXT_MARKERS = (
    "Tool execution results:",
    "Do not emit any tool call markup",
    "Return a plain-text answer",
    "You MUST call exactly one tool",
    "Return a valid tool call now",
    "Do not repeat the same tool call",
    "Retry the same user task",
)

# Domain-owned prompt builders that are intentionally not migrated in OPSC v1.
_ALLOWED_DOMAIN_OWNED = {
    "modules/brain/bootstrap/freshness_classify.py": "freshness classifier prompt is brain-bootstrap owned",
    "modules/brain/bootstrap/skill/selection.py": "skill-selection prompt is skill-bootstrap owned",
    "modules/brain/loop/adaptive/seeded.py": "remaining seeded-loop guidance is adaptive-loop owned",
    "modules/brain/loop/strategies/coding/prompts.py": "coding strategy planning prompt is strategy-owned",
    "modules/brain/loop/constants.py": "loop finalization retry prompt is loop-policy owned",
    "modules/brain/loop/tools/engine.py": "loop-control guidance is tool-loop owned",
    "modules/brain/loop/tools/phases/eval.py": "tool-loop evaluator prompt is phase-owned",
    "modules/brain/loop/tools/phases/observe.py": "tool-loop observe prompts are phase-owned",
    "modules/brain/loop/tools/phases/refine.py": "tool-loop refine prompt is phase-owned",
    "modules/brain/loop/tools/prompts.py": "adaptive tool-loop retry prompts are tool-loop owned",
    "modules/brain/loop/tools/response_payloads.py": "structured-response guidance is response-payload owned",
    "modules/brain/retry.py": "retry repair guidance is retry-policy owned",
    "modules/memory/runtime/consolidation/merge.py": "memory consolidation prompt is memory-runtime owned",
    "services/agent/execution_prompts.py": "agent execution retry prompts are domain-owned",
    "modules/memory/runtime/extraction/records.py": "memory exact-value guidance is memory-extraction owned",
    "services/lifecycle/prompts.py": "sidecar approval prompts are operator UX, not LLM prompts",
}


@dataclass(frozen=True)
class Finding:
    path: Path
    line: int
    name: str
    preview: str

    def render(self) -> str:
        rel = self.path.relative_to(REPO_ROOT)
        return f"{rel}:{self.line}: {self.name} = {self.preview!r}"


def _is_prompt_owner(path: Path) -> bool:
    try:
        path.relative_to(PROMPT_OWNER)
    except ValueError:
        return False
    return True


def _is_allowed_domain_owned(path: Path) -> bool:
    try:
        rel = path.relative_to(SRC_ROOT).as_posix()
    except ValueError:
        return False
    return rel in _ALLOWED_DOMAIN_OWNED


def _target_names(target: ast.expr) -> list[str]:
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        names: list[str] = []
        for item in target.elts:
            names.extend(_target_names(item))
        return names
    return []


def _constant_text(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            part.value
            for part in node.values
            if isinstance(part, ast.Constant) and isinstance(part.value, str)
        )
    return None


def _looks_like_prompt_prose(text: str) -> bool:
    compact = " ".join(text.split())
    return len(compact) >= 40 and " " in compact


def _looks_stable_prompt(name: str, text: str) -> bool:
    upper = name.upper()
    if any(marker in text for marker in _TEXT_MARKERS):
        return True
    if "HEADER" in upper and text.strip().startswith("## "):
        return True
    return any(
        marker in upper for marker in _PROSE_NAME_MARKERS
    ) and _looks_like_prompt_prose(text)


def _looks_inline_prompt_literal(text: str) -> bool:
    return any(marker in text for marker in _INLINE_TEXT_MARKERS)


def _scan_file(path: Path) -> list[Finding]:
    try:
        tree = ast.parse(path.read_text(), filename=str(path))
    except SyntaxError as exc:
        return [
            Finding(
                path=path, line=exc.lineno or 1, name="<syntax-error>", preview=str(exc)
            )
        ]
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            text = _constant_text(node.value)
            if text is None:
                continue
            if _looks_inline_prompt_literal(text):
                findings.append(
                    Finding(
                        path=path,
                        line=getattr(node, "lineno", 1),
                        name="<inline-prompt>",
                        preview=" ".join(text.split())[:96],
                    )
                )
                continue
            for target in node.targets:
                for name in _target_names(target):
                    if _looks_stable_prompt(name, text):
                        findings.append(
                            Finding(
                                path=path,
                                line=getattr(node, "lineno", 1),
                                name=name,
                                preview=" ".join(text.split())[:96],
                            )
                        )
        elif isinstance(node, ast.AnnAssign):
            text = _constant_text(node.value) if node.value is not None else None
            if text is None:
                continue
            if _looks_inline_prompt_literal(text):
                findings.append(
                    Finding(
                        path=path,
                        line=getattr(node, "lineno", 1),
                        name="<inline-prompt>",
                        preview=" ".join(text.split())[:96],
                    )
                )
                continue
            for name in _target_names(node.target):
                if _looks_stable_prompt(name, text):
                    findings.append(
                        Finding(
                            path=path,
                            line=getattr(node, "lineno", 1),
                            name=name,
                            preview=" ".join(text.split())[:96],
                        )
                    )
        elif isinstance(node, ast.Return):
            text = _constant_text(node.value) if node.value is not None else None
            if text is not None and _looks_inline_prompt_literal(text):
                findings.append(
                    Finding(
                        path=path,
                        line=getattr(node, "lineno", 1),
                        name="<inline-prompt>",
                        preview=" ".join(text.split())[:96],
                    )
                )
        elif isinstance(node, ast.Call):
            for keyword in node.keywords:
                if keyword.arg != "user_message":
                    continue
                text = _constant_text(keyword.value)
                if text is None or not _looks_inline_prompt_literal(text):
                    continue
                findings.append(
                    Finding(
                        path=path,
                        line=getattr(node, "lineno", 1),
                        name="user_message",
                        preview=" ".join(text.split())[:96],
                    )
                )
    return findings


def collect_findings(root: Path = SRC_ROOT) -> list[Finding]:
    findings: list[Finding] = []
    for path in sorted(root.rglob("*.py")):
        if _is_prompt_owner(path) or _is_allowed_domain_owned(path):
            continue
        findings.extend(_scan_file(path))
    return findings


def collect_domain_owner_findings(
    root: Path = SRC_ROOT,
    allowed: dict[str, str] = _ALLOWED_DOMAIN_OWNED,
) -> list[Finding]:
    """Report stale or empty domain-owned prompt exemptions."""

    findings: list[Finding] = []
    for relative_path, rationale in sorted(allowed.items()):
        path = root / relative_path
        if not path.is_file():
            findings.append(
                Finding(
                    path=path,
                    line=1,
                    name="<missing-domain-owner>",
                    preview=rationale,
                )
            )
        elif not _scan_file(path):
            findings.append(
                Finding(
                    path=path,
                    line=1,
                    name="<empty-domain-owner>",
                    preview=rationale,
                )
            )
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strict", action="store_true", help="fail when findings exist"
    )
    parser.add_argument(
        "--max-lines", type=int, default=20, help="maximum findings to print"
    )
    args = parser.parse_args()

    findings = collect_domain_owner_findings() + collect_findings()
    if not findings:
        print(
            "[prompt-literals] clean — stable runtime prompts route through modules/prompting"
        )
        return 0

    print(
        f"[prompt-literals] warn-only — {len(findings)} stable prompt literal candidate(s) "
        "outside modules/prompting"
    )
    for finding in findings[: max(0, args.max_lines)]:
        print(f"  {finding.render()}")
    remaining = len(findings) - max(0, args.max_lines)
    if remaining > 0:
        print(f"  ... {remaining} more")
    return 1 if args.strict else 0


if __name__ == "__main__":
    raise SystemExit(main())
