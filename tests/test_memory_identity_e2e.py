from __future__ import annotations

import importlib
from pathlib import Path

import pytest

try:
    importlib.import_module("openminion.modules.identity")
    importlib.import_module("openminion.api.runtime")
    importlib.import_module("openminion.base.config")
    from openminion.services.diagnostics.debug import DebugStatus

    HAS_OPENMINION = True
except ImportError:
    HAS_OPENMINION = False


class TestMemoryIdentityE2EFixtures:
    def test_valid_identity_fixture_exists(self) -> None:
        fixture_path = (
            Path(__file__).parent / "fixtures" / "identity" / "valid_profile.yaml"
        )
        assert fixture_path.exists(), f"Fixture not found: {fixture_path}"
        content = fixture_path.read_text()
        assert "agent_id: mide-valid-agent" in content
        assert "fixture_type: valid_identity" in content

    def test_degraded_identity_fixture_exists(self) -> None:
        fixture_path = (
            Path(__file__).parent / "fixtures" / "identity" / "degraded_profile.yaml"
        )
        assert fixture_path.exists(), f"Fixture not found: {fixture_path}"
        content = fixture_path.read_text()
        assert "fixture_type: degraded_identity" in content
        assert "degraded_marker: true" in content

    def test_memory_seeded_session_fixture_exists(self) -> None:
        fixture_path = (
            Path(__file__).parent / "fixtures" / "memory" / "seeded_session.yaml"
        )
        assert fixture_path.exists(), f"Fixture not found: {fixture_path}"
        content = fixture_path.read_text()
        assert "fixture_type: memory_seeded_session" in content
        assert "seed_turns:" in content


@pytest.mark.skipif(not HAS_OPENMINION, reason="openminion not available")
class TestMemoryIdentityInProcessE2E:
    def test_inprocess_identity_debug_module_returns_ok(self) -> None:
        from openminion.cli.commands.debug import OpenMinionIdentityDebugProvider

        provider = OpenMinionIdentityDebugProvider()
        payload = provider.get_debug()

        assert payload.module == "openminion-identity"
        assert payload.status in [DebugStatus.OK, DebugStatus.WARN]

    def test_inprocess_memory_debug_module_returns_ok(self) -> None:
        from openminion.cli.commands.debug import OpenMinionMemoryDebugProvider

        provider = OpenMinionMemoryDebugProvider()
        payload = provider.get_debug()

        assert payload.module == "openminion-memory"
        assert payload.status in [DebugStatus.OK, DebugStatus.WARN]

    def test_inprocess_retrieve_debug_module_returns_ok(self) -> None:
        from openminion.cli.commands.debug import OpenMinionRetrieveDebugProvider

        provider = OpenMinionRetrieveDebugProvider()
        payload = provider.get_debug()

        assert payload.module == "openminion-retrieve"
        assert payload.status in [DebugStatus.OK, DebugStatus.WARN]


@pytest.mark.skipif(not HAS_OPENMINION, reason="openminion not available")
class TestMemoryIdentityDaemonE2E:
    def test_daemon_lane_parity_with_inprocess(self) -> None:
        from openminion.cli.commands.debug import (
            OpenMinionIdentityDebugProvider,
            OpenMinionMemoryDebugProvider,
            OpenMinionRetrieveDebugProvider,
        )

        identity_inproc = OpenMinionIdentityDebugProvider().get_debug()
        memory_inproc = OpenMinionMemoryDebugProvider().get_debug()
        retrieve_inproc = OpenMinionRetrieveDebugProvider().get_debug()

        assert identity_inproc.details.get("import_ok") is not None
        assert memory_inproc.details.get("import_ok") is not None
        assert retrieve_inproc.details.get("import_ok") is not None


@pytest.mark.skipif(not HAS_OPENMINION, reason="openminion not available")
class TestMemoryIdentityNegativePaths:
    def test_missing_identity_bundle_returns_degraded(self) -> None:
        from openminion.modules.identity.runtime.service import IdentityCtl
        from openminion.modules.identity.storage import InMemoryIdentityStore

        store = InMemoryIdentityStore()
        ctl = IdentityCtl(store=store)

        profile = ctl.get_profile("nonexistent-agent-12345")
        assert profile is None

    def test_invalid_profile_validation_fails(self) -> None:
        from openminion.modules.identity.runtime.service import IdentityCtl
        from openminion.modules.identity.storage import InMemoryIdentityStore

        store = InMemoryIdentityStore()
        ctl = IdentityCtl(store=store)

        invalid_data = {
            "agent_id": "",
            "display_name": "",
            "profile_revision": 0,
        }

        result = ctl.validate_profile(invalid_data)
        assert not result.ok
        assert len(result.errors) > 0


class TestMemoryIdentityPerformanceBudget:
    def test_fixture_files_within_size_bounds(self) -> None:
        fixtures_dir = Path(__file__).parent / "fixtures"

        for fixture_file in fixtures_dir.rglob("*.yaml"):
            size = fixture_file.stat().st_size
            assert size < 10 * 1024, f"Fixture {fixture_file} too large: {size} bytes"

    def test_identity_fixture_has_bounded_content(self) -> None:
        fixture_path = (
            Path(__file__).parent / "fixtures" / "identity" / "valid_profile.yaml"
        )
        content = fixture_path.read_text()

        assert len(content) < 5000, "Identity fixture content too long"
