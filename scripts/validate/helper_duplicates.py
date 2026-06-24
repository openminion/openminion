#!/usr/bin/env python3
"""Detect repeated helper bodies that should converge on one owner."""

from __future__ import annotations

import ast
import collections
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SCAN_ROOT = REPO_ROOT / "src" / "openminion"

EXCLUDED_NAMES: set[str] = {
    "_as_bool",
    "_as_int",
    "_as_float",
    "_as_str",
    "_as_str_or_none",
    "_as_non_empty_str",
    "_as_list",
    "_as_str_list",
    "_as_obj",
    "_as_optional_float",
    "_as_optional_int",
    "_as_non_negative_int",
    "_as_positive_int",
    "_json",
    "_json_load",
    "_json_list_dump",
    "_json_list_load",
    "_iso_now",
    "_now_iso",
    "_now_ts",
    "_utc_now",
    "_coerce_bool",
    "_coerce_int",
    "_coerce_float",
    "_coerce_str",
    "_safe_json_loads",
    "_new_id",
    "_normalize",
    "_candidate",
    "_env",
    "_get",
    "_clean",
    "_strip",
    "_check",
    "_validate",
    "_resolve",
    "_format",
    "_parse",
    "_parse_json",
    "_build",
    "_make",
    "_create",
    "_find",
    "_from_dict",
    "_to_dict",
    "_hash",
    "_eq",
    "_repr",
    "_runner_delegate",
}

EXCLUDED_PAIRS: set[tuple[str, str]] = {
    ("modules/brain", "_exit_code"),
    ("modules/brain/modes", "_prepare"),
    ("modules/compress", "_count_tokens"),
    ("modules/context", "_content_hash"),
    ("cli/commands", "_resolve_root"),
    ("modules/llm/providers", "_last_user_text"),
    ("modules/llm/providers", "_resolve_api_key"),
    ("modules/skill", "_dedupe"),
    ("modules/skill", "_parse_scalar"),
    ("modules/task", "_derive_task_status"),
    ("modules/task", "_next_actionable_step"),
    ("modules/tool", "_json_schema_type_ok"),
    ("modules/tool", "_validate_against_schema"),
    ("services/gateway", "_text_fingerprint"),
    ("tools/search/providers/tavily", "_format_web_search_content"),
    ("tools/search/providers/tavily", "_tavily_api_key"),
    ("tools/search/providers/tavily", "_tavily_api_url"),
    ("tools/search/providers/tavily", "_verify_web_search_payload"),
    ("modules/retrieve", "_safe_json_loads"),
    ("modules/storage/migrations", "_utc_now_iso"),
}
STORAGE_PAIR_SUFFIXES = (
    "store",
    "sqlite_store",
    "audit_store",
    "sqlite_audit_store",
    "service",
    "persistent_service",
)
STORAGE_PAIR_NAMES = {"memory"}


def _is_storage_pair_file(path: pathlib.Path) -> bool:
    """Return whether the file belongs to an intentional storage/service split."""
    name = path.stem
    return name in STORAGE_PAIR_NAMES or any(
        name.endswith(suffix) for suffix in STORAGE_PAIR_SUFFIXES
    )


def _collect_private_functions(path: pathlib.Path) -> list[str]:
    try:
        source = path.read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(source, filename=str(path))
    except (SyntaxError, OSError):
        return []
    names = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            name = node.name
            if name.startswith("_") and name not in EXCLUDED_NAMES:
                names.append(name)
    return names


def _is_excluded_file(path: pathlib.Path) -> bool:
    return (
        path.name in {"__init__.py", "__main__.py"}
        or "tests" in path.parts
        or path.name.startswith("test_")
    )


def _is_excluded_pair(rel_dir: str, fn_name: str) -> bool:
    for dir_suffix, name in EXCLUDED_PAIRS:
        if rel_dir.endswith(dir_suffix) and fn_name == name:
            return True
    return False


def main() -> int:
    dir_fn_files: dict[pathlib.Path, dict[str, list[pathlib.Path]]] = (
        collections.defaultdict(lambda: collections.defaultdict(list))
    )

    for path in sorted(SCAN_ROOT.rglob("*.py")) if SCAN_ROOT.exists() else ():
        if _is_excluded_file(path):
            continue
        parent = path.parent
        for fn_name in _collect_private_functions(path):
            dir_fn_files[parent][fn_name].append(path)

    hits: list[str] = []
    for directory, fn_map in sorted(dir_fn_files.items()):
        rel_dir = str(directory.relative_to(REPO_ROOT))
        for fn_name, files in sorted(fn_map.items()):
            if len(files) < 2:
                continue
            if _is_excluded_pair(rel_dir, fn_name):
                continue
            storage_files = [f for f in files if _is_storage_pair_file(f)]
            non_storage_files = [f for f in files if not _is_storage_pair_file(f)]
            if len(non_storage_files) < 2 and len(storage_files) >= len(files) - 1:
                continue
            file_list = ", ".join(str(f.relative_to(REPO_ROOT)) for f in sorted(files))
            hits.append(f"{rel_dir}: duplicate helper '{fn_name}' in [{file_list}]")

    if hits:
        sys.stderr.write(
            "Duplicated private helper functions detected in sibling files:\n"
        )
        sys.stderr.write("\n".join(hits) + "\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
