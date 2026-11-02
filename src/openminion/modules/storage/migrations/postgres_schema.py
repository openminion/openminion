"""PostgreSQL schema selection for runtime storage and module stores (#446).

Runtime storage and every module used to write into the one schema the configured URL selects, so
they shared ``alembic_version`` and ``om_meta`` and adopted each other's tables through
``CREATE TABLE IF NOT EXISTS``. Now:

- runtime storage keeps that base schema, ``current_schema()`` of the configured URL;
- each module uses its own schema, ``<base>__<module id>``;
- a module whose history is already in the base schema keeps using it, with no move and no stamp.

The evidence is an owner's ledger together with exact tables. The ledger is the ``migrations``
rows v1..vk for runtime storage, and for a module ``om_meta.module_id`` with its
``alembic_version`` row (none at Alembic base), or with ``om_meta.schema_head`` for a store that
has no Alembic ledger.
A table belongs to an owner when that owner's ledger is recorded in the schema and a history
listed for that ledger in ``postgres_histories.json`` has the table with the same columns, types,
NOT NULL and primary key. An owner's history there is proven when every table of one listed
history is present, belongs to that owner alone, and the owner has no other table there. A table
an owner would create or read that is not proven its own is refused before any write; owners
that use none of the refused table names are not affected. An existing ``<base>__<module id>``
is used only when it is empty or holds the module's proven history; the storage engine's tables
there must then have the exact columns this code creates.

The checks read only catalogs and ledger rows, with explicit schema names, in one READ ONLY
transaction.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from openminion.modules.storage.constants import POSTGRES_MAX_IDENTIFIER_BYTES
from openminion.modules.storage.migrations.errors import SchemaOwnershipError

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection, Engine

    from openminion.modules.storage.engine import StorageEngineConfig

RUNTIME = "runtime"
_HISTORIES_FILE = Path(__file__).with_name("postgres_histories.json")
_LEDGER_TABLES = frozenset({"alembic_version", "om_meta"})
_INTEGER_TYPES = frozenset({"smallint", "integer", "bigint"})
_SAFE_NAME = re.compile(r"[A-Za-z0-9_]+")
_NOTHING_WRITTEN = (
    "Nothing was written. OpenMinion does not move, repair or restamp existing tables."
)
_GUIDANCE = (
    "Stop every OpenMinion process, take a full pg_dump, and follow "
    '"PostgreSQL schemas per owner" in docs/runtime-database-upgrades.md.'
)


@dataclass(frozen=True)
class History:
    """One supported layout of an owner: its ledger and its exact tables."""

    owner: str
    ledger: str  # "v3", "alembic 0004_namespace" or "store 0001_baseline"
    tables: dict[str, str]  # table -> signature
    measured: tuple[str, ...]


@dataclass(frozen=True)
class _Catalog:
    engine_tables: dict[str, frozenset[str]]  # storage engine table -> its signatures
    runtime_versions: dict[int, str]
    histories: dict[str, dict[str, tuple[History, ...]]]  # owner -> ledger -> histories
    names: dict[str, frozenset[str]]  # owner -> every table name in its histories
    signatures: dict[str, frozenset[str]]  # "owner/table" -> every listed signature


@lru_cache(maxsize=1)
def _catalog() -> _Catalog:
    data = json.loads(_HISTORIES_FILE.read_text(encoding="utf-8"))
    signatures: dict[str, str] = data["signatures"]
    by_owner: dict[str, dict[str, list[History]]] = {}
    for item in data["histories"]:
        history = History(
            owner=item["owner"],
            ledger=item["ledger"],
            tables={ref.split("/")[0]: signatures[ref] for ref in item["tables"]},
            measured=tuple(item["measured"]),
        )
        ledgers_of = by_owner.setdefault(history.owner, {})
        ledgers_of.setdefault(history.ledger, []).append(history)
    histories = {
        owner: {ledger: tuple(items) for ledger, items in ledgers.items()}
        for owner, ledgers in by_owner.items()
    }
    names: dict[str, frozenset[str]] = {}
    sigs: dict[str, set[str]] = {}
    for owner, ledgers in histories.items():
        names[owner] = frozenset(
            table for items in ledgers.values() for h in items for table in h.tables
        )
        for items in ledgers.values():
            for h in items:
                for table, signature in h.tables.items():
                    sigs.setdefault(f"{owner}/{table}", set()).add(signature)
    return _Catalog(
        engine_tables={
            table: frozenset(listed) for table, listed in data["engine_tables"].items()
        },
        runtime_versions={int(k): v for k, v in data["runtime_versions"].items()},
        histories=histories,
        names=names,
        signatures={key: frozenset(value) for key, value in sigs.items()},
    )


@lru_cache(maxsize=None)
def alembic_head(module_id: str) -> str | None:
    """``alembic <revision>`` for the head of the module's Alembic chain in this code."""
    from alembic.script import ScriptDirectory

    from openminion.modules.storage.migrations.alembic import (
        discover_module_alembic_paths,
    )

    _, location = discover_module_alembic_paths(module_id)
    if location is None:
        return None
    head = ScriptDirectory(str(location)).get_current_head()
    return None if head is None else f"alembic {head}"


