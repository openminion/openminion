from __future__ import annotations

from types import SimpleNamespace

from openminion.modules.brain.loop.tools.cache import LoopCache


def _fake_result(summary: str) -> object:
    return SimpleNamespace(
        action_result=SimpleNamespace(status="success", summary=summary), job=None
    )


def test_identical_read_returns_cached_result() -> None:
    cache = LoopCache()
    result = _fake_result("file content")

    cache.put("file.read", {"path": "app.py"}, result)
    retrieved = cache.get("file.read", {"path": "app.py"})

    assert retrieved is result
    assert cache.hits == 1
    assert cache.misses == 0


def test_different_args_cause_cache_miss() -> None:
    cache = LoopCache()
    result_a = _fake_result("content of a")
    result_b = _fake_result("content of b")

    cache.put("file.read", {"path": "a.py"}, result_a)
    cache.put("file.read", {"path": "b.py"}, result_b)

    assert cache.get("file.read", {"path": "a.py"}) is result_a
    assert cache.get("file.read", {"path": "b.py"}) is result_b
    miss = cache.get("file.read", {"path": "c.py"})
    assert miss is None
    assert cache.misses == 1


def test_exec_tools_bypass_cache() -> None:
    cache = LoopCache()
    result = _fake_result("exec output")

    cache.put("exec.run", {"cmd": "pytest"}, result)  # no-op, nothing stored
    retrieved = cache.get("exec.run", {"cmd": "pytest"})

    assert retrieved is None
    assert cache.hits == 0
    assert cache.misses == 1  # only get() registers a miss; put() is a silent no-op


def _status(status: str) -> object:
    return SimpleNamespace(action_result=SimpleNamespace(status=status), job=None)


def test_write_invalidates_overlapping_read_cache() -> None:
    cache = LoopCache()
    original = _fake_result("original content")

    cache.put("file.read", {"path": "config.py"}, original)
    assert cache.get("file.read", {"path": "config.py"}) is original

    cache.invalidate_for_tool("file.write", {"path": "config.py"}, _status("success"))

    cache.hits = 0
    cache.misses = 0
    assert cache.get("file.read", {"path": "config.py"}) is None
    assert cache.misses == 1


def test_write_does_not_invalidate_different_path() -> None:
    cache = LoopCache()
    result_b = _fake_result("b content")

    cache.put("file.read", {"path": "b.py"}, result_b)
    cache.invalidate_for_tool("file.write", {"path": "a.py"}, _status("success"))

    assert cache.get("file.read", {"path": "b.py"}) is result_b


def test_unknown_tool_invalidates_the_read_cache() -> None:
    cache = LoopCache()
    result = _fake_result("content")

    cache.put("file.read", {"path": "x.py"}, result)
    cache.invalidate_for_tool("exec.run", {"cmd": "echo x > x.py"}, _status("success"))

    assert cache.get("file.read", {"path": "x.py"}) is None


def test_failed_known_writer_still_invalidates() -> None:
    cache = LoopCache()
    result = _fake_result("content")

    cache.put("file.read", {"path": "x.py"}, result)
    cache.invalidate_for_tool("file.write", {"path": "x.py"}, _status("failed"))

    assert cache.get("file.read", {"path": "x.py"}) is None


def test_failed_unknown_tool_clears_the_read_cache() -> None:
    cache = LoopCache()
    result = _fake_result("content")

    cache.put("file.read", {"path": "x.py"}, result)
    cache.invalidate_for_tool("exec.run", {"cmd": "echo x > x.py"}, _status("timeout"))

    assert cache.get("file.read", {"path": "x.py"}) is None


def test_denied_tool_keeps_the_read_cache() -> None:
    for denial in ("blocked", "needs_user"):
        cache = LoopCache()
        result = _fake_result("content")

        cache.put("file.read", {"path": "x.py"}, result)
        cache.invalidate_for_tool("file.write", {"path": "x.py"}, _status(denial))
        cache.invalidate_for_tool("exec.run", {"cmd": "false"}, _status(denial))

        assert cache.get("file.read", {"path": "x.py"}) is result, denial


