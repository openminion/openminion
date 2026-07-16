from __future__ import annotations

import pytest


# Package-level re-exports (matrix §3.1 __init__.py preserves these).


def test_telegram_package_re_exports_canonical_public_surface() -> None:

    from openminion.modules.controlplane.channels import telegram as mod

    # Every name in the top-level __all__ is a public contract.
    expected = {
        "ControlplaneTelegramConfig",
        "TELEGRAM_INTERFACE_VERSION",
        "TelegramChannelConfig",
        "TelegramPollingRunner",
        "ensure_telegram_component_compatibility",
        "load_config",
        "__version__",
    }
    for name in expected:
        assert hasattr(mod, name), (
            f"controlplane_telegram missing public export {name!r}"
        )


def test_telegram_interface_version_is_stable() -> None:

    from openminion.modules.controlplane.channels.telegram import (
        TELEGRAM_INTERFACE_VERSION,
    )

    assert isinstance(TELEGRAM_INTERFACE_VERSION, str)
    assert TELEGRAM_INTERFACE_VERSION.strip(), (
        "TELEGRAM_INTERFACE_VERSION must be non-empty"
    )


# Per-submodule imports (matrix §3.1 target-path entries).


@pytest.mark.parametrize(
    "submodule,attr",
    [
        ("access", None),
        ("approval", None),
        ("bot_api", None),
        ("clarify", None),
        ("command_aliases", None),
        ("config", "ControlplaneTelegramConfig"),
        ("constants", None),
        ("debug_provider", None),
        ("delivery", None),
        ("events", None),
        ("interfaces", "TELEGRAM_INTERFACE_VERSION"),
        ("listener", None),
        ("models", None),
        ("normalization", None),
        ("pairing", None),
        ("polling", "TelegramPollingRunner"),
        ("reactions", None),
        ("state", None),
        ("webhook", None),
    ],
)
def test_every_live_submodule_resolves(submodule: str, attr: str | None) -> None:

    module = __import__(
        f"openminion.modules.controlplane.channels.telegram.{submodule}",
        fromlist=["*"],
    )
    assert module is not None
    if attr is not None:
        assert hasattr(module, attr), (
            f"controlplane_telegram.{submodule} missing {attr!r}"
        )


@pytest.mark.parametrize(
    "storage_submodule",
    ["base", "integrity", "io", "store"],
)
def test_storage_submodules_resolve(storage_submodule: str) -> None:

    module = __import__(
        f"openminion.modules.controlplane.channels.telegram.storage.{storage_submodule}",
        fromlist=["*"],
    )
    assert module is not None


def test_storage_migrations_resolve() -> None:

    from openminion.modules.controlplane.channels.telegram.storage import migrations

    assert migrations is not None


# Cross-package importer seams (matrix §4.1 — 7 non-test src importers).


def test_non_test_src_importers_resolve() -> None:

    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[3]
    known_non_test_importers = {
        "src/openminion/cli/commands/agents.py",
        "src/openminion/cli/commands/channel.py",
        "src/openminion/cli/commands/debug/registry.py",
        "src/openminion/services/bootstrap/config.py",
        "src/openminion/services/runtime/catalog.py",
        "src/openminion/services/runtime/lifecycle.py",
        "src/openminion/services/diagnostics/debug.py",
    }
    for rel in known_non_test_importers:
        path = repo_root / rel
        assert path.exists(), f"Known non-test importer missing: {rel}"
        content = path.read_text()
        assert "openminion.modules.controlplane.channels.telegram" in content, (
            f"{rel} lost its controlplane_telegram import "
            f"(CTCR-01 audit needs refresh before CTCR-05)"
        )


# Console script (matrix §4.5).


def test_console_script_entry_targets_current_cli() -> None:

    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[3]
    pyproject = repo_root / "pyproject.toml"
    content = pyproject.read_text()
    assert "openminion-controlplane-telegram =" in content, (
        "pyproject.toml missing openminion-controlplane-telegram console script"
    )
    # Pre-migration target:
    assert "openminion.modules.controlplane.channels.telegram.cli:main" in content, (
        "pyproject.toml console script does not point at pre-migration cli; "
        "CTCR-01 audit needs refresh"
    )