@dataclass(frozen=True)
class SchemaLayout:
    """What one schema holds, as far as ownership is concerned."""

    name: str
    exists: bool
    tables: dict[str, str | None] = field(
        default_factory=dict
    )  # signature for known names
    alembic: tuple[str, ...] | None = None
    om_meta: dict[str, str] | None = None
    migrations: tuple[tuple[int, str], ...] | None = None
    objects: int = 0
    owner: str | None = None
    usage: bool = False
    create: bool = False

    def ledger(self, owner: str) -> str | None:
        """The ledger key this schema records for ``owner``, or None."""
        if owner == RUNTIME:
            rows = self.migrations or ()
            versions = _catalog().runtime_versions
            if rows and list(rows) == [
                (v, versions.get(v)) for v in range(1, len(rows) + 1)
            ]:
                return f"v{len(rows)}"
            return None
        meta = self.om_meta or {}
        if meta.get("module_id") != owner:
            return None
        if self.alembic is None:
            return f"store {meta.get('schema_head', '')}"
        if not self.alembic:
            # The module's Alembic chain downgraded to base: no revision, no table.
            return "alembic base"
        if len(self.alembic) == 1:
            return f"alembic {self.alembic[0]}"
        return None


@dataclass(frozen=True)
class ModuleSchema:
    """Where a module keeps its PostgreSQL tables.

    Only this module's selection functions build one from what they read. A caller that hands
    one to ``MigrationRunner(postgres_schema=...)`` or a store vouches for it: it is used as is.
    """

    module_id: str
    base: str
    schema: str
    create: bool = False
    ledger: str | None = None  # the module's ledger recorded there, if any

    @property
    def in_place(self) -> bool:
        return self.schema == self.base

    def url(self, configured_url: str) -> str:
        """The URL a module connection uses: unchanged in place, else its schema appended."""
        if self.in_place:
            return configured_url
        return module_url(configured_url, self.schema)


