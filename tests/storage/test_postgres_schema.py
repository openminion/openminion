"""PostgreSQL schema per owner (#446): the decisions on catalog layouts, without a server.

The layouts are built from the listed histories in ``postgres_histories.json``; the same cases run
against a real PostgreSQL server in ``tests/integration/test_postgres_module_schemas.py``.
"""

from __future__ import annotations

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
    # The first two are the layouts of the review of f04b4eae; the last is every table a
    # registry store's first start creates, exact, without its om_meta row. Tables
    # without the module's ledger are not evidence, whatever their columns.
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
