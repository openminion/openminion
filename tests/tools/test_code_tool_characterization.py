from __future__ import annotations

from pathlib import Path
import shutil

import pytest

from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.contracts.model_ids import (
    MODEL_CODE_GREP,
    MODEL_CODE_PATCH,
    MODEL_CODE_REPO_INDEX,
    MODEL_CODE_REPO_MAP,
    MODEL_CODE_SYMBOL_FIND,
)
from openminion.modules.tool.runtime.policy import Policy
from openminion.modules.tool.registry import ToolRegistry
from openminion.modules.tool.runtime import RuntimeContext
from openminion.tools.code.cache import reset_repo_map_cache_for_tests
from openminion.tools.code.plugin import (
    CodeGrepArgs,
    _h_grep,
    _h_patch,
    _h_repo_index,
    _h_repo_map,
    _h_symbol_find,
    _resolve_code_path,
    register,
)
from openminion.tools.file.plugin import _h_write_file


def _ctx(tmp_path: Path, *, session_id: str = "code-session") -> RuntimeContext:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    run_root = tmp_path / "run"
    run_root.mkdir(parents=True, exist_ok=True)
    return RuntimeContext(
        policy=Policy(
            raw={
                "workspace_root": str(workspace),
                "paths": {
                    "read_allow": [str(workspace)],
                    "write_allow": [str(workspace)],
                    "deny": [],
                },
            }
        ),
        workspace=workspace,
        run_root=run_root,
        scope="WRITE_SAFE",
        confirm=False,
        telemetry_session_id=session_id,
    )


def test_code_register_exposes_expected_tools() -> None:
    registry = ToolRegistry()

    register(registry)

    for tool_name in (
        MODEL_CODE_PATCH,
        MODEL_CODE_GREP,
        MODEL_CODE_REPO_INDEX,
        MODEL_CODE_REPO_MAP,
        MODEL_CODE_SYMBOL_FIND,
    ):
        assert registry.get(tool_name).name == tool_name


def test_resolve_code_path_suggests_workspace_local_tmp_for_absolute_tmp(
    tmp_path: Path,
) -> None:
    ctx = _ctx(tmp_path)

    with pytest.raises(ToolRuntimeError) as excinfo:
        _resolve_code_path(ctx, "/tmp/http_server.asm", operation="write")

    assert excinfo.value.code == "POLICY_DENIED"
    assert excinfo.value.details["retry_path"] == "tmp/http_server.asm"
    assert "tmp/http_server.asm" in excinfo.value.message


