from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Sequence
from uuid import uuid4

from openminion.base.time import utc_now_iso as _iso_now


@dataclass(frozen=True)
class StorageOpsContext:
    blob_store: Any
    store: Any
    fts_enabled: bool


def _delete_fts_rows(ctx: StorageOpsContext, rows: Sequence[Any]) -> None:
    for row in rows:
        ctx.store.execute(
            "DELETE FROM retrievectl_units_fts WHERE unit_id = ?",
            (str(row["unit_id"]),),
        )


def write_text_blob(ctx: StorageOpsContext, text: str) -> str:
    blob = ctx.blob_store.put_bytes(
        text.encode("utf-8"),
        media_type="text/plain",
        ext="txt",
        meta={"module": "retrievectl"},
    )
    return str(blob.hash)


def read_text_blob(ctx: StorageOpsContext, text_ref: str) -> str:
    try:
        with ctx.blob_store.open(str(text_ref)) as handle:
            return handle.read().decode("utf-8", errors="replace")
    except FileNotFoundError:
        return ""


def index_unit_fts(
    ctx: StorageOpsContext,
    *,
    unit_id: str,
    title: str,
    fts_text: str,
    tags: Sequence[str],
) -> None:
    params = (unit_id, str(title or "").strip(), fts_text, " ".join(tags))
    if ctx.fts_enabled:
        ctx.store.execute(
            "INSERT INTO retrievectl_units_fts(unit_id, title, fts_text, tags) VALUES (?, ?, ?, ?)",
            params,
        )
    else:
        ctx.store.execute(
            """
            INSERT INTO retrievectl_units_fts(unit_id, title, fts_text, tags)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(unit_id) DO UPDATE SET
                title=excluded.title,
                fts_text=excluded.fts_text,
                tags=excluded.tags
            """,
            params,
        )


def delete_units_for_doc(
    ctx: StorageOpsContext, doc_id: str, unit_kind: str | None = None
) -> None:
    if unit_kind:
        rows = ctx.store.execute(
            "SELECT unit_id FROM retrievectl_units WHERE doc_id = ? AND unit_kind = ?",
            (doc_id, unit_kind),
        ).fetchall()
    else:
        rows = ctx.store.execute(
            "SELECT unit_id FROM retrievectl_units WHERE doc_id = ?", (doc_id,)
        ).fetchall()

    _delete_fts_rows(ctx, rows)
    if unit_kind:
        ctx.store.execute(
            "DELETE FROM retrievectl_units WHERE doc_id = ? AND unit_kind = ?",
            (doc_id, unit_kind),
        )
    else:
        ctx.store.execute("DELETE FROM retrievectl_units WHERE doc_id = ?", (doc_id,))


def delete_raptor_for_doc(ctx: StorageOpsContext, doc_id: str) -> None:
    internal_rows = ctx.store.execute(
        "SELECT unit_id FROM retrievectl_units WHERE doc_id = ? AND level IN ('root', 'internal')",
        (doc_id,),
    ).fetchall()
    _delete_fts_rows(ctx, internal_rows)
    ctx.store.execute(
        "DELETE FROM retrievectl_units WHERE doc_id = ? AND level IN ('root', 'internal')",
        (doc_id,),
    )
    ctx.store.execute(
        "DELETE FROM retrievectl_raptor_nodes WHERE doc_id = ?", (doc_id,)
    )


def row_exists(ctx: StorageOpsContext, table: str, key: str, value: str) -> bool:
    sql = f"SELECT 1 FROM {table} WHERE {key} = ? LIMIT 1"
    row = ctx.store.execute(sql, (value,)).fetchone()
    return row is not None


def record_run(
    ctx: StorageOpsContext,
    *,
    session_id: str,
    query: str,
    strategy: str,
    k: int,
    unit_ids: list[str],
) -> None:
    ctx.store.execute(
        """
        INSERT INTO retrievectl_runs(run_id, session_id, query, strategy, k, selected_unit_ids_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            uuid4().hex,
            session_id,
            query,
            strategy,
            int(k),
            json.dumps(
                unit_ids, sort_keys=True, separators=(",", ":"), ensure_ascii=True
            ),
            _iso_now(),
        ),
    )
    ctx.store.commit()
