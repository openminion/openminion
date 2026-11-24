"""PostgreSQL schema per owner (#446): the decisions on catalog layouts, without a server.

The layouts are built from the listed histories in ``postgres_histories.json``; the same cases run
against a real PostgreSQL server in ``tests/integration/test_postgres_module_schemas.py``.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from openminion.modules.storage.migrations import postgres_schema as ps
from openminion.modules.storage.migrations.errors import SchemaOwnershipError

MODULES = ("memory", "a2a", "session", "registry", "artifact", "secret")


def _history(owner: str, ledger: str, *, store: bool = True) -> ps.History:
    items = ps._catalog().histories[owner][ledger]
    current = [h for h in items if "current" in h.measured]
    return sorted(current, key=lambda h: len(h.tables), reverse=store)[0]


def _runtime_rows(k: int) -> tuple[tuple[int, str], ...]:
    names = ps._catalog().runtime_versions
    return tuple((v, names[v]) for v in range(1, k + 1))


def _layout(name: str = "public", **kwargs) -> ps.SchemaLayout:
    tables = dict(kwargs.pop("tables", {}))
    kwargs.setdefault("objects", len(tables))
    return ps.SchemaLayout(
        name=name,
        exists=True,
        tables=tables,
        owner="app",
        usage=True,
        create=True,
        **kwargs,
    )


def _runtime(k: int = 15, **extra: str) -> dict[str, str]:
    return {**_history("runtime", f"v{k}").tables, **extra}


def _memory_meta() -> dict[str, str]:
    return {"module_id": "memory", "schema_head": "0004_namespace"}


def _a2a_meta() -> dict[str, str]:
    return {"module_id": "a2a", "schema_head": "0005_job_idempotency_scope"}


def _with_column(sig: str) -> str:
    return sig.replace("; pk(", ", added_by_hand text; pk(")


def _engine(**changed: str) -> dict[str, str]:
    """The storage engine's tables, as this code creates them next to a store's tables."""
    listed = ps._catalog().engine_tables
    return {t: changed.get(t, sorted(sigs)[0]) for t, sigs in listed.items()}


FOREIGN_CORE_EVENTS = "foreign_payload text; pk()"


def _decide(
    module_id: str, base: ps.SchemaLayout, derived: ps.SchemaLayout | None = None
):
    derived = derived or ps.SchemaLayout(name=f"{base.name}__{module_id}", exists=False)
    return ps.decide_module_schema(
        module_id, base, derived, role="app", database_create=True
    )


def _outcome(
    module_id: str, base: ps.SchemaLayout, derived: ps.SchemaLayout | None = None
) -> str:
    try:
        selection = _decide(module_id, base, derived)
    except SchemaOwnershipError:
        return "refused"
    return "in place" if selection.in_place else selection.schema


def _refusal(
    module_id: str, base: ps.SchemaLayout, derived: ps.SchemaLayout | None = None
) -> str:
    with pytest.raises(SchemaOwnershipError) as refused:
        _decide(module_id, base, derived)
    message = str(refused.value)
    assert "Nothing was written" in message
    return message


def test_listed_histories_name_their_tables_and_sources() -> None:
    catalog = ps._catalog()
    for owner, ledgers in catalog.histories.items():
        assert owner == ps.RUNTIME or owner in catalog.names
        for ledger, items in ledgers.items():
            for history in items:
                assert history.measured, (owner, ledger)
                assert all(";" in sig for sig in history.tables.values())
    assert set(catalog.engine_tables) == {
        "core_events",
        "core_sidecar_rows",
        "sidecar_ingest_log",
    }
    assert all(
        sigs and all(";" in sig for sig in sigs)
        for sigs in catalog.engine_tables.values()
    )


def test_listed_histories_match_this_code() -> None:
    """The file lists this code's runtime versions and every revision of each Alembic chain."""
    from alembic.script import ScriptDirectory

    from openminion.modules.storage.migrations.alembic import (
        discover_module_alembic_paths,
    )
    from openminion.modules.storage.runtime.migrations import DEFAULT_MIGRATIONS

    catalog = ps._catalog()
    assert catalog.runtime_versions == {m.version: m.name for m in DEFAULT_MIGRATIONS}
    assert set(catalog.histories[ps.RUNTIME]) == {
        f"v{m.version}" for m in DEFAULT_MIGRATIONS
    }
    for owner, ledgers in catalog.histories.items():
        if owner == ps.RUNTIME:
            continue
        _, location = discover_module_alembic_paths(owner)
        assert location is not None, owner
        chain = ScriptDirectory(str(location))
        current = {
            ledger
            for ledger, items in ledgers.items()
            if ledger.startswith("alembic ")
            and any("current" in h.measured for h in items)
        }
        # Every revision of the chain; "alembic base" (no table) where it is listed.
        assert current - {"alembic base"} == {
            f"alembic {s.revision}" for s in chain.walk_revisions()
        }
        assert ps.alembic_head(owner) == f"alembic {chain.get_current_head()}"


