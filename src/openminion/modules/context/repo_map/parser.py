import ast
from dataclasses import dataclass
from pathlib import Path

from openminion.modules.context.repo_map.constants import (
    RMP_PARSER_VERSION_AST_V1,
)
from openminion.modules.context.repo_map.schemas import (
    RepoMap,
    RepoSymbol,
    SymbolKind,
)


_PYTHON_SUFFIX = ".py"


def _signature(node: ast.AST) -> str:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        args = ", ".join(arg.arg for arg in node.args.args)
        return f"{node.name}({args})"
    if isinstance(node, ast.ClassDef):
        bases = ", ".join(_safe_repr(b) for b in node.bases)
        return f"class {node.name}" + (f"({bases})" if bases else "")
    return ""


def _safe_repr(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return getattr(node, "id", "")


def _docstring_first_line(node: ast.AST) -> str:
    doc = (
        ast.get_docstring(node)
        if isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)
        )
        else None
    )
    if not doc:
        return ""
    return doc.splitlines()[0].strip()[:140]


def _kind_for(node: ast.AST, parent_chain: tuple[str, ...]) -> SymbolKind:
    if isinstance(node, ast.ClassDef):
        return "class"
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        if parent_chain and parent_chain[-1].startswith("class:"):
            return "method"
        return "function"
    return "module"


def _walk_module(path: str, tree: ast.Module) -> list[RepoSymbol]:
    symbols: list[RepoSymbol] = []
    module_doc = _docstring_first_line(tree)
    if module_doc:
        symbols.append(
            RepoSymbol(
                path=path,
                name="__module__",
                kind="module",
                signature="",
                docstring_first_line=module_doc,
                line_number=1,
            )
        )

    def visit(node: ast.AST, parent_chain: tuple[str, ...]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                kind = _kind_for(child, parent_chain)
                symbols.append(
                    RepoSymbol(
                        path=path,
                        name=child.name,
                        kind=kind,
                        signature=_signature(child),
                        docstring_first_line=_docstring_first_line(child),
                        line_number=child.lineno,
                        parent_chain=parent_chain,
                    )
                )
                next_chain = parent_chain + (
                    f"class:{child.name}"
                    if isinstance(child, ast.ClassDef)
                    else f"func:{child.name}",
                )
                visit(child, next_chain)

    visit(tree, parent_chain=())
    return symbols


@dataclass
class AstRepoMapBuilder:
    parser_version: str = RMP_PARSER_VERSION_AST_V1

    def parse(self, root: Path) -> RepoMap:
        root = Path(root)
        symbols: list[RepoSymbol] = []
        for file in sorted(root.rglob(f"*{_PYTHON_SUFFIX}")):
            try:
                source = file.read_text(encoding="utf-8")
                tree = ast.parse(source, filename=str(file))
            except (OSError, SyntaxError):
                continue
            relpath = (
                str(file.relative_to(root)) if file.is_relative_to(root) else str(file)
            )
            symbols.extend(_walk_module(relpath, tree))
        return RepoMap(
            root=str(root),
            symbols=tuple(symbols),
            parser_version=self.parser_version,
        )


def build_repo_map(root: Path | str) -> RepoMap:
    return AstRepoMapBuilder().parse(Path(root))


__all__ = ["AstRepoMapBuilder", "build_repo_map"]
