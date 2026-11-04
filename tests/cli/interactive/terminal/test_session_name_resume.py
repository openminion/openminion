from __future__ import annotations

import sqlite3
from argparse import Namespace
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from openminion.base.config.action_policy import ACTION_POLICY_SESSION_OVERRIDE_KEY
from openminion.base.config.runtime.profile_overrides import RunProfileOverrides
from openminion.cli.commands.interactive import _launch_terminal_focus
from openminion.cli.interactive.runtime import OpenMinionRuntime
from openminion.modules.storage.runtime.migrations import migrate_database
from openminion.modules.storage.runtime.session_store import SessionStore
from openminion.modules.storage.runtime.sqlite import connect_database

_AGENT_ID = "agent-1"
_CHANNEL = "cli"
_TARGET = "focus"
_WORKING_DIR = "/tmp/project"
_OTHER_WORKING_DIR = "/tmp/other-project"


@pytest.fixture()
def runtime_store(
    tmp_path: Path,
) -> Iterator[tuple[SessionStore, sqlite3.Connection]]:
    db_path = tmp_path / "runtime-session-name.db"
    migrate_database(db_path)
    connection = connect_database(db_path)
    store = SessionStore(connection)
    yield store, connection
    connection.close()


def _runtime(store: SessionStore) -> OpenMinionRuntime:
    runtime = OpenMinionRuntime.__new__(OpenMinionRuntime)
    runtime._rt = SimpleNamespace(
        sessions=store,
        config=SimpleNamespace(runtime=SimpleNamespace(security_lab=None)),
        run_profile_overrides=RunProfileOverrides(),
    )
    runtime._agent_id = _AGENT_ID
    runtime._gateway = object()
    runtime._channel = _CHANNEL
    runtime._target = _TARGET
    runtime._history_limit = 200
    runtime._session_id = ""
    runtime._conversation_id = ""
    runtime._working_dir = _WORKING_DIR
    runtime._permission_mode = ""
    runtime._read_only_mode = False
    runtime._action_policy_mode_override = ""
    runtime._permission_overrides = {}
    runtime._permission_overrides_explicit = False
    runtime._permission_posture_diagnostic = ""
    runtime._project_context = None
    runtime._project_context_pending = False
    runtime.restore_session_model_selection = lambda record: None
    runtime._restore_token_usage_accounting = lambda: None
    runtime._clear_model_selection = lambda: None
    runtime._rebind_model_gateway = lambda: None
    return runtime


def _create_session(store: SessionStore, session_id: str, **metadata: Any) -> str:
    record = store.resolve_session(
        agent_id=_AGENT_ID,
        channel=_CHANNEL,
        target=_TARGET,
        session_id=session_id,
        metadata={**_metadata_defaults(), **metadata},
    )
    return record.id


def _metadata_defaults() -> dict[str, Any]:
    return {"working_dir": _WORKING_DIR}


def _set_updated_at(connection: sqlite3.Connection, session_id: str, iso: str) -> None:
    connection.execute(
        "UPDATE sessions SET updated_at = ? WHERE id = ?", (iso, session_id)
    )


def test_rename_oldest_session_still_resolves_by_name(
    runtime_store: tuple[SessionStore, sqlite3.Connection],
) -> None:
    store, connection = runtime_store
    target_id = _create_session(store, "focus-target")

    runtime = _runtime(store)
    runtime.rename_session(target_id, "Unique Project")

    # Fifty newer sessions so the renamed session is older than the 50 most
    # recent (the old limit-50 directory listing would have missed it).
    for index in range(50):
        _create_session(store, f"focus-later-{index:02d}")

    # Force a deterministic age order: the renamed session is the oldest.
    _set_updated_at(connection, target_id, "2020-01-01T00:00:00+00:00")
    connection.execute(
        'UPDATE sessions SET updated_at = "2021-01-01T00:00:00+00:00" WHERE id != ?',
        (target_id,),
    )
    connection.commit()

    fresh = _runtime(store)
    assert fresh.resolve_session_by_name("Unique Project") == target_id