def test_fresh_base_schema_runtime_proceeds_and_modules_create_their_own() -> None:
    base = _layout()

    assert ps.runtime_refusal(base) is None
    for module_id in MODULES:
        selection = _decide(module_id, base)
        assert (selection.schema, selection.create) == (f"public__{module_id}", True)


def test_runtime_history_keeps_its_tables_and_modules_go_to_their_schema() -> None:
    base = _layout(tables=_runtime(15), migrations=_runtime_rows(15))

    assert ps.runtime_refusal(base) is None
    # sessions, events, idempotency_keys, memory_records and migrations are runtime
    # storage's here: its ledger and its exact v15 history prove it.
    assert {m: _outcome(m, base) for m in MODULES} == {
        m: f"public__{m}" for m in MODULES
    }


def test_runtime_history_from_an_earlier_version_proceeds() -> None:
    base = _layout(tables=_runtime(11), migrations=_runtime_rows(11))

    assert ps.runtime_refusal(base) is None


def test_dedicated_module_history_stays_in_place_and_runtime_is_refused() -> None:
    memory = _history("memory", "alembic 0004_namespace")
    base = _layout(
        tables=memory.tables,
        alembic=("0004_namespace",),
        om_meta=_memory_meta(),
        migrations=(),
    )

    assert _decide("memory", base) == ps.ModuleSchema(
        "memory", "public", "public", ledger="alembic 0004_namespace"
    )
    message = ps.runtime_refusal(base)
    assert message is not None
    assert "no runtime storage ledger is recorded there" in message
    assert "memory_records is recorded by memory alembic 0004_namespace" in message
    assert "Nothing was written" in message
    assert _outcome("session", base) == "public__session"


def test_a_table_two_recorded_histories_include_refuses_both_owners() -> None:
    # Memory first, then runtime storage, which committed v1..v3 and failed at v4:
    # memory's empty migrations table now holds runtime storage's ledger rows.
    memory = _history("memory", "alembic 0004_namespace")
    base = _layout(
        tables={**memory.tables, **_runtime(3)},
        alembic=("0004_namespace",),
        om_meta=_memory_meta(),
        migrations=_runtime_rows(3),
    )

    shared = (
        "migrations is recorded by runtime storage v3 and memory alembic 0004_namespace"
    )
    runtime = ps.runtime_refusal(base)
    assert runtime is not None and shared in runtime
    assert shared in _refusal("memory", base)
    # Owners that do not touch those names are not refused.
    assert _outcome("registry", base) == "public__registry"
    assert _outcome("session", base) == "public__session"


def test_an_empty_migrations_table_stays_refused() -> None:
    # A2A first, then a runtime storage start that failed before v1.
    a2a = _history("a2a", "alembic 0005_job_idempotency_scope")
    base = _layout(
        tables={**a2a.tables, "migrations": _runtime(1)["migrations"]},
        alembic=("0005_job_idempotency_scope",),
        om_meta=_a2a_meta(),
        migrations=(),
    )

    assert ps.runtime_refusal(base) is not None
    message = _refusal("memory", base)
    assert "no memory ledger is recorded there" in message
    assert (
        "migrations matches no recorded ledger (its columns are listed for memory, "
        "runtime storage)" in message
    )
    assert _outcome("a2a", base) == "in place"
    assert _outcome("artifact", base) == "public__artifact"


