from __future__ import annotations

from pathlib import Path

from openminion.modules import paths as module_paths
from openminion.modules.brain import constants as brain_constants
from openminion.modules.controlplane import constants as controlplane_constants
from openminion.modules.controlplane.channels.telegram import (
    constants as controlplane_telegram_constants,
)
from openminion.modules.context import constants as context_constants
from openminion.modules.identity import constants as identity_constants
from openminion.modules.memory import constants as memory_constants
from openminion.modules.brain.loop.recursive import constants as rlm_constants
from openminion.modules.session import constants as session_constants
from openminion.modules.tool import constants as tool_constants


def test_module_paths_exports_shared_session_identity_and_memory_families() -> None:
    assert module_paths.SESSION_DIRNAME == "session"
    assert module_paths.SESSIONS_DB_FILENAME == "sessions.db"
    assert module_paths.SESSION_DB_SUBPATH == Path("session") / "sessions.db"
    assert module_paths.STANDALONE_SESSION_DIRNAME == ".sessctl"
    assert (
        module_paths.STANDALONE_SESSION_DB_SUBPATH == Path(".sessctl") / "sessions.db"
    )
    assert module_paths.IDENTITY_DIRNAME == "identity"
    assert module_paths.IDENTITY_DB_FILENAME == "identity.db"
    assert module_paths.IDENTITY_DB_SUBPATH == Path("identity") / "identity.db"
    assert module_paths.MEMORY_DIRNAME == "memory"
    assert module_paths.MEMORY_DB_FILENAME == "memory.db"
    assert module_paths.MEMORY_DB_SUBPATH == Path("memory") / "memory.db"
    assert module_paths.STANDALONE_CONTROLPLANE_DIRNAME == ".controlplane"
    assert module_paths.CONTROLPLANE_DIRNAME == "controlplane"


def test_session_family_consumers_alias_shared_paths() -> None:
    assert (
        brain_constants.DEFAULT_SESSION_DB_FILENAME == module_paths.SESSIONS_DB_FILENAME
    )
    assert brain_constants.DEFAULT_SESSION_DB_SUBPATH == module_paths.SESSION_DB_SUBPATH
    assert context_constants.DEFAULT_STANDALONE_SESSION_DB_SUBPATH == (
        module_paths.STANDALONE_SESSION_DB_SUBPATH
    )
    assert context_constants.DEFAULT_INTEGRATED_SESSION_DB_SUBPATH == (
        module_paths.SESSION_DB_SUBPATH
    )
    assert rlm_constants.DEFAULT_STANDALONE_SESSION_DB_SUBPATH == (
        module_paths.STANDALONE_SESSION_DB_SUBPATH
    )
    assert rlm_constants.DEFAULT_INTEGRATED_SESSION_DB_SUBPATH == (
        module_paths.SESSION_DB_SUBPATH
    )
    assert session_constants.DEFAULT_STANDALONE_DB_SUBPATH == (
        module_paths.STANDALONE_SESSION_DB_SUBPATH
    )
    assert (
        session_constants.DEFAULT_INTEGRATED_DB_SUBPATH
        == module_paths.SESSION_DB_SUBPATH
    )


def test_identity_family_consumers_alias_shared_path_but_keep_identityctl_local() -> (
    None
):
    assert (
        brain_constants.DEFAULT_IDENTITY_DB_FILENAME
        == module_paths.IDENTITY_DB_FILENAME
    )
    assert (
        brain_constants.DEFAULT_IDENTITY_DB_SUBPATH == module_paths.IDENTITY_DB_SUBPATH
    )
    assert context_constants.DEFAULT_INTEGRATED_IDENTITY_DB_SUBPATH == (
        module_paths.IDENTITY_DB_SUBPATH
    )
    assert (
        identity_constants.DEFAULT_IDENTITY_DB_FILENAME
        == module_paths.IDENTITY_DB_FILENAME
    )
    assert identity_constants.DEFAULT_INTEGRATED_BUNDLE_SUBPATH == Path(
        module_paths.IDENTITY_DIRNAME
    )
    assert identity_constants.DEFAULT_IDENTITY_CTL_DB_FILENAME == "identityctl.db"
    assert identity_constants.DEFAULT_INTEGRATED_STORAGE_SUBPATH == (
        Path(module_paths.IDENTITY_DIRNAME) / "identityctl.db"
    )
    assert (
        tool_constants.DEFAULT_IDENTITY_DB_SUBPATH == module_paths.IDENTITY_DB_SUBPATH
    )


def test_memory_family_consumers_alias_shared_integrated_path_but_keep_memctl_local() -> (
    None
):
    assert brain_constants.DEFAULT_MEMORY_DB_FILENAME == module_paths.MEMORY_DB_FILENAME
    assert brain_constants.DEFAULT_MEMORY_DB_SUBPATH == module_paths.MEMORY_DB_SUBPATH
    assert (
        memory_constants.DEFAULT_INTEGRATED_SQLITE_SUBPATH
        == module_paths.MEMORY_DB_SUBPATH
    )
    assert memory_constants.DEFAULT_STANDALONE_ROOT_SUBPATH == Path(".memctl")
    assert (
        memory_constants.DEFAULT_STANDALONE_SQLITE_SUBPATH
        == Path(".memctl") / "memory.db"
    )


def test_controlplane_consumers_alias_shared_dirnames_but_keep_local_filenames() -> (
    None
):
    assert controlplane_constants.DEFAULT_STANDALONE_SQLITE_SUBPATH == (
        Path(module_paths.STANDALONE_CONTROLPLANE_DIRNAME) / "cp.db"
    )
    assert controlplane_constants.DEFAULT_INTEGRATED_SQLITE_SUBPATH == (
        Path(module_paths.CONTROLPLANE_DIRNAME) / "cp.db"
    )
    assert controlplane_telegram_constants.DEFAULT_STANDALONE_POLL_STATE_SUBPATH == (
        Path(module_paths.STANDALONE_CONTROLPLANE_DIRNAME) / "telegram-poll-state.db"
    )
    assert controlplane_telegram_constants.DEFAULT_INTEGRATED_POLL_STATE_SUBPATH == (
        Path(module_paths.CONTROLPLANE_DIRNAME) / "telegram-poll-state.db"
    )
    assert controlplane_telegram_constants.DEFAULT_HOME_ROOT_POLL_STATE_SUBPATH == (
        Path(".openminion")
        / module_paths.CONTROLPLANE_DIRNAME
        / "telegram-poll-state.db"
    )