# ---------------------------------------------------------------------------- catalog reads


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def read_schema(connection: Connection, schema: str) -> SchemaLayout:
    """Read one schema's tables, signatures and ledger rows (explicit schema names only)."""
    from sqlalchemy import text

    row = connection.execute(
        text(
            "SELECT oid, pg_get_userbyid(nspowner), has_schema_privilege(oid, 'USAGE'), "
            "has_schema_privilege(oid, 'CREATE') FROM pg_catalog.pg_namespace "
            "WHERE nspname = :s"
        ),
        {"s": schema},
    ).first()
    if row is None:
        return SchemaLayout(name=schema, exists=False)
    oid, owner, usage, create = row
    if not usage:
        # Its ledger rows cannot be read; the caller refuses it with the GRANT to run.
        return SchemaLayout(
            name=schema, exists=True, owner=str(owner), create=bool(create)
        )
    catalog = _catalog()
    known = (
        {n for names in catalog.names.values() for n in names}
        | set(catalog.engine_tables)
        | _LEDGER_TABLES
    )
    names = [
        r[0]
        for r in connection.execute(
            text(
                "SELECT relname FROM pg_catalog.pg_class WHERE relnamespace = :o "
                "AND relkind IN ('r', 'p')"
            ),
            {"o": oid},
        )
    ]
    wanted = [n for n in names if n in known]
    columns: dict[str, list[tuple[str, str, bool]]] = {}
    for rel, att, typ, notnull in connection.execute(
        text(
            "SELECT c.relname, a.attname, format_type(a.atttypid, a.atttypmod), "
            "a.attnotnull FROM pg_catalog.pg_class c "
            "JOIN pg_catalog.pg_attribute a ON a.attrelid = c.oid "
            "WHERE c.relnamespace = :o AND c.relname = ANY(:n) "
            "AND a.attnum > 0 AND NOT a.attisdropped"
        ),
        {"o": oid, "n": wanted},
    ):
        columns.setdefault(str(rel), []).append((str(att), str(typ), bool(notnull)))
    keys: dict[str, list[str]] = {}
    for rel, att in connection.execute(
        text(
            "SELECT c.relname, a.attname FROM pg_catalog.pg_index i "
            "JOIN pg_catalog.pg_class c ON c.oid = i.indrelid "
            "JOIN pg_catalog.pg_attribute a ON a.attrelid = c.oid "
            "AND a.attnum = ANY(i.indkey) "
            "WHERE i.indisprimary AND c.relnamespace = :o AND c.relname = ANY(:n)"
        ),
        {"o": oid, "n": wanted},
    ):
        keys.setdefault(str(rel), []).append(str(att))
    tables: dict[str, str | None] = {
        n: (signature(columns.get(n, []), keys.get(n, [])) if n in known else None)
        for n in names
    }
    objects: int = connection.execute(
        text(
            "SELECT (SELECT count(*) FROM pg_catalog.pg_class WHERE relnamespace = :o) "
            "+ (SELECT count(*) FROM pg_catalog.pg_proc WHERE pronamespace = :o) "
            "+ (SELECT count(*) FROM pg_catalog.pg_type WHERE typnamespace = :o "
            "AND typrelid = 0 AND typelem = 0)"
        ),
        {"o": oid},
    ).scalar_one()
    return SchemaLayout(
        name=schema,
        exists=True,
        tables=tables,
        alembic=_alembic_rows(connection, schema, columns),
        om_meta=_om_meta(connection, schema, columns),
        migrations=_runtime_rows(connection, schema, columns),
        objects=int(objects),
        owner=str(owner),
        usage=True,
        create=bool(create),
    )


def signature(columns: list[tuple[str, str, bool]], primary_key: list[str]) -> str:
    """``name type [NOT NULL], ...; pk(a, b)`` with columns in name order."""
    cols = ", ".join(
        f"{name} {typ}{' NOT NULL' if notnull else ''}"
        for name, typ, notnull in sorted(columns)
    )
    return f"{cols}; pk({', '.join(sorted(primary_key))})"


def _column_types(
    columns: dict[str, list[tuple[str, str, bool]]], table: str
) -> dict[str, str]:
    return {name: typ for name, typ, _ in columns.get(table, [])}


def _alembic_rows(
    connection: Connection,
    schema: str,
    columns: dict[str, list[tuple[str, str, bool]]],
) -> tuple[str, ...] | None:
    from sqlalchemy import text

    if "alembic_version" not in columns:
        return None
    if "version_num" not in _column_types(columns, "alembic_version"):
        return ("<alembic_version without version_num>",)
    rows = connection.execute(
        text(f"SELECT version_num FROM {_quote(schema)}.alembic_version")
    )
    return tuple(sorted(str(r[0]) for r in rows))


def _om_meta(
    connection: Connection,
    schema: str,
    columns: dict[str, list[tuple[str, str, bool]]],
) -> dict[str, str] | None:
    from sqlalchemy import text

    if "om_meta" not in columns:
        return None
    if not {"key", "value"} <= set(_column_types(columns, "om_meta")):
        return {"module_id": "<om_meta without key/value>"}
    rows = connection.execute(
        text(
            f"SELECT key, value FROM {_quote(schema)}.om_meta "
            "WHERE key IN ('module_id', 'schema_head')"
        )
    )
    return {str(r[0]): str(r[1]) for r in rows}