def test_a_ledger_that_claims_another_owners_table_is_refused() -> None:
    # Runtime storage, then the A2A audit store on the same URL: A2A's ledger records
    # 0005, but idempotency_keys is runtime storage's.
    a2a = _history("a2a", "alembic 0005_job_idempotency_scope")
    tables = {t: s for t, s in a2a.tables.items() if t != "idempotency_keys"}
    base = _layout(
        tables={**_runtime(15), **tables},
        alembic=("0005_job_idempotency_scope",),
        om_meta=_a2a_meta(),
        migrations=_runtime_rows(15),
    )

    assert ps.runtime_refusal(base) is None
    assert "idempotency_keys is recorded by runtime storage v15" in _refusal(
        "a2a", base
    )
    assert _outcome("memory", base) == "public__memory"
    # registry shares the name agents, which A2A's ledger records with exactly these
    # columns: the table is A2A's, so registry uses its own schema.
    assert _outcome("registry", base) == "public__registry"


def test_an_unproven_owner_does_not_refuse_an_owner_of_its_table_names() -> None:
    # A2A in place with one altered table: A2A is refused; agents is still exactly
    # the table A2A's ledger records, so registry is not.
    a2a = dict(_history("a2a", "alembic 0005_job_idempotency_scope").tables)
    a2a["jobs"] = _with_column(a2a["jobs"])
    base = _layout(
        tables=a2a, alembic=("0005_job_idempotency_scope",), om_meta=_a2a_meta()
    )

    assert "jobs has other columns than a2a alembic 0005_job_idempotency_scope" in (
        _refusal("a2a", base)
    )
    assert _outcome("registry", base) == "public__registry"


@pytest.mark.parametrize(
    ("change", "expected", "session"),
    [
        ("extra column", "sessions has other columns", "refused"),
        ("missing column", "sessions has other columns", "refused"),
        ("missing table", "room_participants is missing", "public__session"),
    ],
)
def test_partial_subset_or_superset_tables_are_not_evidence(
    change: str, expected: str, session: str
) -> None:
    tables = _runtime(15)
    if change == "extra column":
        tables["sessions"] = _with_column(tables["sessions"])
    elif change == "missing column":
        tables["sessions"] = tables["sessions"].replace("channel text NOT NULL, ", "")
    else:
        del tables["room_participants"]
    base = _layout(tables=tables, migrations=_runtime_rows(15))

    message = ps.runtime_refusal(base)
    assert message is not None and expected in message
    # Only the owners that use a refused table name are refused: session uses
    # sessions; memory and A2A use runtime storage's other names, which its ledger
    # still records exactly.
    assert _outcome("session", base) == session
    assert {m: _outcome(m, base) for m in ("memory", "a2a", "registry")} == {
        "memory": "public__memory",
        "a2a": "public__a2a",
        "registry": "public__registry",
    }


def test_a_module_already_in_its_own_schema_keeps_it() -> None:
    # Runtime storage's sessions was altered after the session and memory modules moved
    # to their own schemas: they keep them, and never read the base tables.
    tables = _runtime(15)
    tables["sessions"] = _with_column(tables["sessions"])
    base = _layout(tables=tables, migrations=_runtime_rows(15))
    session = _history("session", "alembic 0005_cron_scheduler_control")
    memory = _history("memory", "alembic 0004_namespace")
    derived = {
        "session": _layout(
            "public__session",
            tables=session.tables,
            alembic=("0005_cron_scheduler_control",),
            om_meta={"module_id": "session", "schema_head": "x"},
        ),
        "memory": _layout(
            "public__memory",
            tables=memory.tables,
            alembic=("0004_namespace",),
            om_meta=_memory_meta(),
            migrations=(),
        ),
    }

    for module_id, layout in derived.items():
        assert _decide(module_id, base, layout) == ps.ModuleSchema(
            module_id,
            "public",
            f"public__{module_id}",
            ledger=layout.ledger(module_id),
        )
    # Without its history there, the module is refused as before.
    assert (
        "holds tables with its names that are not another owner's alone (sessions), "
        'and "public__session" also exists and is empty; neither is selected'
    ) in _refusal("session", base, _layout("public__session", objects=0))

    # Base tables with names only session uses are its own rows without their ledger:
    # it is refused rather than switched away from them.
    legacy = {t: session.tables[t] for t in ("cron_jobs", "message_refs")}
    message = _refusal(
        "session",
        _layout(tables={**tables, **legacy}, migrations=_runtime_rows(15)),
        derived["session"],
    )
    assert (
        "not another owner's alone (cron_jobs, message_refs, sessions)" in message
        and "holds its proven alembic 0005_cron_scheduler_control history" in message
        and "neither is selected" in message
    )


