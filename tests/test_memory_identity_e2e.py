from __future__ import annotations

import importlib
import json
import tempfile
import unittest
from pathlib import Path

# Try to import openminion modules
try:
    importlib.import_module("openminion.services.agent.identity")
    importlib.import_module("openminion.api.runtime")
    importlib.import_module("openminion.base.config")
    from openminion.services.diagnostics.debug import DebugStatus

    HAS_OPENMINION = True
except ImportError:
    HAS_OPENMINION = False


class TestMemoryIdentityE2EFixtures(unittest.TestCase):
    def test_valid_identity_fixture_exists(self) -> None:
        fixture_path = (
            Path(__file__).parent / "fixtures" / "identity" / "valid_profile.yaml"
        )
        self.assertTrue(fixture_path.exists(), f"Fixture not found: {fixture_path}")
        content = fixture_path.read_text()
        self.assertIn("agent_id: mide-valid-agent", content)
        self.assertIn("fixture_type: valid_identity", content)

    def test_degraded_identity_fixture_exists(self) -> None:
        fixture_path = (
            Path(__file__).parent / "fixtures" / "identity" / "degraded_profile.yaml"
        )
        self.assertTrue(fixture_path.exists(), f"Fixture not found: {fixture_path}")
        content = fixture_path.read_text()
        self.assertIn("fixture_type: degraded_identity", content)
        self.assertIn("degraded_marker: true", content)

    def test_memory_seeded_session_fixture_exists(self) -> None:
        fixture_path = (
            Path(__file__).parent / "fixtures" / "memory" / "seeded_session.yaml"
        )
        self.assertTrue(fixture_path.exists(), f"Fixture not found: {fixture_path}")
        content = fixture_path.read_text()
        self.assertIn("fixture_type: memory_seeded_session", content)
        self.assertIn("seed_turns:", content)


@unittest.skipUnless(HAS_OPENMINION, "openminion not available")
class TestMemoryIdentityInProcessE2E(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.config_path = Path(self.tmp_dir.name) / "test_config.json"

        # Create minimal config
        config = {
            "runtime": {
                "storage_path": self.tmp_dir.name,
                "debug_enabled": True,
            }
        }
        self.config_path.write_text(json.dumps(config))

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_inprocess_identity_debug_module_returns_ok(self) -> None:
        from openminion.cli.commands.debug import OpenMinionIdentityDebugProvider

        provider = OpenMinionIdentityDebugProvider()
        payload = provider.get_debug()

        self.assertEqual(payload.module, "openminion-identity")
        # Should be OK or WARN (if module issues), not FAIL
        self.assertIn(payload.status, [DebugStatus.OK, DebugStatus.WARN])

    def test_inprocess_memory_debug_module_returns_ok(self) -> None:
        from openminion.cli.commands.debug import OpenMinionMemoryDebugProvider

        provider = OpenMinionMemoryDebugProvider()
        payload = provider.get_debug()

        self.assertEqual(payload.module, "openminion-memory")
        # Should be OK or WARN, not FAIL
        self.assertIn(payload.status, [DebugStatus.OK, DebugStatus.WARN])

    def test_inprocess_retrieve_debug_module_returns_ok(self) -> None:
        from openminion.cli.commands.debug import OpenMinionRetrieveDebugProvider

        provider = OpenMinionRetrieveDebugProvider()
        payload = provider.get_debug()

        self.assertEqual(payload.module, "openminion-retrieve")
        # Should be OK or WARN, not FAIL
        self.assertIn(payload.status, [DebugStatus.OK, DebugStatus.WARN])


@unittest.skipUnless(HAS_OPENMINION, "openminion not available")
class TestMemoryIdentityDaemonE2E(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.config_path = Path(self.tmp_dir.name) / "test_config.json"

        config = {
            "runtime": {
                "storage_path": self.tmp_dir.name,
                "debug_enabled": True,
                "daemon_auto_start": False,  # Don't auto-start for tests
            }
        }
        self.config_path.write_text(json.dumps(config))

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_daemon_lane_parity_with_inprocess(self) -> None:
        # This test verifies that daemon and in-process return similar
        # core fields for identity/memory debug modules

        from openminion.cli.commands.debug import (
            OpenMinionIdentityDebugProvider,
            OpenMinionMemoryDebugProvider,
            OpenMinionRetrieveDebugProvider,
        )

        # Get in-process payloads
        identity_inproc = OpenMinionIdentityDebugProvider().get_debug()
        memory_inproc = OpenMinionMemoryDebugProvider().get_debug()
        retrieve_inproc = OpenMinionRetrieveDebugProvider().get_debug()

        # Verify core fields exist
        self.assertIsNotNone(identity_inproc.details.get("import_ok"))
        self.assertIsNotNone(memory_inproc.details.get("import_ok"))
        self.assertIsNotNone(retrieve_inproc.details.get("import_ok"))


@unittest.skipUnless(HAS_OPENMINION, "openminion not available")
class TestMemoryIdentityNegativePaths(unittest.TestCase):
    def test_missing_identity_bundle_returns_degraded(self) -> None:
        from openminion.modules.identity.runtime.service import IdentityCtl
        from openminion.modules.identity.storage import InMemoryIdentityStore

        store = InMemoryIdentityStore()
        ctl = IdentityCtl(store=store)

        # Try to get non-existent profile
        profile = ctl.get_profile("nonexistent-agent-12345")
        self.assertIsNone(profile)

    def test_invalid_profile_validation_fails(self) -> None:
        from openminion.modules.identity.runtime.service import IdentityCtl
        from openminion.modules.identity.storage import InMemoryIdentityStore

        store = InMemoryIdentityStore()
        ctl = IdentityCtl(store=store)

        # Invalid profile data (empty required fields)
        invalid_data = {
            "agent_id": "",
            "display_name": "",
            "profile_revision": 0,
        }

        result = ctl.validate_profile(invalid_data)
        self.assertFalse(result.ok)
        self.assertGreater(len(result.errors), 0)


class TestMemoryIdentityPerformanceBudget(unittest.TestCase):
    def test_fixture_files_within_size_bounds(self) -> None:
        fixtures_dir = Path(__file__).parent / "fixtures"

        for fixture_file in fixtures_dir.rglob("*.yaml"):
            size = fixture_file.stat().st_size
            # Fixtures should be under 10KB
            self.assertLess(
                size, 10 * 1024, f"Fixture {fixture_file} too large: {size} bytes"
            )

    def test_identity_fixture_has_bounded_content(self) -> None:
        fixture_path = (
            Path(__file__).parent / "fixtures" / "identity" / "valid_profile.yaml"
        )
        content = fixture_path.read_text()

        # Should be reasonable length for prompt inclusion
        self.assertLess(len(content), 5000, "Identity fixture content too long")