def _runtime_rows(
    connection: Connection,
    schema: str,
    columns: dict[str, list[tuple[str, str, bool]]],
) -> tuple[tuple[int, str], ...] | None:
    """Runtime storage's ledger rows; None when ``migrations`` cannot hold them."""
    from sqlalchemy import text

    types = _column_types(columns, "migrations")
    if types.get("version") not in _INTEGER_TYPES or "name" not in types:
        # Not runtime storage's table: its signature then matches no runtime history.
        return None
    rows = connection.execute(
        text(f"SELECT version, name FROM {_quote(schema)}.migrations ORDER BY version")
    )
    return tuple((int(r[0]), str(r[1])) for r in rows)


# ---------------------------------------------------------------------------- evidence


def _name(owner: str) -> str:
    return "runtime storage" if owner == RUNTIME else owner


def _label(owner: str, layout: SchemaLayout) -> str:
    return f"{_name(owner)} {layout.ledger(owner)}"


def complete_history(owner: str, layout: SchemaLayout) -> History | None:
    """The listed history the schema's ledger records for ``owner``, if all its tables match."""
    ledger = layout.ledger(owner)
    if ledger is None:
        return None
    matches = [
        history
        for history in _catalog().histories.get(owner, {}).get(ledger, ())
        if all(layout.tables.get(t) == sig for t, sig in history.tables.items())
    ]
    if not matches:
        return None
    # A store's extra tables make a second listed history with the same ledger: prefer the
    # one that holds exactly the owner's tables here, else the largest.
    owned = _owned_tables(owner, layout)
    exact = [history for history in matches if set(history.tables) == owned]
    return (exact or sorted(matches, key=lambda h: -len(h.tables)))[0]


def _owned_tables(owner: str, layout: SchemaLayout) -> set[str]:
    """The tables in ``layout`` whose names ``owner`` creates or reads."""
    return {t for t in layout.tables if t in _catalog().names.get(owner, frozenset())}


def _attribution(layout: SchemaLayout) -> dict[str, list[str]]:
    """Table -> the owners it belongs to, each table on its own.

    A table belongs to owner O when O's ledger is recorded in the schema and a history listed
    for that ledger has the table with exactly these columns.
    """
    catalog = _catalog()
    result: dict[str, list[str]] = {}
    for owner in sorted(catalog.histories, key=lambda o: (o != RUNTIME, o)):
        ledger = layout.ledger(owner)
        listed = catalog.histories[owner].get(ledger or "", ())
        for table, sig in layout.tables.items():
            if sig is not None and any(h.tables.get(table) == sig for h in listed):
                result.setdefault(table, []).append(owner)
    return result


def _proven(
    owner: str, layout: SchemaLayout, attribution: dict[str, list[str]]
) -> History | None:
    """The owner's listed history in ``layout`` when it is proven, else None.

    Every table of the history is there exactly and belongs to the owner alone, and no other
    table with one of the owner's names is there.
    """
    history = complete_history(owner, layout)
    if history is None or _owned_tables(owner, layout) != set(history.tables):
        return None
    if any(attribution.get(t) != [owner] for t in history.tables):
        return None
    return history


def _facts(
    owner: str,
    layout: SchemaLayout,
    attribution: dict[str, list[str]],
    tables: list[str] | None = None,
) -> str:
    """Why ``owner``'s tables in ``layout`` (or just ``tables``) are not its own, as facts."""
    catalog = _catalog()
    name = _name(owner)
    ledger = layout.ledger(owner)
    listed = catalog.histories.get(owner, {}).get(ledger or "", ())
    if ledger is None:
        facts = [f"no {name} ledger is recorded there"]
    elif not listed:
        facts = [f"its {name} ledger records {ledger}, which is not a listed history"]
    else:
        facts = [f"its {name} ledger records {ledger}"]
        common = set.intersection(*(set(h.tables) for h in listed))
        facts += [f"{t} is missing" for t in sorted(common - set(layout.tables))]
    for table in sorted(_owned_tables(owner, layout) if tables is None else tables):
        holders = attribution.get(table, [])
        if holders == [owner]:
            continue
        if holders:
            labels = " and ".join(_label(o, layout) for o in holders)
            facts.append(f"{table} is recorded by {labels}")
        elif any(table in h.tables for h in listed):
            facts.append(f"{table} has other columns than {name} {ledger} lists")
        else:
            sig = layout.tables[table]
            listed_for = [
                _name(o)
                for o in sorted(catalog.names)
                if sig in catalog.signatures.get(f"{o}/{table}", frozenset())
            ]
            facts.append(
                f"{table} matches no recorded ledger ("
                + (
                    f"its columns are listed for {', '.join(listed_for)})"
                    if listed_for
                    else "its columns match no listed history)"
                )
            )
    if len(facts) == 1 and listed:
        facts.append(f"its tables are not one listed {name} {ledger} history")
    return "; ".join(facts)


