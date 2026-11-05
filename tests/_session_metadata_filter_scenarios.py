"""Session metadata filter scenarios shared by the SQLite and PostgreSQL tests.

pytest does not rewrite the assertions of this module, so each check compares
one result with its expectation and prints both on failure.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from openminion.modules.storage.runtime.session_store import SessionStore

_TARGET = "metadata-filters"

# Session id -> metadata, as callers store it.
_SESSIONS: dict[str, dict[str, Any]] = {
    "acp-a": {"surface": "acp", "workspace_root": "/work/a"},
    "acp-b": {"surface": "acp", "workspace_root": "/work/b"},
    "cli-a": {"surface": "cli", "workspace_root": "/work/a"},
    "no-surface": {"workspace_root": "/work/a"},
    "null-surface": {"surface": None},
    "empty-surface": {"surface": ""},
    "list-surface": {"surface": ["acp"]},
    "int-value": {"value": 1},
    "float-value": {"value": 1.5},
    "true-value": {"value": True},
    "false-value": {"value": False},
    "null-value": {"value": None},
    "object-value": {"value": {"kind": "acp"}},
    "text-one": {"value": "1"},
    "text-true": {"value": "true"},
    "text-null": {"value": "null"},
    "quote-key": {"it's": "yes"},
    "like-key": {"100%_done": "yes"},
    "dollar-key": {"$scope": "yes"},
    "space-key": {"a b": "yes"},
    "sql-key": {"k') OR ('1'='1": "yes"},
    "escaped-value": {"note": 'say "hi" \\ C:\\work\\a'},
    "unicode-value": {"note": "caf\u00e9 \U0001f600 \u2028"},
    "newline-value": {"note": "line\nbreak\ttab"},
    "like-value": {"note": "a%"},
}

# Filter -> the sessions it selects, on both backends.
_EXPECTED: list[tuple[dict[str, Any], set[str]]] = [
    ({"surface": "acp"}, {"acp-a", "acp-b"}),
    ({"surface": "focus"}, set()),
    ({"surface": "ACP"}, set()),
    ({"surface": "acp", "workspace_root": "/work/a"}, {"acp-a"}),
    ({"surface": "acp", "workspace_root": "/work/c"}, set()),
    ({"surface": "acp", "missing": "acp"}, set()),
    ({"missing": "acp"}, set()),
    ({"missing": ""}, set()),
    ({"surface": ""}, {"empty-surface"}),
    ({"surface": None}, {"empty-surface"}),
    ({"  surface  ": "cli"}, {"cli-a"}),
    ({"": "ignored", "surface": "cli"}, {"cli-a"}),
    ({"value": "1"}, {"text-one"}),
    ({"value": "1.5"}, set()),
    ({"value": "true"}, {"text-true"}),
    ({"value": "True"}, set()),
    ({"value": "false"}, set()),
    ({"value": "0"}, set()),
    ({"value": "null"}, {"text-null"}),
    ({"value": "None"}, set()),
    ({"value": ""}, set()),
    ({"value": '{"kind": "acp"}'}, set()),
    ({"it's": "yes"}, {"quote-key"}),
    ({"100%_done": "yes"}, {"like-key"}),
    ({"100%": "yes"}, set()),
    ({"$scope": "yes"}, {"dollar-key"}),
    ({"a b": "yes"}, {"space-key"}),
    ({"k') OR ('1'='1": "yes"}, {"sql-key"}),
    ({"k') OR ('1'='1": "no"}, set()),
    ({"note": 'say "hi" \\ C:\\work\\a'}, {"escaped-value"}),
    ({"note": "caf\u00e9 \U0001f600 \u2028"}, {"unicode-value"}),
    ({"note": "line\nbreak\ttab"}, {"newline-value"}),
    ({"note": "a%"}, {"like-value"}),
    ({"note": "a_"}, set()),
]


def _store_sessions(sessions: SessionStore, metadata_by_id: dict[str, Any]) -> None:
    for session_id, metadata in metadata_by_id.items():
        sessions.resolve_session(
            agent_id="main",
            channel="console",
            target=_TARGET,
            session_id=session_id,
            metadata=metadata,
        )


def _selected(sessions: SessionStore, metadata_filter: dict[str, Any]) -> set[str]:
    rows = sessions.list_sessions(
        limit=100, target=_TARGET, metadata_filter=metadata_filter
    )
    return {row.id for row in rows}


def check_metadata_filters(sessions: SessionStore) -> None:
    """Every filter selects the same sessions on SQLite and PostgreSQL."""
    _store_sessions(sessions, _SESSIONS)
    actual = [(item, _selected(sessions, item)) for item, _ in _EXPECTED]
    assert actual == _EXPECTED, (actual, _EXPECTED)
    unfiltered = _selected(sessions, {})
    assert unfiltered == set(_SESSIONS), (unfiltered, set(_SESSIONS))


def check_backend_reading_differences(
    sessions: SessionStore, *, postgres: bool
) -> None:
    """Where the backends read the stored metadata differently.

    SQLite's `json_extract` returns an object or array as its minified JSON
    text and reads the key as a JSON path. PostgreSQL reads the key as one
    top-level key and matches JSON strings only.
    """
    _store_sessions(
        sessions,
        {
            "object": {"value": {"kind": "acp"}},
            "array": {"value": [1, "x"]},
            "dotted-top": {"a.b": "top"},
            "dotted-nested": {"a": {"b": "nested"}},
            "bracket-top": {"tags[0]": "top"},
            "bracket-array": {"tags": ["first"]},
            "quoted-top": {'"q"': "top"},
            "quoted-plain": {"q": "plain"},
        },
    )
    filters: list[tuple[dict[str, Any], set[str], set[str]]] = [
        # filter, SQLite, PostgreSQL
        ({"value": '{"kind":"acp"}'}, {"object"}, set()),
        ({"value": '[1,"x"]'}, {"array"}, set()),
        ({"a.b": "top"}, set(), {"dotted-top"}),
        ({"a.b": "nested"}, {"dotted-nested"}, set()),
        ({"tags[0]": "top"}, set(), {"bracket-top"}),
        ({"tags[0]": "first"}, {"bracket-array"}, set()),
        ({'"q"': "top"}, set(), {"quoted-top"}),
        ({'"q"': "plain"}, {"quoted-plain"}, set()),
    ]
    expected = [
        (item, postgres_ids if postgres else sqlite_ids)
        for item, sqlite_ids, postgres_ids in filters
    ]
    actual = [(item, _selected(sessions, item)) for item, _, _ in filters]
    assert actual == expected, (actual, expected)


def _acp_runtime(sessions: SessionStore) -> Any:
    from openminion.base.config import OpenMinionConfig

    return SimpleNamespace(
        sessions=sessions,
        config=OpenMinionConfig.from_dict(
            {
                "agents": {"default": {"name": "default", "provider": "echo"}},
                "default_agent": "default",
            }
        ),
    )


def check_acp_session_list(sessions: SessionStore, root: Path) -> None:
    """ACP `session/list` lists the ACP sessions, filtered by `surface`."""
    pytest.importorskip("acp")
    from openminion.api.operations.acp import OpenMinionACPAgent

    workspace_a = root / "workspace-a"
    workspace_b = root / "workspace-b"
    workspace_a.mkdir(parents=True)
    workspace_b.mkdir(parents=True)
    agent = OpenMinionACPAgent(_acp_runtime(sessions))

    async def flow() -> tuple[list[str], list[str], list[str]]:
        await agent.initialize(protocol_version=1)
        first = await agent.new_session(cwd=str(workspace_a))
        second = await agent.new_session(cwd=str(workspace_b))
        for session_id, surface in (
            ("cli-in-a", "cli"),
            ("list-in-a", ["acp"]),
            ("true-in-a", True),
            ("no-surface-in-a", None),
        ):
            metadata: dict[str, Any] = {"workspace_root": str(workspace_a.resolve())}
            if surface is not None:
                metadata["surface"] = surface
            sessions.resolve_session(
                agent_id="default",
                channel="console",
                target="local-client",
                session_id=session_id,
                metadata=metadata,
            )
        in_a = await agent.list_sessions(cwd=str(workspace_a))
        every = await agent.list_sessions()
        return (
            [first.session_id, second.session_id],
            [item.session_id for item in in_a.sessions],
            sorted(item.session_id for item in every.sessions),
        )

    created, in_a, every = asyncio.run(flow())
    actual = (in_a, every)
    expected = ([created[0]], sorted(created))
    assert actual == expected, (actual, expected)


class _FocusGateway:
    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id


class _FocusRuntime:
    """The parts of `APIRuntime` that Focus session creation and listing use."""

    def __init__(self, sessions: SessionStore) -> None:
        self.sessions = sessions
        self.config = SimpleNamespace(
            default_agent="alpha",
            runtime=SimpleNamespace(session_context_token_budget=200000),
            agents={
                name: SimpleNamespace(name=name, provider="echo", default_channel="cli")
                for name in ("alpha", "beta")
            },
        )

    def resolve_agent_profile(self, agent_id: str | None = None) -> SimpleNamespace:
        return SimpleNamespace(name=str(agent_id or "").strip() or "alpha")

    def resolve_gateway(
        self, agent_id: str | None = None, *, overrides: object | None = None
    ) -> _FocusGateway:
        del overrides
        return _FocusGateway(str(agent_id or "").strip() or "alpha")


def check_focus_directory_sessions(sessions: SessionStore, root: Path) -> None:
    """Focus lists the current agent's sessions of its working directory."""
    from openminion.cli.interactive.runtime import OpenMinionRuntime

    rt = _FocusRuntime(sessions)
    project_a = root / "project-a"
    project_b = root / "project-b"

    def focus(working_dir: Path, agent_id: str | None = None) -> Any:
        return OpenMinionRuntime(
            rt,
            channel="cli",
            target="focus",
            agent_id=agent_id,
            working_dir=str(working_dir),
            bind_immediately=False,
        )

    focus_a = focus(project_a)
    first = focus_a.create_new_session()
    second = focus_a.create_new_session()
    focus(project_b).create_new_session()
    focus(project_a, agent_id="beta").create_new_session()
    stored = sessions.get_session(first)
    assert stored is not None
    working_dir = stored.metadata.get("working_dir")
    focus_mode = stored.metadata.get("focus_mode")
    listed = [item.id for item in focus_a.list_directory_sessions()]
    candidate = focus_a.find_candidate_session()
    actual = (
        working_dir,
        focus_mode,
        sorted(listed),
        candidate.id if candidate is not None else None,
    )
    expected = (
        str(project_a.resolve()),
        True,
        sorted([first, second]),
        listed[0] if listed else None,
    )
    assert actual == expected, (actual, expected)
    assert candidate is not None and candidate.id in {first, second}, candidate
