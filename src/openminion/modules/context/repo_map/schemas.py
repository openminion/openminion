"""Typed RepoMap + RepoSymbol contracts."""

from dataclasses import dataclass, field
from typing import Literal


SymbolKind = Literal["class", "function", "method", "module"]


@dataclass(frozen=True)
class RepoSymbol:
    """One repo-map symbol — class, function, method, or module-level."""

    path: str
    name: str
    kind: SymbolKind
    signature: str = ""
    docstring_first_line: str = ""
    line_number: int = 0
    parent_chain: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class RepoMap:
    """Typed repo-map snapshot — symbols + ranking metadata."""

    root: str
    symbols: tuple[RepoSymbol, ...] = field(default_factory=tuple)
    parser_version: str = "ast-1"


__all__ = ["RepoMap", "RepoSymbol", "SymbolKind"]