# ---------------------------------------------------------------------------- decisions


def runtime_refusal(layout: SchemaLayout) -> str | None:
    """None when runtime storage can use the base schema, else the refusal message."""
    if not _owned_tables(RUNTIME, layout):
        return None
    attribution = _attribution(layout)
    if _proven(RUNTIME, layout, attribution) is not None:
        return None
    return (
        f"Runtime storage will not use PostgreSQL schema {_quote(layout.name)}: tables it "
        f"creates or reads there are not provably its own: "
        f"{_facts(RUNTIME, layout, attribution)}. {_NOTHING_WRITTEN} {_GUIDANCE}"
    )


def check_runtime_schema(engine: Engine) -> None:
    """Refuse, before any write, a base schema runtime storage cannot prove is its own."""
    with _read_only(engine) as connection:
        base = _current_schema(connection)
        if base is None:
            raise SchemaOwnershipError(
                "Runtime storage: the PostgreSQL search_path names no existing schema. "
                f"{_NOTHING_WRITTEN}"
            )
        message = runtime_refusal(read_schema(connection, base))
    if message is not None:
        raise SchemaOwnershipError(message)


def check_runtime_postgres_url(url: str) -> None:
    """:func:`check_runtime_schema` for a configured URL, on a short-lived connection."""
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool

    probe = create_engine(url, poolclass=NullPool)
    try:
        check_runtime_schema(probe)
    finally:
        probe.dispose()


def derived_schema_name(base: str, module_id: str) -> str:
    return f"{base}__{module_id}"


def _name_problem(name: str) -> str | None:
    if not _SAFE_NAME.fullmatch(name):
        return "it may only contain letters, digits and underscores"
    if len(name.encode("utf-8")) > POSTGRES_MAX_IDENTIFIER_BYTES:
        return f"it is longer than {POSTGRES_MAX_IDENTIFIER_BYTES} bytes"
    return None


