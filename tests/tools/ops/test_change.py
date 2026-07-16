from pathlib import Path

import pytest

from openminion.tools.ops.change import apply_local_change, file_digest
from openminion.tools.ops.contracts import ChangePlan


def _plan(path: Path, content: str = "after") -> ChangePlan:
    return ChangePlan(
        plan_id="change",
        target_id="local",
        path=str(path),
        content=content,
        expected_digest=file_digest(path),
        expected_content=content,
    )


def test_change_requires_approval_and_current_precondition(tmp_path) -> None:
    path = tmp_path / "service.conf"
    path.write_text("before", encoding="utf-8")
    plan = _plan(path)

    with pytest.raises(PermissionError, match="explicit approval"):
        apply_local_change(plan, approved=False, allowed_root=tmp_path)
    path.write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="stale"):
        apply_local_change(plan, approved=True, allowed_root=tmp_path)


def test_change_is_atomic_and_checks_postcondition(tmp_path) -> None:
    path = tmp_path / "service.conf"
    path.write_text("before", encoding="utf-8")

    digest = apply_local_change(_plan(path), approved=True, allowed_root=tmp_path)

    assert path.read_text(encoding="utf-8") == "after"
    assert digest == file_digest(path)


def test_change_rolls_back_failed_verification(tmp_path) -> None:
    path = tmp_path / "service.conf"
    path.write_text("before", encoding="utf-8")

    with pytest.raises(RuntimeError, match="verification failed"):
        apply_local_change(
            _plan(path),
            approved=True,
            allowed_root=tmp_path,
            verify=lambda _: False,
        )

    assert path.read_text(encoding="utf-8") == "before"


def test_change_reports_rollback_failure(tmp_path, monkeypatch) -> None:
    path = tmp_path / "service.conf"
    path.write_text("before", encoding="utf-8")
    from openminion.tools.ops import change

    replace = change._replace  # noqa: SLF001 - fixture injects rollback failure
    calls = 0

    def fail_rollback(target: Path, content: bytes) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("rollback unavailable")
        replace(target, content)

    monkeypatch.setattr(change, "_replace", fail_rollback)

    with pytest.raises(OSError, match="rollback unavailable"):
        apply_local_change(
            _plan(path),
            approved=True,
            allowed_root=tmp_path,
            verify=lambda _: False,
        )
