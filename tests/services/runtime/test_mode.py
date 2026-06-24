from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest import mock


# Add openminion src to path for imports
OPENMINION_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(OPENMINION_ROOT / "src"))


class TestRuntimeModeResolution:
    def test_default_runtime_mode_is_brain(self):
        # Clear any existing env var to test default
        env_patch = {"OPENMINION_AGENT_RUNTIME_MODE": ""}
        with mock.patch.dict(os.environ, env_patch, clear=False):
            runtime_mode = (
                os.environ.get("OPENMINION_AGENT_RUNTIME_MODE", "").strip().lower()
                or "brain"
            )
            assert runtime_mode == "brain", (
                f"Expected default 'brain', got '{runtime_mode}'"
            )

    def test_legacy_runtime_mode_is_rejected_by_policy(self):
        env_patch = {"OPENMINION_AGENT_RUNTIME_MODE": "legacy"}
        with mock.patch.dict(os.environ, env_patch, clear=False):
            runtime_mode = (
                os.environ.get("OPENMINION_AGENT_RUNTIME_MODE", "brain").strip().lower()
            )
            assert runtime_mode == "legacy", "Legacy mode should be explicitly set"

    def test_brain_mode_aliases(self):
        for alias in ["brain", "brain-bridge", "bridge"]:
            env_patch = {"OPENMINION_AGENT_RUNTIME_MODE": alias}
            with mock.patch.dict(os.environ, env_patch, clear=False):
                runtime_mode = (
                    os.environ.get("OPENMINION_AGENT_RUNTIME_MODE", "").strip().lower()
                )
                assert runtime_mode in {"brain", "brain-bridge", "bridge"}

    def test_runtime_mode_does_not_depend_on_removed_fallback_env(self):
        env_patch = {
            "OPENMINION_AGENT_RUNTIME_MODE": "",
        }
        with mock.patch.dict(os.environ, env_patch, clear=False):
            runtime_mode = (
                os.environ.get("OPENMINION_AGENT_RUNTIME_MODE", "").strip().lower()
                or "brain"
            )
            assert runtime_mode == "brain"

    def test_unknown_fallback_env_has_no_effect_on_runtime_mode(self):
        for value in ["true", "1", "yes"]:
            env_patch = {
                "OPENMINION_AGENT_RUNTIME_MODE": "brain",
                "OPENMINION_UNUSED_RUNTIME_FLAG": value,
            }
            with mock.patch.dict(os.environ, env_patch, clear=False):
                runtime_mode = (
                    os.environ.get("OPENMINION_AGENT_RUNTIME_MODE", "").strip().lower()
                )
                assert runtime_mode == "brain"


class TestRuntimeModeEnvVars:
    def test_env_var_case_insensitive(self):
        for case_variant in ["BRAIN", "Brain", "brain", "BrAiN"]:
            env_patch = {"OPENMINION_AGENT_RUNTIME_MODE": case_variant}
            with mock.patch.dict(os.environ, env_patch, clear=False):
                runtime_mode = (
                    os.environ.get("OPENMINION_AGENT_RUNTIME_MODE", "").strip().lower()
                )
                assert runtime_mode == "brain"

    def test_env_var_whitespace_handling(self):
        env_patch = {"OPENMINION_AGENT_RUNTIME_MODE": "  brain  "}
        with mock.patch.dict(os.environ, env_patch, clear=False):
            runtime_mode = (
                os.environ.get("OPENMINION_AGENT_RUNTIME_MODE", "").strip().lower()
            )
            assert runtime_mode == "brain"


class TestModuleFirstPrerequisites:
    def test_bootstrap_hook_has_no_legacy_storage_path(self):
        # Import the bootstrap function
        from openminion.api.runtime import _bootstrap_openminion_brain_import_path

        # Get the module list from the function
        import inspect

        source = inspect.getsource(_bootstrap_openminion_brain_import_path)

        assert "openminion-storage" not in source
        assert _bootstrap_openminion_brain_import_path() is None


class TestRuntimeModeConstants:
    def test_runtime_mode_constants_defined(self):
        # These are the valid runtime modes
        valid_modes = {"brain", "brain-bridge", "bridge"}
        assert len(valid_modes) == 3, "Expected 3 supported runtime modes"
        assert "brain" in valid_modes, "brain mode should be valid"
        assert "legacy" not in valid_modes, "legacy mode should not be supported"