def decide_module_schema(
    module_id: str,
    base: SchemaLayout,
    derived: SchemaLayout,
    *,
    role: str,
    database_create: bool,
) -> ModuleSchema:
    """Choose the module's schema from the configured base schema; raise when unproven.

    A base table with one of the module's names concerns the module unless it belongs to
    other owners only.
    """
    attribution = _attribution(base)
    unproven = sorted(
        t
        for t in _owned_tables(module_id, base)
        if module_id in attribution.get(t, [module_id])
    )
    recorded = (base.om_meta or {}).get("module_id") == module_id
    if recorded or unproven:
        shared = all(
            any(t in names for o, names in _catalog().names.items() if o != module_id)
            for t in unproven
        )
        proven_derived = _proven_derived(module_id, derived, role=role)
        if not recorded and shared and proven_derived:
            # The module already uses its own schema, and every base table in question
            # has a name another owner uses too: none of them can only be its rows.
            return ModuleSchema(
                module_id, base.name, derived.name, ledger=derived.ledger(module_id)
            )
        if derived.exists:
            held = " and ".join(
                (["its om_meta ledger"] if recorded else [])
                + (
                    [
                        "tables with its names that are not another owner's alone "
                        f"({', '.join(unproven)})"
                    ]
                    if unproven
                    else []
                )
            )
            if proven_derived:
                state = f"holds its proven {derived.ledger(module_id)} history"
            elif not derived.usage:
                state = "this role cannot read it"
            elif not derived.tables and not derived.objects:
                state = "is empty"
            else:
                state = f"holds no proven {module_id} history"
            raise SchemaOwnershipError(
                f"Module {module_id!r}: {_quote(base.name)} holds {held}, and "
                f"{_quote(derived.name)} also exists and {state}; neither is selected. "
                "Keep one location, and move the other to a schema that is not named "
                f"<base>__<module id>. {_NOTHING_WRITTEN} {_GUIDANCE}"
            )
        if _proven(module_id, base, attribution) is not None:
            return ModuleSchema(
                module_id, base.name, base.name, ledger=base.ledger(module_id)
            )
        raise SchemaOwnershipError(
            f"Module {module_id!r} will not use PostgreSQL schema {_quote(base.name)}: "
            f"its tables there are not provably its own: "
            f"{_facts(module_id, base, attribution, None if recorded else unproven)}. "
            f"{_NOTHING_WRITTEN} {_GUIDANCE}"
        )
    problem = _name_problem(derived.name)
    if problem is not None:
        raise SchemaOwnershipError(
            f"Module {module_id!r} needs its own PostgreSQL schema {derived.name!r}, but "
            f"{problem}. Use a base schema named with letters, digits and underscores, "
            f"at most {POSTGRES_MAX_IDENTIFIER_BYTES - 2 - len(module_id)} bytes. {_NOTHING_WRITTEN}"
        )
    if not derived.exists:
        if not database_create:
            raise SchemaOwnershipError(
                f"Module {module_id!r} needs to create PostgreSQL schema "
                f"{_quote(derived.name)}, and role {role!r} has no CREATE privilege on the "
                "database. A database administrator can run "
                f"CREATE SCHEMA {_quote(derived.name)} AUTHORIZATION {_quote(role)}; "
                f"or GRANT CREATE ON DATABASE <database> TO {_quote(role)}; "
                f"{_NOTHING_WRITTEN}"
            )
        return ModuleSchema(module_id, base.name, derived.name, create=True)
    refusal = _derived_refusal(module_id, derived, role=role)
    if refusal is not None:
        raise SchemaOwnershipError(refusal)
    return ModuleSchema(
        module_id, base.name, derived.name, ledger=derived.ledger(module_id)
    )


def _proven_derived(module_id: str, layout: SchemaLayout, *, role: str) -> bool:
    """Whether ``<base>__<module id>`` holds the module's ledger and is selectable."""
    return (
        layout.exists
        and layout.ledger(module_id) is not None
        and _derived_refusal(module_id, layout, role=role) is None
    )


def _derived_refusal(module_id: str, layout: SchemaLayout, *, role: str) -> str | None:
    """None when an existing ``<base>__<module id>`` can be used, else the refusal message.

    It can be used when it is empty (a schema a database administrator created, or one this code
    created before its first table), or when it records the module's ledger, holds one of the
    module's listed histories proven, and every storage engine table there has the exact columns
    this code creates. A schema with tables but no ledger of the module is refused whatever the
    tables are: a part of a store's tables, a copy, and a first start that stopped before it
    wrote ``om_meta`` look the same.
    """
    if not (layout.usage and layout.create):
        return (
            f"Module {module_id!r}: role {role!r} lacks USAGE or CREATE on PostgreSQL "
            f"schema {_quote(layout.name)} (owner {layout.owner}). A database administrator "
            f"can run GRANT USAGE, CREATE ON SCHEMA {_quote(layout.name)} TO {_quote(role)}; "
            f"{_NOTHING_WRITTEN}"
        )
    catalog = _catalog()
    where = f"Module {module_id!r}: PostgreSQL schema {_quote(layout.name)}"
    if not layout.tables:
        if layout.objects:
            return f"{where} holds objects but no table; it is not selected. {_NOTHING_WRITTEN}"
        return None
    foreign = sorted(
        t
        for t in layout.tables
        if t not in catalog.names.get(module_id, ())
        and t not in catalog.engine_tables
        and t not in _LEDGER_TABLES
    )
    if foreign:
        return (
            f"{where} holds tables that are not {module_id}'s: {', '.join(foreign)}. "
            f"{_NOTHING_WRITTEN} {_GUIDANCE}"
        )
    meta = (layout.om_meta or {}).get("module_id")
    if meta not in (None, module_id) or layout.migrations:
        recorded = (
            f"om_meta names {meta!r}"
            if meta not in (None, module_id)
            else ("migrations holds runtime storage ledger rows")
        )
        return (
            f"{where} records another owner's ledger: {recorded}. "
            f"{_NOTHING_WRITTEN} {_GUIDANCE}"
        )
    if layout.ledger(module_id) is None:
        return (
            f"{where} holds tables ({', '.join(sorted(layout.tables))}) but records no "
            f"{module_id} ledger: tables without their owner's ledger are not evidence, "
            f"also when they have the columns this code's {module_id} store creates (a first "
            f"start that stopped before it wrote om_meta leaves such a schema). "
            f"{_NOTHING_WRITTEN} {_GUIDANCE}"
        )
    attribution = _attribution(layout)
    if _proven(module_id, layout, attribution) is None:
        return (
            f"{where} is not provably {module_id}'s: "
            f"{_facts(module_id, layout, attribution)}. {_NOTHING_WRITTEN} {_GUIDANCE}"
        )
    engine = sorted(
        t
        for t, sig in layout.tables.items()
        if t in catalog.engine_tables and sig not in catalog.engine_tables[t]
    )
    if engine:
        return (
            f"{where} holds {', '.join(engine)} with other columns than this code's "
            f"storage engine creates. {_NOTHING_WRITTEN} {_GUIDANCE}"
        )
    return None


