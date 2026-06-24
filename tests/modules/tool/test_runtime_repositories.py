from __future__ import annotations

import threading
from pathlib import Path

from openminion.modules.tool.runtime.policy import Policy
from openminion.modules.tool.runtime import (
    LazyRepositoryHandle,
    RuntimeContext,
    RuntimeRepositories,
    build_runtime_repositories,
    resolve_cron_repository,
    resolve_identity_repository,
)


def test_build_runtime_repositories_uses_prewired_handles() -> None:
    identity_repo = object()
    cron_repo = object()
    repos = build_runtime_repositories(
        context_metadata={
            "runtime_repositories": {
                "identity": identity_repo,
                "cron": cron_repo,
            }
        }
    )
    assert repos.identity.get() is identity_repo
    assert repos.cron.get() is cron_repo


def test_build_runtime_repositories_disables_audit_sink_for_jsonl_only() -> None:
    repos = build_runtime_repositories(
        context_metadata={
            "storage_path": "dummy.db",
            "tool_runtime_audit_mode": "jsonl_only",
        }
    )
    assert repos.audit_db_path is None


def test_runtime_repositories_path_resolution_is_deterministic(
    monkeypatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    data = tmp_path / "data"
    first_identity = tmp_path / "identity-first.db"
    second_identity = tmp_path / "identity-second.db"
    home.mkdir(parents=True, exist_ok=True)
    data.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("OPENMINION_HOME", str(home))
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(data))
    monkeypatch.setenv("OPENMINION_IDENTITY_DB", str(first_identity))
    repos = build_runtime_repositories(context_metadata={})
    assert repos.identity_path == first_identity.resolve(strict=False)

    # Subsequent env changes should not mutate already-built wiring.
    monkeypatch.setenv("OPENMINION_IDENTITY_DB", str(second_identity))
    assert repos.identity_path == first_identity.resolve(strict=False)


def test_lazy_repository_handle_initializes_once_under_concurrency() -> None:
    calls = {"count": 0}
    counter_lock = threading.Lock()

    def _factory() -> object:
        with counter_lock:
            calls["count"] += 1
        return object()

    handle = LazyRepositoryHandle(_factory=_factory)
    results: list[object | None] = [None] * 20

    def _worker(idx: int) -> None:
        results[idx] = handle.get()

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(len(results))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert calls["count"] == 1
    first = results[0]
    assert first is not None
    assert all(item is first for item in results)


def test_runtime_context_exposes_repositories_accessor(tmp_path: Path) -> None:
    ctx = RuntimeContext(
        policy=Policy(raw={}),
        workspace=tmp_path,
        run_root=tmp_path,
        scope="READ_ONLY",
        confirm=False,
    )
    assert isinstance(ctx.repositories, RuntimeRepositories)


def test_resolve_identity_repository_prefers_prewired_handle(tmp_path: Path) -> None:
    identity_repo = object()
    ctx = RuntimeContext(
        policy=Policy(raw={}),
        workspace=tmp_path,
        run_root=tmp_path,
        scope="READ_ONLY",
        confirm=False,
        repositories=RuntimeRepositories(
            identity=LazyRepositoryHandle(_factory=lambda: identity_repo)
        ),
    )
    assert resolve_identity_repository(ctx) is identity_repo


def test_resolve_cron_repository_prefers_prewired_handle(tmp_path: Path) -> None:
    cron_repo = object()
    ctx = RuntimeContext(
        policy=Policy(raw={}),
        workspace=tmp_path,
        run_root=tmp_path,
        scope="READ_ONLY",
        confirm=False,
        repositories=RuntimeRepositories(
            cron=LazyRepositoryHandle(_factory=lambda: cron_repo)
        ),
    )
    assert resolve_cron_repository(ctx) is cron_repo
