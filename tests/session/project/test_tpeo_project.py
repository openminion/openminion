from __future__ import annotations

import os
import tempfile

import pytest

from openminion.modules.session.project import (
    Project,
    SQLiteProjectStore,
)
from openminion.modules.session.project.binding import resolve_inheritance
from openminion.modules.session.project.cli import dispatch_project_command
from openminion.modules.session.project.cron_binding import (
    resolve_cron_project_binding,
)
from openminion.modules.session.project.schemas import ProjectSessionBinding


def _make_store():
    tmp = tempfile.TemporaryDirectory()
    store = SQLiteProjectStore(os.path.join(tmp.name, "proj.db"))
    return store, tmp


def _make_project(project_id="p1", name="Demo"):
    return Project(
        project_id=project_id,
        name=name,
        master_instruction="hold context across sessions",
        skill_set=["coding", "research"],
        scheduled_triggers=["cron-daily"],
    )


# --- Schema (TPEO-01) ---


def test_project_schema_dedupes_skill_set_and_triggers():
    p = Project(
        project_id="p1",
        name="x",
        skill_set=["coding", "coding", " research "],
        scheduled_triggers=["c", "c"],
    )
    assert p.skill_set == ["coding", "research"]
    assert p.scheduled_triggers == ["c"]


def test_project_schema_memory_scope_key_uses_canonical_vocabulary():
    p = _make_project("proj-xyz")
    assert p.memory_scope_key() == "project:proj-xyz"


def test_project_session_binding_strip_required_fields():
    b = ProjectSessionBinding(project_id="  p1  ", session_id=" s1 ")
    assert b.project_id == "p1"
    assert b.session_id == "s1"


def test_project_schema_rejects_empty_project_id():
    with pytest.raises(Exception):
        Project(project_id="", name="x")


# --- Storage (TPEO-02) ---


def test_store_create_then_get_round_trips():
    store, tmp = _make_store()
    try:
        p = _make_project()
        store.create(p)
        loaded = store.get(p.project_id)
        assert loaded is not None
        assert loaded.name == p.name
        assert loaded.skill_set == p.skill_set
        assert loaded.scheduled_triggers == p.scheduled_triggers
    finally:
        tmp.cleanup()


def test_store_list_returns_all_projects_in_creation_order():
    store, tmp = _make_store()
    try:
        store.create(_make_project("p1", "A"))
        store.create(_make_project("p2", "B"))
        names = [p.name for p in store.list()]
        assert names == ["A", "B"]
    finally:
        tmp.cleanup()


def test_store_delete_removes_project_and_bindings():
    store, tmp = _make_store()
    try:
        p = _make_project()
        store.create(p)
        store.bind_session(p.project_id, "s1")
        assert store.delete(p.project_id) is True
        assert store.get(p.project_id) is None
        assert store.list_bindings_for_project(p.project_id) == []
    finally:
        tmp.cleanup()


def test_store_delete_unknown_returns_false():
    store, tmp = _make_store()
    try:
        assert store.delete("nope") is False
    finally:
        tmp.cleanup()


# --- Binding (TPEO-03) ---


def test_bind_session_then_project_for_session_resolves():
    store, tmp = _make_store()
    try:
        p = _make_project()
        store.create(p)
        binding = store.bind_session(p.project_id, "sess-1")
        assert binding.project_id == p.project_id
        resolved = store.project_for_session("sess-1")
        assert resolved is not None
        assert resolved.project_id == p.project_id
    finally:
        tmp.cleanup()


def test_resolve_inheritance_returns_typed_payload():
    store, tmp = _make_store()
    try:
        p = _make_project()
        store.create(p)
        store.bind_session(p.project_id, "sess-1")
        inh = resolve_inheritance(store, session_id="sess-1")
        assert inh is not None
        assert inh.project_id == p.project_id
        assert inh.master_instruction == p.master_instruction
        assert inh.skill_set == tuple(p.skill_set)
        assert inh.scope_key == f"project:{p.project_id}"
        assert inh.scheduled_triggers == tuple(p.scheduled_triggers)
    finally:
        tmp.cleanup()


def test_resolve_inheritance_returns_none_for_unbound_session():
    store, tmp = _make_store()
    try:
        store.create(_make_project())
        assert resolve_inheritance(store, session_id="unbound") is None
    finally:
        tmp.cleanup()


def test_bind_session_is_upsert_on_session_id():
    store, tmp = _make_store()
    try:
        store.create(_make_project("p1"))
        store.create(_make_project("p2"))
        store.bind_session("p1", "s1")
        store.bind_session("p2", "s1")  # rebind same session_id
        resolved = store.project_for_session("s1")
        assert resolved is not None
        assert resolved.project_id == "p2"
    finally:
        tmp.cleanup()


# --- CLI (TPEO-04) ---


