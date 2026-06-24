from collections import defaultdict
from typing import Sequence

from openminion.modules.context.repo_map.constants import (
    RMP_CHARS_PER_TOKEN_HEURISTIC,
    RMP_DEFAULT_TOKEN_BUDGET,
)
from openminion.modules.context.repo_map.schemas import RepoSymbol


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // RMP_CHARS_PER_TOKEN_HEURISTIC)


def serialize_repo_map(
    ranked_symbols: Sequence[RepoSymbol],
    *,
    token_budget: int = RMP_DEFAULT_TOKEN_BUDGET,
    header: str = "[repo map]",
) -> str:
    by_path: dict[str, list[RepoSymbol]] = defaultdict(list)
    for sym in ranked_symbols:
        by_path[sym.path].append(sym)

    lines: list[str] = [header]
    running_tokens = _estimate_tokens(header)

    for path, syms in by_path.items():
        path_line = f"{path}:"
        if running_tokens + _estimate_tokens(path_line) > token_budget:
            lines.append("  [truncated]")
            break
        lines.append(path_line)
        running_tokens += _estimate_tokens(path_line)
        for sym in syms:
            sig = sym.signature or sym.name
            doc = f"  # {sym.docstring_first_line}" if sym.docstring_first_line else ""
            indent = "  " * (1 + len(sym.parent_chain))
            line = f"{indent}{sig}{doc}".rstrip()
            if not line.strip():
                continue
            est = _estimate_tokens(line)
            if running_tokens + est > token_budget:
                lines.append("  [truncated]")
                return "\n".join(lines)
            lines.append(line)
            running_tokens += est
    return "\n".join(lines)


__all__ = ["serialize_repo_map"]