def select_module_schema(engine: Engine, module_id: str) -> ModuleSchema:
    """Choose the schema of ``module_id`` under the configured URL's base schema.

    Read only; raises :class:`SchemaOwnershipError` before any write when the choice is not
    proven.
    """
    with _read_only(engine) as connection:
        return _select_on(connection, module_id)


def _select_on(connection: Connection, module_id: str) -> ModuleSchema:
    base_name = _current_schema(connection)
    if base_name is None:
        raise SchemaOwnershipError(
            f"Module {module_id!r}: the PostgreSQL search_path names no existing schema. "
            f"{_NOTHING_WRITTEN}"
        )
    role, database_create = connection.execute(
        _sql(
            "SELECT current_user, has_database_privilege(current_database(), 'CREATE')"
        )
    ).one()
    base = read_schema(connection, base_name)
    derived_name = derived_schema_name(base_name, module_id)
    derived = (
        read_schema(connection, derived_name)
        if _name_problem(derived_name) is None
        else SchemaLayout(name=derived_name, exists=False)
    )
    return decide_module_schema(
        module_id,
        base,
        derived,
        role=str(role),
        database_create=bool(database_create),
    )


def check_module_connection(engine: Engine, module_id: str) -> ModuleSchema:
    """For an engine handed to a module directly: its current schema must be the module's own.

    The schema is used when it holds the module's proven history, or when it is empty. Read
    only.
    """
    with _read_only(engine) as connection:
        name = _current_schema(connection)
        if name is None:
            raise SchemaOwnershipError(
                f"Module {module_id!r}: the PostgreSQL search_path names no existing schema. "
                f"{_NOTHING_WRITTEN}"
            )
        role = str(connection.execute(_sql("SELECT current_user")).scalar_one())
        layout = read_schema(connection, name)
    ledger = layout.ledger(module_id)
    if _proven(module_id, layout, _attribution(layout)) is not None:
        return ModuleSchema(module_id, name, name, ledger=ledger)
    refusal = _derived_refusal(module_id, layout, role=role)
    if refusal is None:
        return ModuleSchema(module_id, name, name, ledger=ledger)
    raise SchemaOwnershipError(refusal)


# ---------------------------------------------------------------------------- connections


def module_url(url: str, schema: str) -> str:
    """``url`` with ``-c search_path="<schema>"`` appended to its ``options``.

    Every other query parameter and options token is kept; when the URL has no ``options``,
    ``PGOPTIONS`` is the starting value, because URL options replace it.
    """
    from sqlalchemy.engine import make_url

    parsed = make_url(url)
    current = parsed.query.get("options")
    if isinstance(current, tuple):
        raise SchemaOwnershipError(
            "The PostgreSQL URL repeats the options parameter; give one options value."
        )
    if current is None:
        from openminion.base.config.env import resolve_environment_config

        current = str(resolve_environment_config().get("PGOPTIONS", "") or "")
    options = f'{current} -c search_path="{schema}"'.strip()
    return parsed.update_query_dict({"options": options}).render_as_string(
        hide_password=False
    )