def test_tables_without_a_ledger_are_not_evidence() -> None:
    # Two of runtime storage's tables, exact, with no migrations rows: their columns
    # match runtime storage v10..v15 and the session module, which proves neither.
    runtime = _runtime(15)
    base = _layout(
        tables={
            t: runtime[t] for t in ("session_turn_leases", "session_retention_holds")
        }
    )

    message = ps.runtime_refusal(base)
    assert message is not None and "no runtime storage ledger" in message
    assert "no session ledger" in _refusal("session", base)


def test_unknown_tables_with_owner_names_are_refused() -> None:
    base = _layout(tables={"sessions": "id integer NOT NULL; pk(id)"})

    fact = "sessions matches no recorded ledger (its columns match no listed history)"
    message = ps.runtime_refusal(base)
    assert message is not None and fact in message
    assert fact in _refusal("session", base)
    assert _outcome("memory", base) == "public__memory"


def test_a_shared_ledger_refuses_the_owners_it_does_not_prove() -> None:
    # `storage migrate --module artifact`, then `--module registry`: the shared
    # alembic_version already said 0001_baseline, so registry's tables were never
    # created and om_meta names registry.
    artifact = _history("artifact", "alembic 0001_baseline")
    base = _layout(
        tables=artifact.tables,
        alembic=("0001_baseline",),
        om_meta={"module_id": "registry", "schema_head": "0001_baseline"},
    )

    assert "agents is missing" in _refusal("registry", base)
    assert "no artifact ledger" in _refusal("artifact", base)
    assert ps.runtime_refusal(base) is None
    assert _outcome("secret", base) == "public__secret"


def test_both_locations_are_refused() -> None:
    memory = _history("memory", "alembic 0004_namespace")
    base = _layout(
        tables=memory.tables,
        alembic=("0004_namespace",),
        om_meta=_memory_meta(),
        migrations=(),
    )
    derived = _layout("public__memory", objects=0)

    assert "neither is selected" in _refusal("memory", base, derived)


def test_existing_derived_schema_rules() -> None:
    base = _layout()
    memory = _history("memory", "alembic 0004_namespace")

    empty = _layout("public__memory", objects=0)
    assert _decide("memory", base, empty) == ps.ModuleSchema(
        "memory", "public", "public__memory"
    )
    own = _layout(
        "public__memory",
        tables=memory.tables,
        alembic=("0004_namespace",),
        om_meta=_memory_meta(),
        migrations=(),
    )
    assert _decide("memory", base, own).schema == "public__memory"

    refusals = {
        "holds tables that are not memory's: notes": _layout(
            "public__memory", tables={"notes": None}
        ),
        "holds objects but no table": _layout("public__memory", objects=2),
        "records another owner's ledger: om_meta names 'a2a'": _layout(
            "public__memory",
            tables=memory.tables,
            alembic=("0004_namespace",),
            om_meta={"module_id": "a2a"},
        ),
        "records another owner's ledger: migrations holds runtime storage": _layout(
            "public__memory",
            tables={"migrations": _runtime(1)["migrations"]},
            migrations=_runtime_rows(1),
        ),
        "memory_records has other columns than memory alembic 0004_namespace lists": (
            _layout(
                "public__memory",
                tables={**memory.tables, "memory_records": "id text NOT NULL; pk(id)"},
                alembic=("0004_namespace",),
                om_meta=_memory_meta(),
            )
        ),
        "holds tables (memory_records) but records no memory ledger": _layout(
            "public__memory",
            tables={"memory_records": memory.tables["memory_records"]},
        ),
        'GRANT USAGE, CREATE ON SCHEMA "public__memory" TO "app"': ps.SchemaLayout(
            name="public__memory", exists=True, owner="postgres", create=True
        ),
    }
    for fragment, derived in refusals.items():
        assert fragment in _refusal("memory", base, derived), fragment