def test_resolve_by_missing_name_returns_none(
    runtime_store: tuple[SessionStore, sqlite3.Connection],
) -> None:
    store, _connection = runtime_store
    _create_session(store, "focus-a", name="Project Alpha")

    assert _runtime(store).resolve_session_by_name("does not exist") is None
    assert _runtime(store).resolve_session_by_name("") is None


def test_resolve_by_name_is_case_insensitive_fallback(
    runtime_store: tuple[SessionStore, sqlite3.Connection],
) -> None:
    store, _connection = runtime_store
    session_id = _create_session(store, "focus-a", name="Project Alpha")

    assert _runtime(store).resolve_session_by_name("project ALPHA") == session_id


def test_duplicate_names_pick_most_recent_updated_at(
    runtime_store: tuple[SessionStore, sqlite3.Connection],
) -> None:
    store, connection = runtime_store
    older = _create_session(store, "focus-older", name="Duplicate")
    newer = _create_session(store, "focus-newer", name="Duplicate")

    _set_updated_at(connection, older, "2021-01-01T00:00:00+00:00")
    _set_updated_at(connection, newer, "2022-01-01T00:00:00+00:00")
    connection.commit()

    assert _runtime(store).resolve_session_by_name("Duplicate") == newer


def test_resolve_by_name_ignores_same_name_in_other_directory(
    runtime_store: tuple[SessionStore, sqlite3.Connection],
) -> None:
    store, _connection = runtime_store
    _create_session(
        store,
        "focus-other",
        name="Wanted",
        working_dir=_OTHER_WORKING_DIR,
    )

    runtime = _runtime(store)
    assert runtime.resolve_session_by_name("Wanted") is None
    assert runtime.resolve_session_by_name("wanted") is None


def test_duplicate_names_across_directories_resolve_current_dir(
    runtime_store: tuple[SessionStore, sqlite3.Connection],
) -> None:
    store, connection = runtime_store
    other = _create_session(
        store,
        "focus-other",
        name="Duplicate",
        working_dir=_OTHER_WORKING_DIR,
    )
    current = _create_session(store, "focus-current", name="Duplicate")

    # The other-directory duplicate is newer, so an unscoped lookup that
    # ignored the working directory would wrongly resolve it instead.
    _set_updated_at(connection, other, "2022-01-01T00:00:00+00:00")
    _set_updated_at(connection, current, "2021-01-01T00:00:00+00:00")
    connection.commit()

    assert _runtime(store).resolve_session_by_name("Duplicate") == current
    assert other != current


def test_resolve_by_name_without_working_dir_returns_none(
    runtime_store: tuple[SessionStore, sqlite3.Connection],
) -> None:
    store, _connection = runtime_store
    _create_session(store, "focus-a", name="Project Alpha")

    runtime = _runtime(store)
    runtime._working_dir = None

    assert runtime.resolve_session_by_name("Project Alpha") is None


def test_session_and_resume_flags_are_mutually_exclusive(capsys: Any) -> None:
    args = Namespace(agent=None, session="some-id", resume_name="Some Name")

    code = _launch_terminal_focus(
        args, None, working_dir="/tmp/project", read_only=False
    )

    assert code == 2
    assert "mutually exclusive" in capsys.readouterr().err


def test_renamed_session_keeps_conversation_count_and_permissions(
    runtime_store: tuple[SessionStore, sqlite3.Connection],
) -> None:
    store, _connection = runtime_store
    metadata = {
        "permission_mode": "auto",
        ACTION_POLICY_SESSION_OVERRIDE_KEY: "auto",
        "permission_overrides": '{"file.copy": "auto"}',
    }
    session_id = _create_session(store, "focus-kept", **metadata)
    store.append_message(session_id=session_id, role="user", body="hello")
    store.append_message(session_id=session_id, role="assistant", body="hi")

    renamed = _runtime(store)
    renamed.rename_session(session_id, "Kept Session")

    fresh = _runtime(store)
    fresh.bind_session(session_id)

    assert fresh.permission_mode == "auto"
    assert fresh.action_policy_mode_override == "auto"
    assert fresh.permission_overrides == {"file.copy": "auto"}
    assert store.count_messages(session_id=session_id) == 2
    bodies = [message.body for message in fresh.get_current_history()]
    assert set(bodies) == {"hello", "hi"}
    assert fresh.session_id == session_id