def _require_landing(url: str, schema: str) -> None:
    """Refuse, before any write, a module URL whose connections would not use ``schema``.

    Reads the connection's ``search_path`` setting, so it works before the schema exists. A
    server or a proxy that drops the URL's ``options`` leaves the default instead.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool

    probe = create_engine(url, poolclass=NullPool)
    try:
        with _read_only(probe) as connection:
            setting = str(
                connection.execute(
                    _sql("SELECT current_setting('search_path')")
                ).scalar_one()
            )
    finally:
        probe.dispose()
    if setting.strip() not in (schema, _quote(schema)):
        raise SchemaOwnershipError(
            f"A connection for PostgreSQL schema {_quote(schema)} started with search_path "
            f"{setting!r}: the server or a proxy (a transaction pooler such as PgBouncer) "
            "did not apply the URL's options parameter. Connect module stores directly or "
            f"through a pooler that passes options. {_NOTHING_WRITTEN}"
        )


def enter_module_schema(connection: Connection, selection: ModuleSchema) -> None:
    """Inside a migration transaction: create the derived schema if selected, then use it."""
    if selection.create:
        connection.execute(_sql(f"CREATE SCHEMA {_quote(selection.schema)}"))
    connection.execute(_sql(f"SET LOCAL search_path TO {_quote(selection.schema)}"))


def select_postgres_module_schema(
    url: str, module_id: str, *, create: bool = False
) -> ModuleSchema:
    """:func:`select_module_schema` for a configured URL, on short-lived connections.

    For the module's own schema, a connection with the module URL must start in it; that is
    checked before any write. With ``create`` a missing schema is created in its own
    transaction, which makes the choice again and rolls the schema back if that refuses.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool

    probe = create_engine(url, poolclass=NullPool)
    try:
        selection = select_module_schema(probe, module_id)
        if selection.in_place:
            return selection
        _require_landing(selection.url(url), selection.schema)
        if create and selection.create:
            with probe.begin() as connection:
                connection.execute(
                    _sql(f"CREATE SCHEMA IF NOT EXISTS {_quote(selection.schema)}")
                )
                # Another process may have created and used it meanwhile.
                selection = _select_on(connection, module_id)
    finally:
        probe.dispose()
    return selection


def module_storage_config(
    config: StorageEngineConfig, module_id: str
) -> StorageEngineConfig:
    """The ``record.postgres`` config a module store uses: its schema selected and created."""
    options = dict(config.record_backend_options)
    url = str(options.get("url", "") or "").strip()
    if not url:
        return config
    selection = select_postgres_module_schema(url, module_id, create=True)
    ledger = selection.ledger or ""
    if ledger.startswith("alembic ") and ledger != alembic_head(module_id):
        # The store's own CREATE TABLE IF NOT EXISTS would add the head's tables under an
        # older alembic_version, a layout `storage migrate` could no longer upgrade.
        head = alembic_head(module_id)
        raise SchemaOwnershipError(
            f"Module {module_id!r}: PostgreSQL schema {_quote(selection.schema)} records "
            f"{selection.ledger}, behind this code's {head}. Run `openminion storage "
            f"migrate --backend postgres --module {module_id}` before the module "
            f"store opens it. {_NOTHING_WRITTEN}"
        )
    if selection.in_place:
        return config
    options["url"] = selection.url(url)
    return replace(config, record_backend_options=options)


def _sql(statement: str) -> Any:
    from sqlalchemy import text

    return text(statement)


def _current_schema(connection: Connection) -> str | None:
    value = connection.execute(_sql("SELECT current_schema()")).scalar()
    return None if value is None else str(value)


@contextmanager
def _read_only(engine: Engine) -> Iterator[Connection]:
    """One READ ONLY transaction on a new connection, always rolled back."""
    connection = engine.connect()
    try:
        transaction = connection.begin()
        try:
            connection.execute(_sql("SET TRANSACTION READ ONLY"))
            yield connection
        finally:
            transaction.rollback()
    finally:
        connection.close()


__all__ = [
    "ModuleSchema",
    "SchemaLayout",
    "check_module_connection",
    "check_runtime_postgres_url",
    "decide_module_schema",
    "enter_module_schema",
    "module_storage_config",
    "module_url",
    "read_schema",
    "select_module_schema",
    "select_postgres_module_schema",
]
