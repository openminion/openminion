from __future__ import annotations

from openminion.modules.brain.loop.tools.cache import LoopCache


# Helpers


def _fake_result(summary: str) -> object:
    class _R:
        pass

    r = _R()
    r.summary = summary  # type: ignore[attr-defined]
    return r


# ACL-09 tests


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
    # Also verify a completely different path is a miss
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


def test_write_invalidates_overlapping_read_cache() -> None:
    cache = LoopCache()
    original = _fake_result("original content")

    cache.put("file.read", {"path": "config.py"}, original)
    assert cache.get("file.read", {"path": "config.py"}) is original

    # Write invalidates the cached read
    cache.invalidate_for_write("file.write", {"path": "config.py"})

    # Now the read entry is gone
    cache.hits = 0  # reset counters for clarity
    cache.misses = 0
    after = cache.get("file.read", {"path": "config.py"})
    assert after is None
    assert cache.misses == 1


def test_write_does_not_invalidate_different_path() -> None:
    cache = LoopCache()
    result_b = _fake_result("b content")

    cache.put("file.read", {"path": "b.py"}, result_b)
    cache.invalidate_for_write("file.write", {"path": "a.py"})

    assert cache.get("file.read", {"path": "b.py"}) is result_b


def test_non_write_tool_does_not_invalidate_cache() -> None:
    cache = LoopCache()
    result = _fake_result("content")

    cache.put("file.read", {"path": "x.py"}, result)
    cache.invalidate_for_write("exec.run", {"path": "x.py"})  # not a write tool

    assert cache.get("file.read", {"path": "x.py"}) is result


def test_all_read_cacheable_tools_are_cached() -> None:
    cacheable = [
        ("file.read", {"path": "a.py"}),
        ("file.read_range", {"path": "b.py"}),
        ("code.grep", {"path": "src/"}),
        ("file.list_dir", {"directory": "src/"}),
        ("file.find", {"directory": "src/"}),
        ("file_read", {"file": "c.py"}),
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
