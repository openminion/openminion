"""Repo-map section builder for pinned-prefix composition.

Returns the serialized repo-map as a `[REPO MAP]` section the existing
`PinnedPrefixBuilder` consumer can splice into the pinned prefix at
build time (operator opt-in).
"""

from pathlib import Path

from openminion.modules.context.repo_map.cache import RepoMapCache
from openminion.modules.context.repo_map.config import RepoMapConfig
from openminion.modules.context.repo_map.parser import AstRepoMapBuilder
from openminion.modules.context.repo_map.ranker import rank_symbols
from openminion.modules.context.repo_map.serializer import serialize_repo_map


def build_repo_map_section(
    root: Path | str,
    *,
    config: RepoMapConfig,
    profile: str = "",
    cache: RepoMapCache | None = None,
    builder: AstRepoMapBuilder | None = None,
    top_k: int | None = None,
) -> str:
    """Return the `[REPO MAP] ...` section, or `""` when disabled / gated."""

    if not config.enabled:
        return ""
    if config.profile_gate and profile and profile not in config.profile_gate:
        return ""

    active_builder = builder or AstRepoMapBuilder()
    repo_map = (
        cache.refresh(Path(root), builder=active_builder)
        if cache is not None
        else active_builder.parse(Path(root))
    )
    ranked = rank_symbols(repo_map, top_k=top_k)
    body = serialize_repo_map(ranked, token_budget=config.token_budget)
    if not body:
        return ""
    return "[REPO MAP]\n" + body


__all__ = ["build_repo_map_section"]