def test_patch_applies_single_hunk(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    target = ctx.workspace / "alpha.py"
    target.write_text("one\ntwo\n", encoding="utf-8")

    result = _h_patch(
        {
            "path": "alpha.py",
            "patch": (
                "--- alpha.py\n+++ alpha.py\n@@ -1,2 +1,2 @@\n one\n-two\n+three\n"
            ),
        },
        ctx,
    )

    assert result == {
        "ok": True,
        "path": str(target),
        "hunk_count": 1,
    }
    assert target.read_text(encoding="utf-8") == "one\nthree\n"


def test_patch_offset_hunk_keeps_writes_to_target(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    target = ctx.workspace / "alpha.py"
    target.write_text("header\none\ntwo\n", encoding="utf-8")
    ctx.policy.raw["paths"]["write_allow"] = [str(target)]

    result = _h_patch(
        {
            "path": "alpha.py",
            "patch": "--- alpha.py\n+++ alpha.py\n@@ -1,2 +1,2 @@\n one\n-two\n+three\n",
        },
        ctx,
    )

    assert result["ok"] is True
    assert target.read_text() == "header\none\nthree\n"
    assert list(ctx.workspace.iterdir()) == [target]


def test_patch_accepts_diff_alias_and_ignores_workspace_hint(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    target = ctx.workspace / "alpha.py"
    target.write_text("one\ntwo\n", encoding="utf-8")

    result = _h_patch(
        {
            "file_path": "alpha.py",
            "workspace": str(ctx.workspace),
            "diff": (
                "--- alpha.py\n+++ alpha.py\n@@ -1,2 +1,2 @@\n one\n-two\n+three\n"
            ),
        },
        ctx,
    )

    assert result["ok"] is True
    assert target.read_text(encoding="utf-8") == "one\nthree\n"


def test_patch_accepts_target_alias(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    target = ctx.workspace / "alpha.py"
    target.write_text("one\ntwo\n", encoding="utf-8")

    result = _h_patch(
        {
            "target": "alpha.py",
            "patch": "--- alpha.py\n+++ alpha.py\n@@ -1,2 +1,2 @@\n one\n-two\n+three\n",
        },
        ctx,
    )

    assert result["ok"] is True
    assert target.read_text(encoding="utf-8") == "one\nthree\n"


def test_patch_rejects_non_applicable_patch(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    target = ctx.workspace / "alpha.py"
    target.write_text("one\ntwo\n", encoding="utf-8")

    result = _h_patch(
        {
            "path": "alpha.py",
            "patch": (
                "--- alpha.py\n+++ alpha.py\n@@ -1,2 +1,2 @@\n nope\n-missing\n+three\n"
            ),
        },
        ctx,
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "PATCH_FAILED"
    assert list(ctx.workspace.iterdir()) == [target]
    assert target.read_text() == "one\ntwo\n"


def test_grep_returns_structured_matches(tmp_path: Path) -> None:
    import shutil

    if shutil.which("rg") is None:
        import pytest

        pytest.skip("ripgrep (rg) not installed on PATH; _h_grep delegates to rg")
    ctx = _ctx(tmp_path)
    (ctx.workspace / "alpha.py").write_text(
        "def hello():\n    return 1\n", encoding="utf-8"
    )
    (ctx.workspace / "beta.txt").write_text("hello\n", encoding="utf-8")

    result = _h_grep(
        {"pattern": "hello", "path": ".", "file_glob": "*.py", "max_results": 10},
        ctx,
    )

    assert result["ok"] is True
    assert result["count"] == 1
    assert result["matches"] == [
        {
            "file": str(ctx.workspace / "alpha.py"),
            "line": 1,
            "text": "def hello():",
        }
    ]


def test_grep_schema_preserves_model_facing_scope_aliases() -> None:
    schema = CodeGrepArgs.model_json_schema()

    assert {"cwd", "file_pattern"} <= set(schema["properties"])
    args = CodeGrepArgs.model_validate(
        {
            "pattern": "hello",
            "cwd": "src",
            "file_pattern": "**/*.py",
        }
    )
    assert args.path == "src"
    assert args.file_glob == "**/*.py"


def test_grep_returns_empty_match_list(tmp_path: Path) -> None:
    import shutil

    if shutil.which("rg") is None:
        import pytest

        pytest.skip("ripgrep (rg) not installed on PATH; _h_grep delegates to rg")
    ctx = _ctx(tmp_path)
    (ctx.workspace / "alpha.py").write_text(
        "def hello():\n    return 1\n", encoding="utf-8"
    )

    result = _h_grep(
        {"pattern": "missing", "path": ".", "file_glob": "*.py", "max_results": 10},
        ctx,
    )

    assert result["ok"] is True
    assert result["matches"] == []
    assert result["count"] == 0


def test_repo_map_cache_invalidates_after_file_write(tmp_path: Path) -> None:
    reset_repo_map_cache_for_tests()
    ctx = _ctx(tmp_path)
    (ctx.workspace / "alpha.py").write_text(
        "def alpha():\n    return 1\n", encoding="utf-8"
    )

    first = _h_repo_map({"path": ".", "max_tokens": 256}, ctx)
    second = _h_repo_map({"path": ".", "max_tokens": 256}, ctx)

    assert first["ok"] is True
    assert second["ok"] is True
    assert first["cached"] is False
    assert second["cached"] is True

    _h_write_file({"path": "beta.py", "content": "def beta():\n    return 2\n"}, ctx)

    third = _h_repo_map({"path": ".", "max_tokens": 256}, ctx)
    assert third["ok"] is True
    assert third["cached"] is False
    assert "beta.py :: beta" in third["repo_map"]


def test_repo_map_truncates_to_budget(tmp_path: Path) -> None:
    reset_repo_map_cache_for_tests()
    ctx = _ctx(tmp_path, session_id="budgeted")
    (ctx.workspace / "alpha.py").write_text(
        "\n".join(f"def fn_{idx}():\n    return {idx}" for idx in range(40)),
        encoding="utf-8",
    )

    result = _h_repo_map({"path": ".", "max_tokens": 128}, ctx)

    assert result["ok"] is True
    assert len(result["repo_map"]) <= 128 * 4


def test_repo_index_returns_structured_python_relationships(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    (ctx.workspace / "alpha.py").write_text(
        "from beta import helper\n\nclass Alpha:\n    pass\n\ndef local():\n    return helper()\n",
        encoding="utf-8",
    )
    (ctx.workspace / "beta.py").write_text(
        "def helper():\n    return 1\n",
        encoding="utf-8",
    )

    result = _h_repo_index({"path": "."}, ctx)

    assert result["ok"] is True
    assert result["repo_index"]["files"][0]["path"].endswith("alpha.py")
    assert any(item["name"] == "Alpha" for item in result["repo_index"]["symbols"])
    assert any(item["module"] == "beta" for item in result["repo_index"]["imports"])


def test_repo_index_skips_hidden_and_generated_paths(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    (ctx.workspace / "alpha.py").write_text("def alpha():\n    return 1\n")
    (ctx.workspace / ".DS_Store").write_text("noise")
    (ctx.workspace / ".venv").mkdir()
    (ctx.workspace / ".venv" / "ignored.py").write_text("def ignored():\n    pass\n")
    (ctx.workspace / "demo.egg-info").mkdir()
    (ctx.workspace / "demo.egg-info" / "PKG-INFO").write_text("metadata")

    result = _h_repo_index({"path": "."}, ctx)

    indexed_paths = {
        Path(item["path"]).relative_to(ctx.workspace).as_posix()
        for item in result["repo_index"]["files"]
    }
    assert indexed_paths == {"alpha.py"}


def test_symbol_find_returns_definition_lines(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    target = ctx.workspace / "alpha.py"
    target.write_text(
        "class Alpha:\n    pass\n\ndef helper():\n    return 1\n",
        encoding="utf-8",
    )

    result = _h_symbol_find({"symbol": "Alpha", "path": "."}, ctx)

    assert result["ok"] is True
    assert result["count"] == 1
    assert result["matches"][0]["file"] == str(target)
    assert result["matches"][0]["start_line"] == 1
    assert result["matches"][0]["end_line"] == 2
    assert result["matches"][0]["kind"] == "class"


def test_symbol_find_returns_structured_not_found(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    (ctx.workspace / "alpha.py").write_text(
        "def helper():\n    return 1\n", encoding="utf-8"
    )

    result = _h_symbol_find({"symbol": "MissingSymbol", "path": "."}, ctx)

    assert result == {
        "ok": False,
        "error": {
            "code": "NOT_FOUND",
            "message": "symbol not found: MissingSymbol",
        },
        "matches": [],
    }


def test_patch_uses_context_metadata_cwd_for_relative_paths(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    nested = ctx.workspace / "openminion"
    nested.mkdir()
    target = nested / "alpha.py"
    target.write_text("one\ntwo\n", encoding="utf-8")
    ctx.policy.raw["context_metadata"] = {"cwd": str(nested)}

    result = _h_patch(
        {
            "path": "alpha.py",
            "patch": (
                "--- alpha.py\n+++ alpha.py\n@@ -1,2 +1,2 @@\n one\n-two\n+three\n"
            ),
        },
        ctx,
    )

    assert result == {
        "ok": True,
        "path": str(target),
        "hunk_count": 1,
    }
    assert target.read_text(encoding="utf-8") == "one\nthree\n"


def test_failed_second_patch_hunk_preserves_original(tmp_path):
    ctx = _ctx(tmp_path)
    target = ctx.workspace / "partial.txt"
    original = "one\ntwo\nthree\nfour\nfive\nsix\nseven\neight\nnine\nten\n"
    target.write_text(original)
    result = _h_patch(
        {
            "path": "partial.txt",
            "patch": "--- partial.txt\n+++ partial.txt\n@@ -1,2 +1,2 @@\n-one\n+changed\n two\n@@ -9,2 +9,2 @@\n nine\n-missing\n+newten\n",
        },
        ctx,
    )
    assert result["ok"] is False
    assert target.read_text() == original
    assert sorted(p.name for p in ctx.workspace.iterdir()) == ["partial.txt"]


@pytest.mark.parametrize(
    "name,pattern", [("ordinary.txt", "-needle"), ("a:b.txt", "needle")]
)
@pytest.mark.skipif(shutil.which("rg") is None, reason="ripgrep not installed")
def test_grep_literal_dash_and_colon_paths(tmp_path, name, pattern):
    ctx = _ctx(tmp_path)
    (ctx.workspace / name).write_text("-needle\n")
    (ctx.workspace / "false-positive.txt").write_text("edle\n")
    result = _h_grep({"path": ".", "pattern": pattern}, ctx)
    assert result["ok"] is True
    assert result["count"] == 1
    assert result["matches"][0]["file"] == str(ctx.workspace / name)


@pytest.mark.parametrize(
    "tool,args",
    [
        (_h_grep, {"pattern": "hidden_symbol"}),
        (_h_repo_map, {}),
        (_h_repo_index, {}),
        (_h_symbol_find, {"symbol": "hidden_symbol"}),
    ],
)
def test_recursive_code_tools_respect_denied_descendants(tmp_path, tool, args):
    import json

    if tool is _h_grep and shutil.which("rg") is None:
        pytest.skip("ripgrep not installed")
    ctx = _ctx(tmp_path)
    private = ctx.workspace / "private"
    private.mkdir()
    (private / "secret.py").write_text("def hidden_symbol():\n    pass\n")
    ctx.policy.raw["paths"]["deny"] = [str(private)]
    result = tool({"path": ".", **args}, ctx)
    assert "secret.py" not in json.dumps(result)
    if tool is _h_symbol_find:
        assert result["error"]["code"] == "NOT_FOUND"
    else:
        assert result["ok"] is True
        assert "hidden_symbol" not in json.dumps(
            result.get("matches", result.get("repo_map", result.get("repo_index", {})))
        )


def test_repo_map_does_not_reuse_cache_after_policy_change(tmp_path):
    ctx = _ctx(tmp_path)
    (ctx.workspace / "secret.py").write_text("def hidden_symbol():\n    pass\n")
    assert "hidden_symbol" in _h_repo_map({"path": "."}, ctx)["repo_map"]
    ctx.policy.raw["paths"]["deny"] = [str(ctx.workspace / "secret.py")]
    assert "hidden_symbol" not in _h_repo_map({"path": "."}, ctx)["repo_map"]


def test_symbol_find_accepts_an_explicit_file(tmp_path):
    ctx = _ctx(tmp_path)
    (ctx.workspace / "a:b.py").write_text("def exact_symbol():\n    pass\n")
    result = _h_symbol_find({"path": "a:b.py", "symbol": "exact_symbol"}, ctx)
    assert result["ok"] is True
    assert result["matches"][0]["start_line"] == 1
