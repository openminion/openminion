"""Typed RepoMap + RepoSymbol contracts."""

from dataclasses import dataclass
from typing import Literal


SymbolKind = Literal["class", "function", "method", "module"]


@dataclass(frozen=True)
class RepoSymbol:
    path: str
    name: str
    kind: SymbolKind
    signature: str = ""
    docstring_first_line: str = ""
    line_number: int = 0
    parent_chain: tuple[str, ...] = ()


@dataclass(frozen=True)
class RepoMap:
    root: str
    symbols: tuple[RepoSymbol, ...] = ()
    parser_version: str = "ast-1"


__all__ = ["RepoMap", "RepoSymbol", "SymbolKind"]
