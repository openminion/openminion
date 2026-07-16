from __future__ import annotations

import ast
import asyncio
import inspect
import io

from rich.console import Console

from openminion.cli.interactive.terminal.shell import (
    _SLASH_COMMANDS,
    _handle_slash,
)
from openminion.cli.interactive.terminal.status_line import TerminalStatusLine
from openminion.cli.interactive.terminal.transcript import TerminalTranscript


class _StubOverlay:
    pass


def _extract_implemented_slashes() -> set[str]:
    src = inspect.getsource(_handle_slash)
    tree = ast.parse(src)
    implemented: set[str] = set()

    for node in ast.walk(tree):
        # Match: `if cmd == "/foo":`
        if isinstance(node, ast.Compare):
            if (
                isinstance(node.left, ast.Name)
                and node.left.id == "cmd"
                and len(node.ops) == 1
                and isinstance(node.ops[0], ast.Eq)
                and len(node.comparators) == 1
                and isinstance(node.comparators[0], ast.Constant)
                and isinstance(node.comparators[0].value, str)
                and node.comparators[0].value.startswith("/")
            ):
                implemented.add(node.comparators[0].value)
            # Match: `if cmd in ("/foo", "/bar"):`
            if (
                isinstance(node.left, ast.Name)
                and node.left.id == "cmd"
                and len(node.ops) == 1
                and isinstance(node.ops[0], ast.In)
                and len(node.comparators) == 1
                and isinstance(node.comparators[0], (ast.Tuple, ast.List, ast.Set))
            ):
                for elt in node.comparators[0].elts:
                    if (
                        isinstance(elt, ast.Constant)
                        and isinstance(elt.value, str)
                        and elt.value.startswith("/")
                    ):
                        implemented.add(elt.value)

    return implemented


# ── Load-bearing test ─────────────────────────────────────────────


def test_slash_catalog_matches_implementation() -> None:
    cataloged = set(_SLASH_COMMANDS)
    implemented = _extract_implemented_slashes()
    missing = cataloged - implemented
    assert not missing, (
        f"Slashes in catalog without dispatch implementation: "
        f"{sorted(missing)}. Either implement them in _handle_slash "
        f"or strip them from _SLASH_COMMANDS."
    )


# ── Post-FIA-01 strip verification ───────────────────────────────


def test_stripped_slashes_not_in_catalog() -> None:
    stripped: set[str] = set()
    cataloged = set(_SLASH_COMMANDS)
    overlap = cataloged & stripped
    assert not overlap, (
        f"FIA-01 strip regression: {sorted(overlap)} reintroduced to "
        f"_SLASH_COMMANDS without implementation. Per FIA tracker "
        f"locked scope, these slashes need runtime cooperation or "
        f"duplicate existing slashes; do not re-add without "
        f"implementation."
    )


def test_implemented_slashes_in_catalog() -> None:
    cataloged = set(_SLASH_COMMANDS)
    implemented = _extract_implemented_slashes()
    # Originally-implemented (pre-FIA) slashes that MUST be in
    # the catalog.
    pre_fia = {
        "/clear",
        "/dashboard",
        "/exit",
        "/expand",
        "/help",
        "/normal",
        "/quiet",
        "/quit",
        "/verbose",
    }
    for slash in pre_fia:
        assert slash in cataloged, (
            f"{slash} dropped from catalog (pre-FIA implementation should still be exposed)"
        )
        assert slash in implemented, f"{slash} dispatch arm missing"


# ── Helper invariants ────────────────────────────────────────────


def test_extractor_finds_known_slashes() -> None:
    implemented = _extract_implemented_slashes()
    # Must find at least these well-known dispatch arms.
    assert "/" in implemented
    assert "/exit" in implemented
    assert "/quit" in implemented
    assert "/clear" in implemented
    assert "/expand" in implemented
    assert "/quiet" in implemented
    assert "/verbose" in implemented
    assert "/normal" in implemented


def test_catalog_has_no_duplicates() -> None:
    assert len(_SLASH_COMMANDS) == len(set(_SLASH_COMMANDS))


def test_catalog_size_after_fia_01() -> None:
    assert len(_SLASH_COMMANDS) >= 9, (
        f"Catalog has {len(_SLASH_COMMANDS)} entries; expected ≥ 9 "
        f"after FIA-01 strip pass (≥ 13 after FIA-05)"
    )


def test_bare_slash_dispatch_prints_menu() -> None:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=160)

    asyncio.run(
        _handle_slash(
            "/",
            runtime=object(),
            console=console,
            transcript=TerminalTranscript(console),
            overlay=_StubOverlay(),  # type: ignore[arg-type]
            status_line=TerminalStatusLine(),
            working_dir="/tmp",
        )
    )

    out = buf.getvalue()
    assert "Slash commands:" in out
    assert "/help" in out
    assert "not yet implemented" not in out