def test_cli_create_then_list_round_trips():
    store, tmp = _make_store()
    try:
        result = dispatch_project_command(
            store,
            ["create", "MyProj", "--instruction", "stay focused", "--skill", "coding"],
        )
        assert result["ok"] is True
        proj_id = result["project"]["project_id"]
        listed = dispatch_project_command(store, ["list"])
        assert listed["ok"] is True
        assert any(p["project_id"] == proj_id for p in listed["projects"])
    finally:
        tmp.cleanup()


def test_cli_show_returns_project_and_bindings():
    store, tmp = _make_store()
    try:
        p = _make_project()
        store.create(p)
        store.bind_session(p.project_id, "s1")
        result = dispatch_project_command(store, ["show", p.project_id])
        assert result["ok"] is True
        assert result["project"]["project_id"] == p.project_id
        assert len(result["bindings"]) == 1
    finally:
        tmp.cleanup()


def test_cli_bind_session_errors_for_unknown_project():
    store, tmp = _make_store()
    try:
        result = dispatch_project_command(store, ["bind-session", "no-such", "sess"])
        assert result["ok"] is False
        assert result["error"] == "project_not_found"
    finally:
        tmp.cleanup()


def test_cli_usage_for_empty_args():
    store, tmp = _make_store()
    try:
        result = dispatch_project_command(store, [])
        assert result["ok"] is False
        assert "usage" in result
    finally:
        tmp.cleanup()


def test_cli_unknown_subcommand_returns_error():
    store, tmp = _make_store()
    try:
        result = dispatch_project_command(store, ["banana"])
        assert result["ok"] is False
        assert result["error"] == "unknown_subcommand"
    finally:
        tmp.cleanup()


# --- Cron binding (TPEO-05) ---


def test_resolve_cron_project_binding_returns_typed_binding():
    store, tmp = _make_store()
    try:
        p = _make_project()
        store.create(p)
        binding = resolve_cron_project_binding(
            store, {"entry_id": "cron-1", "project_id": p.project_id}
        )
        assert binding is not None
        assert binding.cron_entry_id == "cron-1"
        assert binding.inheritance.project_id == p.project_id
    finally:
        tmp.cleanup()


def test_resolve_cron_project_binding_returns_none_when_no_project_id():
    store, tmp = _make_store()
    try:
        assert resolve_cron_project_binding(store, {"entry_id": "x"}) is None
    finally:
        tmp.cleanup()


def test_resolve_cron_project_binding_returns_none_for_unknown_project():
    store, tmp = _make_store()
    try:
        result = resolve_cron_project_binding(
            store, {"entry_id": "c", "project_id": "ghost"}
        )
        assert result is None
    finally:
        tmp.cleanup()


# --- TPEO-06 memory scope reconciliation audit ---


def test_project_scope_key_matches_canonical_project_vocabulary():

    p = Project(project_id="audit-id", name="X")
    assert p.memory_scope_key().startswith("project:")
    assert p.memory_scope_key() == "project:audit-id"


def test_inheritance_scope_key_is_project_scoped():

    store, tmp = _make_store()
    try:
        p = _make_project()
        store.create(p)
        store.bind_session(p.project_id, "sess-1")
        inh = resolve_inheritance(store, session_id="sess-1")
        assert inh is not None
        assert inh.scope_key.startswith("project:")
    finally:
        tmp.cleanup()


# --- TPEO-08 E2E smoke ---


def test_e2e_smoke_create_bind_inherit_cron_inspect():

    store, tmp = _make_store()
    try:
        # Surface 1: CLI create
        created = dispatch_project_command(
            store,
            [
                "create",
                "AutonomyProj",
                "--instruction",
                "ship lanes",
                "--skill",
                "coding",
                "--skill",
                "research",
                "--trigger",
                "cron-daily",
            ],
        )
        project_id = created["project"]["project_id"]

        # Surface 2: bind session via CLI
        bind = dispatch_project_command(
            store, ["bind-session", project_id, "smoke-sess"]
        )
        assert bind["ok"] is True

        # Surface 3: session inherits via binding service
        inh = resolve_inheritance(store, session_id="smoke-sess")
        assert inh is not None
        assert inh.master_instruction == "ship lanes"
        assert "coding" in inh.skill_set
        assert inh.scope_key == f"project:{project_id}"

        # Surface 4: cron entry resolves the project
        cron_binding = resolve_cron_project_binding(
            store, {"entry_id": "cron-daily", "project_id": project_id}
        )
        assert cron_binding is not None
        assert cron_binding.inheritance.scope_key == inh.scope_key

        # Surface 5: operator inspection via CLI
        shown = dispatch_project_command(store, ["show", project_id])
        assert shown["ok"] is True
        assert len(shown["bindings"]) == 1
        assert shown["bindings"][0]["session_id"] == "smoke-sess"
    finally:
        tmp.cleanup()
