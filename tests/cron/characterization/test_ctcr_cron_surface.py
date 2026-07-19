from __future__ import annotations

import pytest


# Package-level re-exports (matrix §3.2 __init__.py preserves these).


def test_cron_package_re_exports_canonical_public_surface() -> None:

    from openminion.services import cron as mod

    expected = {
        "CRON_INTERFACE_VERSION",
        "CronDeliveryHandler",
        "CronEventHook",
        "CronExecutionResult",
        "CronExecutor",
        "CronScheduler",
        "CronSchedulerInterface",
        "CronStore",
        "CronStoreInterface",
        "CronStoreProtocol",
        "HttpPost",
        "MisfirePolicy",
        "OutboundSender",
        "compute_next_due",
        "default_delete_after_run",
        "default_session_target_for_payload",
        "deliver_cron_result",
        "encode_misfire_policy",
        "ensure_cron_compatibility",
        "ensure_cron_store_compatibility",
        "normalize_delivery",
        "normalize_misfire_policy",
        "normalize_payload",
        "normalize_schedule",
        "normalize_session_target",
        "normalize_wake_mode",
        "parse_iso_datetime",
        "to_iso_utc",
        "utc_now",
        "validate_target_payload_pair",
    }
    for name in expected:
        assert hasattr(mod, name), f"modules.cron missing public export {name!r}"


def test_cron_interface_version_is_stable() -> None:

    from openminion.services.cron import CRON_INTERFACE_VERSION

    assert isinstance(CRON_INTERFACE_VERSION, str)
    assert CRON_INTERFACE_VERSION.strip()


# Per-submodule imports (matrix §3.2 target-path entries).


@pytest.mark.parametrize(
    "submodule,attr",
    [
        ("config", None),
        ("scheduler", "CronScheduler"),
    ],
)
def test_every_live_submodule_resolves(submodule: str, attr: str | None) -> None:

    module = __import__(
        f"openminion.services.cron.{submodule}",
        fromlist=["*"],
    )
    assert module is not None
    if attr is not None:
        assert hasattr(module, attr), f"cron.{submodule} missing {attr!r}"


# The service package composes task-owned scheduling with service orchestration.


def test_cron_service_imports_task_scheduling_only_at_composition_points() -> None:

    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[3]
    cron_dir = repo_root / "src" / "openminion" / "services" / "cron"
    importers: set[str] = set()
    for py_file in cron_dir.rglob("*.py"):
        text = py_file.read_text()
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if (
                "from openminion.modules.task.scheduling" in stripped
                or "import openminion.modules.task.scheduling" in stripped
            ):
                importers.add(str(py_file.relative_to(cron_dir)))

    assert importers == {"__init__.py", "scheduler.py"}


# Cross-package importer seams (matrix §4.3).


def test_non_test_src_importers_resolve() -> None:

    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[3]
    service_importers = {
        "src/openminion/services/health/lifecycle.py",
        "src/openminion/services/runtime/daemon.py",
    }
    canonical_importers = {
        "src/openminion/modules/session/interfaces.py",
        "src/openminion/modules/session/storage/repository.py",
        "src/openminion/modules/session/storage/cron_store.py",
        "src/openminion/tools/time/plugin.py",
        "src/openminion/tools/task/plugin.py",
        "src/openminion/tools/task/scheduled_task/runtime.py",
        "src/openminion/tools/task/scheduled_task/views.py",
    }
    for rel in service_importers:
        path = repo_root / rel
        assert path.exists(), f"Known non-test importer missing: {rel}"
        content = path.read_text()
        assert "openminion.services.cron" in content, (
            f"{rel} lost its supported cron compatibility import"
        )
    for rel in canonical_importers:
        path = repo_root / rel
        assert path.exists(), f"Known canonical importer missing: {rel}"
        content = path.read_text()
        assert "openminion.modules.task.scheduling" in content, (
            f"{rel} lost its task-owned scheduling import"
        )