@pytest.mark.parametrize(
    "case",
    [
        "one table of the store",
        "a foreign storage engine table",
        "an exact storage engine table",
        "a first start stopped before om_meta",
    ],
)
def test_a_module_schema_without_its_ledger_is_refused(case: str) -> None:
    # Some of a store's tables, a storage engine table with foreign or exact columns, and
    # a first start stopped before om_meta all lack the module's ledger. Exact columns
    # cannot tell a copy or a partial start from the module's own history, so each is
    # refused.
    registry = _history("registry", "store 0001_baseline")
    tables = {
        "one table of the store": lambda: {
            "agent_methods": registry.tables["agent_methods"]
        },
        "a foreign storage engine table": lambda: {"core_events": FOREIGN_CORE_EVENTS},
        "an exact storage engine table": lambda: {
            "core_events": _engine()["core_events"]
        },
        "a first start stopped before om_meta": lambda: {
            **registry.tables,
            **_engine(),
        },
    }[case]()
    derived = _layout("public__registry", tables=tables)

    message = _refusal("registry", _layout(), derived)
    assert (
        f'"public__registry" holds tables ({", ".join(sorted(tables))}) but records '
        "no registry ledger" in message
    )
    assert "take a full pg_dump" in message


def test_a_module_schema_at_alembic_base_is_used_where_listed() -> None:
    # `storage migrate`, then the module's Alembic chain downgraded to base: only its
    # ledger tables are left, om_meta naming it and alembic_version without a row.
    def at_base(module_id: str) -> ps.SchemaLayout:
        return _layout(
            f"public__{module_id}",
            tables={
                "alembic_version": "version_num character varying(32) NOT NULL; "
                "pk(version_num)",
                "om_meta": "key text NOT NULL, value text NOT NULL; pk(key)",
            },
            alembic=(),
            om_meta={"module_id": module_id, "schema_head": "head"},
        )

    assert _decide("controlplane", _layout(), at_base("controlplane")) == (
        ps.ModuleSchema(
            "controlplane", "public", "public__controlplane", ledger="alembic base"
        )
    )
    # The memory downgrade does not run on PostgreSQL: its base is not listed.
    assert "its memory ledger records alembic base, which is not a listed history" in (
        _refusal("memory", _layout(), at_base("memory"))
    )


def test_a_proven_module_schema_with_exact_engine_tables_is_used() -> None:
    registry = _history("registry", "store 0001_baseline")
    meta = {"module_id": "registry", "schema_head": "0001_baseline"}
    own = _layout(
        "public__registry", tables={**registry.tables, **_engine()}, om_meta=meta
    )

    assert _decide("registry", _layout(), own) == ps.ModuleSchema(
        "registry", "public", "public__registry", ledger="store 0001_baseline"
    )
    # The storage engine's tables are held to their exact columns too.
    foreign = _layout(
        "public__registry",
        tables={**registry.tables, **_engine(core_events=FOREIGN_CORE_EVENTS)},
        om_meta=meta,
    )
    assert (
        '"public__registry" holds core_events with other columns than this code\'s '
        "storage engine creates"
    ) in _refusal("registry", _layout(), foreign)


def test_privilege_and_name_refusals_name_the_fix() -> None:
    base = _layout()
    with pytest.raises(SchemaOwnershipError) as no_create:
        ps.decide_module_schema(
            "memory",
            base,
            ps.SchemaLayout(name="public__memory", exists=False),
            role="app",
            database_create=False,
        )
    assert 'CREATE SCHEMA "public__memory" AUTHORIZATION "app"' in str(no_create.value)

    with pytest.raises(SchemaOwnershipError) as grant:
        _decide("memory", base, ps.SchemaLayout(name="public__memory", exists=True))
    assert 'GRANT USAGE, CREATE ON SCHEMA "public__memory" TO "app"' in str(grant.value)

    for name in ("App-Base", "b" * 60):
        with pytest.raises(
            SchemaOwnershipError, match="needs its own PostgreSQL schema"
        ):
            _decide("memory", _layout(name))


