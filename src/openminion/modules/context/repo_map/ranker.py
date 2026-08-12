"""Symbol ranker — deterministic top-K with simple ranking heuristic."""

from collections import defaultdict
from typing import Iterable, Sequence

from openminion.modules.context.repo_map.schemas import RepoMap, RepoSymbol


_KIND_PRIORITY = {
    "module": 0,
    "class": 3,
    "function": 2,
    "method": 1,
}


def _name_reference_count(symbols: Sequence[RepoSymbol]) -> dict[str, int]:
    """Count other symbol names referenced by each signature."""

    counts: dict[str, int] = defaultdict(int)
    name_index = {sym.name for sym in symbols}
    for sym in symbols:
        for name in name_index:
            if name and name != sym.name and name in sym.signature:
                counts[name] += 1
    return dict(counts)


def rank_symbols(
    repo_map: RepoMap,
    *,
    top_k: int | None = None,
    pinned_names: Iterable[str] | None = None,
) -> list[RepoSymbol]:
    """Return symbols sorted by ranking heuristic, optionally top-K."""

    pinned = set(pinned_names or ())
    refs = _name_reference_count(repo_map.symbols)

    def score(sym: RepoSymbol) -> tuple[int, int, int, str]:
        return (
            0 if sym.name in pinned else 1,
            -_KIND_PRIORITY.get(sym.kind, 0),
            -refs.get(sym.name, 0),
            sym.path,
        )

    ordered = sorted(repo_map.symbols, key=score)
    if top_k is not None:
        return ordered[: max(0, top_k)]
    return ordered


__all__ = ["rank_symbols"]
