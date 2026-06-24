from __future__ import annotations

from pathlib import Path

import pytest

from openminion.modules.context.repo_map import (
    AstRepoMapBuilder,
    RepoMap,
    RepoMapCache,
    RepoSymbol,
    build_repo_map,
    rank_symbols,
    serialize_repo_map,
)
from openminion.modules.context.repo_map.config import RepoMapConfig
from openminion.modules.context.repo_map.section import (
    build_repo_map_section,
)


_FIXTURE_PY = '''"""Module docstring line 1.

Second line.
"""

def free_function(x):
    """Free function doc."""
    return x


class FooClass:
    """Foo doc."""

    def method_one(self, y):
        """Method one doc."""
        return y

    class NestedClass:
        """Nested doc."""

        def nested_method(self):
            return 1
'''


def _write_fixture(tmp: Path, *, name: str = "mod.py", body: str = _FIXTURE_PY) -> Path:
    f = tmp / name
    f.write_text(body, encoding="utf-8")
    return f


def _build_fixture_repo_map(tmp_path: Path):
    _write_fixture(tmp_path)
    return build_repo_map(tmp_path)


def test_repo_symbol_is_frozen():
    sym = RepoSymbol(path="m.py", name="x", kind="function")

    with pytest.raises(Exception):
        sym.name = "y"  # type: ignore[misc]


def test_repo_map_carries_parser_version():
    repo_map = RepoMap(root="/")
    assert repo_map.parser_version == "ast-1"


def test_ast_parser_extracts_classes_functions_methods_and_nested(tmp_path: Path):
    repo_map = _build_fixture_repo_map(tmp_path)
    kinds = {(s.name, s.kind) for s in repo_map.symbols}
    assert ("free_function", "function") in kinds
    assert ("FooClass", "class") in kinds
    assert ("method_one", "method") in kinds
    assert ("NestedClass", "class") in kinds
    assert ("nested_method", "method") in kinds


def test_ast_parser_captures_signatures_and_docstrings(tmp_path: Path):
    repo_map = _build_fixture_repo_map(tmp_path)
    by_name = {s.name: s for s in repo_map.symbols}
    assert by_name["free_function"].signature == "free_function(x)"
    assert by_name["free_function"].docstring_first_line == "Free function doc."
    assert by_name["method_one"].signature == "method_one(self, y)"


def test_ast_parser_emits_module_symbol_for_module_docstring(tmp_path: Path):
    repo_map = _build_fixture_repo_map(tmp_path)
    module_syms = [s for s in repo_map.symbols if s.kind == "module"]
    assert len(module_syms) == 1
    assert module_syms[0].docstring_first_line == "Module docstring line 1."


def test_ast_parser_skips_syntax_error_files_silently(tmp_path: Path):
    _write_fixture(tmp_path, name="good.py")
    (tmp_path / "bad.py").write_text("def broken(:\n", encoding="utf-8")
    repo_map = build_repo_map(tmp_path)
    names = {s.name for s in repo_map.symbols}
    assert "free_function" in names


def test_rank_symbols_returns_deterministic_top_k(tmp_path: Path):
    repo_map = _build_fixture_repo_map(tmp_path)
    a = rank_symbols(repo_map, top_k=3)
    b = rank_symbols(repo_map, top_k=3)
    assert [s.name for s in a] == [s.name for s in b]
    assert len(a) <= 3


def test_rank_symbols_promotes_pinned_names_to_top(tmp_path: Path):
    repo_map = _build_fixture_repo_map(tmp_path)
    ranked = rank_symbols(repo_map, pinned_names={"nested_method"})
    assert ranked[0].name == "nested_method"


def test_serialize_respects_token_budget(tmp_path: Path):
    ranked = rank_symbols(_build_fixture_repo_map(tmp_path))
    out = serialize_repo_map(ranked, token_budget=10)
    assert "[truncated]" in out


def test_serialize_includes_signature_and_docstring_within_budget(tmp_path: Path):
    ranked = rank_symbols(_build_fixture_repo_map(tmp_path))
    out = serialize_repo_map(ranked, token_budget=10000)
    assert "free_function(x)" in out
    assert "Free function doc." in out


def test_cache_is_fresh_after_record(tmp_path: Path):
    f = _write_fixture(tmp_path)
    cache = RepoMapCache()
    cache.record(f, [])
    assert cache.is_fresh(f) is True


def test_cache_invalidates_on_content_change(tmp_path: Path):
    f = _write_fixture(tmp_path)
    cache = RepoMapCache()
    cache.record(f, [])
    f.write_text(_FIXTURE_PY + "\n# changed\n", encoding="utf-8")
    assert cache.is_fresh(f) is False


def test_cache_save_and_load_round_trip(tmp_path: Path):
    f = _write_fixture(tmp_path)
    cache = RepoMapCache()
    cache.record(f, [])
    save_path = tmp_path / "cache.json"
    cache.save_to(save_path)
    loaded = RepoMapCache.load_from(save_path)
    assert loaded.entries == cache.entries


def test_cache_refresh_reparses_only_changed_files(tmp_path: Path):
    a = _write_fixture(tmp_path, name="a.py", body="def fa(): pass\n")
    b = _write_fixture(tmp_path, name="b.py", body="def fb(): pass\n")
    cache = RepoMapCache()
    cache.refresh(tmp_path, builder=AstRepoMapBuilder())
    b.write_text("def fb_new(): pass\n", encoding="utf-8")
    assert cache.is_fresh(a) is True
    assert cache.is_fresh(b) is False


def test_bridge_returns_empty_when_disabled(tmp_path: Path):
    _write_fixture(tmp_path)
    section = build_repo_map_section(tmp_path, config=RepoMapConfig(enabled=False))
    assert section == ""


def test_bridge_returns_empty_when_profile_gated_out(tmp_path: Path):
    _write_fixture(tmp_path)
    config = RepoMapConfig(enabled=True, profile_gate=("coding",))
    section = build_repo_map_section(tmp_path, config=config, profile="research")
    assert section == ""


def test_bridge_emits_section_when_enabled_and_profile_matches(tmp_path: Path):
    _write_fixture(tmp_path)
    config = RepoMapConfig(enabled=True, profile_gate=("coding",))
    section = build_repo_map_section(tmp_path, config=config, profile="coding")
    assert section.startswith("[REPO MAP]")
    assert "free_function" in section


def test_repo_map_config_default_disabled():
    cfg = RepoMapConfig()
    assert cfg.enabled is False
    assert cfg.token_budget == 1500
    assert "coding" in cfg.profile_gate