def test_directory_read_is_invalidated_by_write_inside_it() -> None:
    cache = LoopCache()
    find_result = _fake_result("match")
    src_result = _fake_result("src match")

    cache.put("file.find", {"path": ".", "pattern": "*.py"}, find_result)
    cache.put("code.grep", {"path": "src", "query": "needle"}, src_result)

    cache.invalidate_for_tool("file.write", {"path": "a.py"}, _status("success"))
    assert cache.get("file.find", {"path": ".", "pattern": "*.py"}) is None
    assert cache.get("code.grep", {"path": "src", "query": "needle"}) is src_result

    cache.invalidate_for_tool("file.write", {"path": "src/a.py"}, _status("success"))
    assert cache.get("code.grep", {"path": "src", "query": "needle"}) is None


def test_pathless_reads_are_invalidated_by_any_write() -> None:
    cache = LoopCache()
    grep_result = _fake_result("match in a.py")

    cache.put("code.grep", {"pattern": "needle"}, grep_result)
    assert cache.get("code.grep", {"pattern": "needle"}) is grep_result

    cache.invalidate_for_tool("file.write", {"path": "a.py"}, _status("success"))

    assert cache.get("code.grep", {"pattern": "needle"}) is None


def test_write_path_is_read_from_alias_argument_names() -> None:
    for alias in ("filename", "file_path", "destination", "target"):
        cache = LoopCache()
        result = _fake_result("content")

        cache.put("file.read", {"path": "d.py"}, result)
        cache.invalidate_for_tool("file.write", {alias: "d.py"}, _status("success"))

        assert cache.get("file.read", {"path": "d.py"}) is None, alias


def test_unrelated_tool_name_is_not_treated_as_a_path() -> None:
    cache = LoopCache()
    result = _fake_result("content")

    cache.put("file.read", {"path": "e.py"}, result)
    # A write that carries no recognisable path has unknown reach.
    cache.invalidate_for_tool("file.write", {"content": "x"}, _status("success"))

    assert cache.get("file.read", {"path": "e.py"}) is None


def test_all_read_cacheable_tools_are_cached() -> None:
    cacheable = [
        ("file.read", {"path": "a.py"}),
        ("file.read_range", {"path": "b.py"}),
        ("code.grep", {"path": "src/"}),
        ("file.list_dir", {"path": "src/"}),
        ("file.find", {"path": "src/"}),
        ("file_read", {"path": "c.py"}),
    ]
    for tool_name, args in cacheable:
        cache = LoopCache()
        result = _fake_result(f"result for {tool_name}")
        cache.put(tool_name, args, result)
        assert cache.get(tool_name, args) is result, f"{tool_name} should be cacheable"


def test_hit_miss_counters_accumulate() -> None:
    cache = LoopCache()
    cache.put("file.read", {"path": "z.py"}, _fake_result("z"))

    cache.get("file.read", {"path": "z.py"})
    cache.get("file.read", {"path": "z.py"})
    cache.get("file.read", {"path": "nope.py"})
    cache.get("exec.run", {"cmd": "echo"})

    assert cache.hits == 2
    assert cache.misses == 2


def test_failed_and_pending_reads_are_not_cached() -> None:
    for status in ("failed", "timeout", "retry", "blocked", "needs_user"):
        cache = LoopCache()
        cache.put("file.read", {"path": "a"}, _status(status))
        assert cache.get("file.read", {"path": "a"}) is None
    cache = LoopCache()
    cache.put(
        "file.read",
        {"path": "a"},
        SimpleNamespace(action_result=SimpleNamespace(status="success"), job=object()),
    )
    assert cache.get("file.read", {"path": "a"}) is None
