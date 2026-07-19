from __future__ import annotations

import importlib
from pathlib import Path

BASE_ROOT = Path(__file__).resolve().parents[2] / "src" / "openminion" / "base"
IMPORT_SURFACE = [
    ("openminion.base", "ConfigManager"),
    ("openminion.base", "UserIO"),
    ("openminion.base.channel", "ChannelRegistry"),
    ("openminion.base.channel", "build_default_channel_registry"),
    ("openminion.base.config", "OpenMinionConfig"),
    ("openminion.base.config", "resolve_default_agent_id"),
    ("openminion.base.errors", "ErrorInfo"),
    ("openminion.base.errors", "error_info_from_exception"),
    ("openminion.base.runtime", "RUNTIME_INTERFACE_VERSION"),
    ("openminion.base.runtime", "LocalRunner"),
    ("openminion.base.runtime", "ExecutionSandboxSpec"),
    ("openminion.base.constants", "OPENMINION_HOME_ENV"),
    ("openminion.base.types", "Message"),
    ("openminion.base.protocol", "ProtocolError"),
    ("openminion.base.logging", "configure_logging"),
    ("openminion.base.redaction", "redact_mapping"),
    ("openminion.base.user_io", "UserIO"),
    ("openminion.base.generated_paths", "resolve_generated_root"),
    ("openminion.base.debug", "DebugStatus"),
]
EXPECTED_ROOT_FILES = {
    "README.md",
    "__init__.py",
    "constants.py",
    "debug.py",
    "generated_paths.py",
    "logging.py",
    "protocol.py",
    "redaction.py",
    "time.py",
    "types.py",
    "user_io.py",
    "version.py",
}
EXPECTED_SUBPACKAGES = {"channel", "config", "errors", "runtime"}


def test_base_layout_characterization_import_surface() -> None:
    for module_name, attr_name in IMPORT_SURFACE:
        module = importlib.import_module(module_name)
        assert hasattr(module, attr_name), f"{module_name} missing {attr_name}"


def test_base_root_files_match_bcd_option_c_contract() -> None:
    root_files = {path.name for path in BASE_ROOT.iterdir() if path.is_file()}
    assert root_files == EXPECTED_ROOT_FILES


def test_base_subpackages_match_admitted_charter_set() -> None:
    top_level_dirs = {
        path.name
        for path in BASE_ROOT.iterdir()
        if path.is_dir() and path.name != "__pycache__"
    }
    assert top_level_dirs == EXPECTED_SUBPACKAGES