def test_module_url_appends_the_schema_and_keeps_every_other_option(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy.engine import make_url

    monkeypatch.setenv("PGOPTIONS", "-c statement_timeout=5000")
    url = (
        "postgresql+psycopg://app:secret@db:5432/om?application_name=om"
        "&options=-c%20work_mem%3D4MB&sslmode=require"
    )

    query = make_url(ps.module_url(url, "AppBase__memory")).query
    assert query["application_name"] == "om"
    assert query["sslmode"] == "require"
    assert query["options"] == '-c work_mem=4MB -c search_path="AppBase__memory"'

    plain = make_url(ps.module_url("postgresql+psycopg://app@db/om", "public__a2a"))
    assert plain.query["options"] == (
        '-c statement_timeout=5000 -c search_path="public__a2a"'
    )
    assert ps.ModuleSchema("a2a", "public", "public").url(url) == url

    with pytest.raises(SchemaOwnershipError, match="repeats the options"):
        ps.module_url(url + "&options=-c%20x%3D1", "public__a2a")


@pytest.mark.parametrize(
    "table",
    ["core_events", "core_sidecar_rows", "sidecar_ingest_log"],
)
def test_runtime_and_in_place_owners_check_base_engine_tables(table: str) -> None:
    foreign_only = _layout(tables={table: FOREIGN_CORE_EVENTS})
    message = ps.runtime_refusal(foreign_only)
    assert message is not None
    assert "no runtime storage ledger is recorded there" in message
    assert (
        f"{table} has other columns than this code's storage engine creates" in message
    )
    assert "Nothing was written" in message
    assert _outcome("memory", foreign_only) == "public__memory"
    assert _outcome("registry", foreign_only) == "public__registry"

    exact_without_runtime = _layout(tables={table: _engine()[table]})
    unowned_msg = ps.runtime_refusal(exact_without_runtime)
    assert unowned_msg is not None
    assert "no runtime storage ledger is recorded there" in unowned_msg
    assert f"{table} matches no recorded runtime storage ledger" in unowned_msg
    assert "Nothing was written" in unowned_msg

    artifact = _history("artifact", "alembic 0001_baseline")
    dedicated_store = _layout(
        tables={**artifact.tables, **_engine()},
        alembic=("0001_baseline",),
        om_meta={"module_id": "artifact", "schema_head": "0001_baseline"},
    )
    assert _outcome("artifact", dedicated_store) == "in place"
    assert ps.runtime_refusal(dedicated_store) is not None

    proven_runtime_exact = _layout(
        tables={**_runtime(16), **_engine()},
        migrations=_runtime_rows(16),
    )
    assert ps.runtime_refusal(proven_runtime_exact) is None

    proven_runtime_bad = _layout(
        tables={**_runtime(16), **_engine(**{table: FOREIGN_CORE_EVENTS})},
        migrations=_runtime_rows(16),
    )
    bad_msg = ps.runtime_refusal(proven_runtime_bad)
    assert bad_msg is not None
    assert (
        f"{table} with other columns than this code's storage engine creates" in bad_msg
    )
    assert "Nothing was written" in bad_msg

    registry = _history("registry", "store 0001_baseline")
    in_place_bad = _layout(
        tables={**registry.tables, **_engine(**{table: FOREIGN_CORE_EVENTS})},
        om_meta={"module_id": "registry", "schema_head": "0001_baseline"},
    )
    assert (
        f"{table} with other columns than this code's storage engine creates"
        in _refusal("registry", in_place_bad)
    )


@pytest.mark.parametrize(
    ("sqlstate", "constraint_name"),
    [
        ("23505", "pg_namespace_nspname_index"),
        ("42P06", None),
    ],
)
def test_competing_schema_creation_maps_to_actionable_refusal(
    sqlstate: str,
    constraint_name: str | None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    from types import SimpleNamespace
    from sqlalchemy.exc import IntegrityError, ProgrammingError

    from openminion.modules.storage.migrations import runner as runner_mod
    from openminion.modules.storage.migrations.module_ids import (
        get_module_application_id,
    )
    from openminion.modules.storage.migrations.runner import MigrationRunner

    class _Orig(Exception):
        def __init__(self, code: str, constraint: str | None) -> None:
            super().__init__(code)
            self.sqlstate = code
            self.pgcode = code
            self.diag = SimpleNamespace(constraint_name=constraint)

    exc_cls = IntegrityError if sqlstate == "23505" else ProgrammingError
    db_err = exc_cls(
        'CREATE SCHEMA "public__memory"',
        {},
        _Orig(sqlstate, constraint_name),
    )

    class _Tx:
        def __init__(self) -> None:
            self.is_active = True
            self.rolled_back = False

        def rollback(self) -> None:
            self.rolled_back = True
            self.is_active = False

        def commit(self) -> None:
            self.is_active = False

    state = {"create_attempted": False}

    class _Conn:
        def __init__(self) -> None:
            self.tx = _Tx()
            self.began = False

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def begin(self) -> _Tx:
            self.began = True
            return self.tx

        def execute(self, statement, params=None):
            sql = str(statement)
            if "CREATE SCHEMA" in sql:
                state["create_attempted"] = True
                raise db_err
            if "SELECT to_regclass(:n)" in sql:
                present = "1" if state["create_attempted"] else None
                return SimpleNamespace(scalar=lambda: present)
            if "FROM " in sql and ".alembic_version" in sql:
                return SimpleNamespace(fetchone=lambda: ("0004_namespace",))
            if "FROM " in sql and ".om_meta" in sql:
                return iter(
                    [("module_id", "memory"), ("schema_head", "0004_namespace")]
                )
            del params
            return SimpleNamespace(
                scalar=lambda: 1 if "SELECT 1" in sql else None,
                scalar_one=lambda: "public__memory",
                fetchone=lambda: None,
            )

    conn = _Conn()
    selection = ps.ModuleSchema("memory", "public", "public__memory", create=True)
    with pytest.raises(SchemaOwnershipError) as refused:
        ps.enter_module_schema(cast(Any, conn), selection)
    assert "Module 'memory': PostgreSQL schema \"public__memory\"" in str(refused.value)
    assert "Nothing was written" in str(refused.value)

    state["create_attempted"] = False
    conns: list[_Conn] = []

    class _Engine:
        def connect(self) -> _Conn:
            created = _Conn()
            conns.append(created)
            return created

        def dispose(self) -> None:
            return None

    runner = MigrationRunner(
        module_id="memory",
        db_path=tmp_path / "memory.db",
        module_application_id=get_module_application_id("memory"),
        backend_type="postgres",
        engine=cast(Any, _Engine()),
        postgres_schema=selection,
    )
    report = runner.migrate()
    began_txs = [c.tx for c in conns if c.began]
    assert len(began_txs) == 1 and began_txs[0].rolled_back is True
    assert report.success is False
    assert report.rolled_back is True
    assert report.before.alembic_revision is None and report.before.om_meta == {}
    assert report.after.alembic_revision == "0004_namespace"
    assert report.after.om_meta == {
        "module_id": "memory",
        "schema_head": "0004_namespace",
    }
    assert report.error is not None and "Nothing was written" in report.error

    pre_conns: list[_Conn] = []

    class _PreEngine:
        def connect(self) -> _Conn:
            created = _Conn()
            pre_conns.append(created)
            return created

        def dispose(self) -> None:
            return None

    def _refuse_pre_begin(_engine, _module_id: str):
        raise SchemaOwnershipError(
            "Module 'memory': PostgreSQL schema \"public__memory\" holds unproven tables. "
            "Nothing was written."
        )

    monkeypatch.setattr(runner_mod, "select_module_schema", _refuse_pre_begin)
    pre_report = MigrationRunner(
        module_id="memory",
        db_path=tmp_path / "memory-pre.db",
        module_application_id=get_module_application_id("memory"),
        backend_type="postgres",
        engine=cast(Any, _PreEngine()),
        select_postgres_schema=True,
    ).migrate()
    assert [c for c in pre_conns if c.began] == []
    assert pre_report.success is False
    assert pre_report.rolled_back is False
    assert (
        pre_report.before.alembic_revision is None
        and pre_report.after.alembic_revision is None
    )
    assert pre_report.error is not None and "Nothing was written" in pre_report.error

    probe_state = {"rolled_back": False}

    class _CreateProbe:
        @staticmethod
        def begin():
            class _Ctx:
                def __enter__(self):
                    return _Conn()

                def __exit__(self, exc_type, *_args):
                    if exc_type is not None:
                        probe_state["rolled_back"] = True
                    return False

            return _Ctx()

        @staticmethod
        def dispose() -> None:
            return None

    monkeypatch.setattr(
        ps,
        "select_module_schema",
        lambda _engine, module_id: ps.ModuleSchema(
            module_id, "public", f"public__{module_id}", create=True
        ),
    )
    monkeypatch.setattr(ps, "_require_landing", lambda _url, _schema: None)
    import sqlalchemy

    monkeypatch.setattr(sqlalchemy, "create_engine", lambda *_a, **_k: _CreateProbe())
    with pytest.raises(SchemaOwnershipError) as store_refused:
        ps.select_postgres_module_schema(
            "postgresql+psycopg://app@db/om", "registry", create=True
        )
    assert probe_state["rolled_back"] is True
    assert "Module 'registry': PostgreSQL schema \"public__registry\"" in str(
        store_refused.value
    )
    assert "Nothing was written" in str(store_refused.value)


def test_non_competing_schema_creation_errors_propagate(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from types import SimpleNamespace
    import sqlalchemy
    from sqlalchemy.exc import IntegrityError, ProgrammingError

    from openminion.modules.storage.migrations.module_ids import (
        get_module_application_id,
    )
    from openminion.modules.storage.migrations.runner import MigrationRunner

    class _Orig(Exception):
        def __init__(
            self,
            code: str,
            constraint: str | None,
            *,
            has_diag: bool = True,
        ) -> None:
            super().__init__(f"db error {code}")
            self.sqlstate = code
            self.pgcode = code
            if has_diag:
                self.diag = SimpleNamespace(constraint_name=constraint)

    monkeypatch.setattr(
        ps,
        "select_module_schema",
        lambda _engine, module_id: ps.ModuleSchema(
            module_id, "public", f"public__{module_id}", create=True
        ),
    )
    monkeypatch.setattr(ps, "_require_landing", lambda _url, _schema: None)

    for err in (
        ProgrammingError('CREATE SCHEMA "public__memory"', {}, _Orig("42501", None)),
        IntegrityError('CREATE SCHEMA "public__memory"', {}, _Orig("23505", None)),
        IntegrityError('CREATE SCHEMA "public__memory"', {}, _Orig("23505", "")),
        IntegrityError(
            'CREATE SCHEMA "public__memory"', {}, _Orig("23505", None, has_diag=False)
        ),
        IntegrityError(
            'CREATE SCHEMA "public__memory"', {}, _Orig("23505", "other_unique_idx")
        ),
    ):

        class _Tx:
            def __init__(self) -> None:
                self.is_active = True
                self.rolled_back = False

            def rollback(self) -> None:
                self.rolled_back = True
                self.is_active = False

            def commit(self) -> None:
                self.is_active = False

        class _Conn:
            def __init__(self) -> None:
                self.tx = _Tx()

            def __enter__(self):
                return self

            def __exit__(self, *_args) -> None:
                return None

            def begin(self) -> _Tx:
                return self.tx

            @staticmethod
            def execute(statement, params=None):
                del params
                sql = str(statement)
                if "CREATE SCHEMA" in sql:
                    raise err
                return SimpleNamespace(
                    scalar=lambda: 1 if "SELECT 1" in sql else None,
                    scalar_one=lambda: "public__memory",
                    fetchone=lambda: None,
                )

        with pytest.raises(type(err)) as direct_exc:
            ps.enter_module_schema(
                cast(Any, _Conn()),
                ps.ModuleSchema("memory", "public", "public__memory", create=True),
            )
        assert not isinstance(direct_exc.value, SchemaOwnershipError)
        assert "another concurrent process" not in str(direct_exc.value)
        assert "start again" not in str(direct_exc.value)

        class _CreateProbe:
            @staticmethod
            def begin():
                class _Ctx:
                    def __enter__(self):
                        return _Conn()

                    def __exit__(self, *_args):
                        return False

                return _Ctx()

            @staticmethod
            def dispose() -> None:
                return None

        monkeypatch.setattr(
            sqlalchemy, "create_engine", lambda *_a, **_k: _CreateProbe()
        )
        with pytest.raises(type(err)) as probe_exc:
            ps.select_postgres_module_schema(
                "postgresql+psycopg://app@db/om", "registry", create=True
            )
        assert not isinstance(probe_exc.value, SchemaOwnershipError)
        assert "another concurrent process" not in str(probe_exc.value)
        assert "start again" not in str(probe_exc.value)

        class _Engine:
            @staticmethod
            def connect() -> _Conn:
                return _Conn()

            @staticmethod
            def dispose() -> None:
                return None

        report = MigrationRunner(
            module_id="memory",
            db_path=tmp_path / "memory-err.db",
            module_application_id=get_module_application_id("memory"),
            backend_type="postgres",
            engine=cast(Any, _Engine()),
            postgres_schema=ps.ModuleSchema(
                "memory", "public", "public__memory", create=True
            ),
        ).migrate()
        assert report.success is False
        assert report.rolled_back is True
        assert report.error is not None
        assert "another concurrent process" not in report.error
        assert "start again" not in report.error
