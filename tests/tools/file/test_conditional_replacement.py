"""Conditional replacement rejects stale observations without truncating files."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from threading import Event, current_thread

import pytest

from openminion.modules.tool.errors import ToolRuntimeError
from openminion.tools.file.backends import InMemoryStorageBackend, LocalStorageBackend
from openminion.tools.file.backends import local
from openminion.tools.file.plugin import _h_read_file, _h_write_file
from tests.test_file_tool_characterization import _ctx


def digest(content):
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@pytest.mark.parametrize("kind", ["local", "memory"])
@pytest.mark.parametrize(
    "observed,current",
    [
        ("a\r\nb", "a\r\nb"),
        ("a\rb", "a\rb"),
        ("a\nb", "a\r\nb"),
        ("a\r\nb", "a\nb"),
    ],
)
def test_read_fingerprint_is_the_write_precondition(tmp_path, kind, observed, current):
    ctx = _ctx(tmp_path)
    ctx.policy.raw["file_backend"] = kind
    path = {"path": "a"}
    assert _h_write_file({**path, "content": observed}, ctx)["ok"]
    read = _h_read_file(path, ctx)
    assert read["content_sha256"] == digest(observed)
    if current != observed:
        assert _h_write_file({**path, "content": current}, ctx)["ok"]
    result = _h_write_file(
        {**path, "content": "READY", "expected_content_sha256": read["content_sha256"]},
        ctx,
    )
    if current == observed:
        assert result["ok"]
        expected = "READY"
    else:
        assert result["error"]["code"] == "PRECONDITION_FAILED"
        expected = current
    assert _h_read_file(path, ctx)["content_sha256"] == digest(expected)
    if kind == "local":
        assert (ctx.workspace / "a").read_bytes() == expected.encode("utf-8")


@pytest.fixture(params=["local", "memory"])
def backend(request, tmp_path):
    return (
        LocalStorageBackend(tmp_path)
        if request.param == "local"
        else InMemoryStorageBackend(root=tmp_path)
    )


def test_matching_precondition_replaces_content(backend, tmp_path):
    path = str(tmp_path / "a")
    backend.write(path, "OLD")
    backend.write(path, "READY", expected_content_sha256=digest("OLD"))
    assert backend.read(path).content == "READY"


def test_mismatch_preserves_existing_bytes(backend, tmp_path):
    path = str(tmp_path / "a")
    backend.write(path, "OTHER")
    with pytest.raises(ToolRuntimeError) as error:
        backend.write(path, "READY", expected_content_sha256=digest("OLD"))
    assert error.value.code == "PRECONDITION_FAILED"
    assert backend.read(path).content == "OTHER"


def test_conditional_append_does_not_apply_effect(backend, tmp_path):
    path = str(tmp_path / "a")
    backend.write(path, "OLD")
    with pytest.raises(ToolRuntimeError):
        backend.write(path, "READY", append=True, expected_content_sha256=digest("OLD"))
    assert backend.read(path).content == "OLD"


def test_missing_target_is_not_created(backend, tmp_path):
    path = str(tmp_path / "new" / "a")
    with pytest.raises(ToolRuntimeError):
        backend.write(path, "READY", expected_content_sha256=digest("OLD"))
    assert not backend.exists(path)
    assert not backend.exists(str(tmp_path / "new"))


def test_same_content_replacement_changes_version_and_is_rejected(tmp_path):
    ctx = _ctx(tmp_path)
    target = ctx.workspace / "a"
    target.write_text("OLD")
    observed = _h_read_file({"path": "a"}, ctx)
    replacement = ctx.workspace / "replacement"
    replacement.write_text("OLD")
    replacement.replace(target)
    out = _h_write_file(
        {
            "path": "a",
            "content": "READY",
            "expected_content_sha256": digest("OLD"),
            "expected_version": observed["version"],
        },
        ctx,
    )
    assert out["error"]["code"] == "PRECONDITION_FAILED"
    assert target.read_text() == "OLD"


def test_symlink_leaf_is_not_followed_by_conditional_write(tmp_path):
    backend = LocalStorageBackend(tmp_path)
    target, alias = tmp_path / "a", tmp_path / "alias"
    target.write_text("OLD")
    alias.symlink_to(target)
    with pytest.raises(ToolRuntimeError):
        backend.write(str(alias), "READY", expected_content_sha256=digest("OLD"))
    assert target.read_text() == "OLD"
    assert alias.is_symlink()


def test_unsupported_platform_fails_before_effect(tmp_path, monkeypatch):
    backend = LocalStorageBackend(tmp_path)
    target = tmp_path / "a"
    target.write_text("OLD")
    monkeypatch.setattr(local, "fcntl", None)
    with pytest.raises(ToolRuntimeError) as error:
        backend.write(str(target), "READY", expected_content_sha256=digest("OLD"))
    assert error.value.code == "PLATFORM_UNSUPPORTED"
    assert target.read_text() == "OLD"


def test_cooperating_writer_waits_before_truncation(tmp_path, monkeypatch):
    backend = LocalStorageBackend(tmp_path)
    target = tmp_path / "a"
    target.write_text("OLD")
    checking, waiting, release = Event(), Event(), Event()
    original_check, original_lock = (
        local.check_replacement_precondition,
        local.fcntl.flock,
    )

    def check(content, expected):
        checking.set()
        assert release.wait(5)
        original_check(content, expected)

    def flock(fd, operation):
        if current_thread().name.startswith("ordinary"):
            waiting.set()
        original_lock(fd, operation)

    monkeypatch.setattr(local, "check_replacement_precondition", check)
    monkeypatch.setattr(local.fcntl, "flock", flock)
    with (
        ThreadPoolExecutor(max_workers=1, thread_name_prefix="guarded") as first,
        ThreadPoolExecutor(max_workers=1, thread_name_prefix="ordinary") as second,
    ):
        guarded = first.submit(
            backend.write, str(target), "READY", expected_content_sha256=digest("OLD")
        )
        assert checking.wait(5)
        ordinary = second.submit(backend.write, str(target), "OTHER")
        try:
            assert waiting.wait(5)
            assert target.read_text() == "OLD"
        finally:
            release.set()
        guarded.result(timeout=5)
        ordinary.result(timeout=5)
    assert target.read_text() == "OTHER"
