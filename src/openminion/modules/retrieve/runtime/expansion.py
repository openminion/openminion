from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from .retrieval import candidate_from_row, to_retrieved_item
from ..schemas import RetrievedItem


def _safe_json_loads(raw: str | None, fallback: Any) -> Any:
    if raw is None:
        return fallback
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return fallback


def _missing_explanation(payload: Mapping[str, Any], detail: str) -> dict[str, Any]:
    return {
        "why": payload.get("why", ""),
        "score": float(payload.get("score", 0.0) or 0.0),
        "detail": detail,
    }


def explain_item(
    service: Any, item: dict[str, Any] | RetrievedItem | str
) -> dict[str, Any]:
    if isinstance(item, RetrievedItem):
        payload = item.model_dump(mode="json")
    elif isinstance(item, dict):
        payload = dict(item)
    else:
        payload = {"ref_id": str(item)}

    unit_id = None
    meta = payload.get("meta")
    if isinstance(meta, dict):
        raw_unit = meta.get("unit_id")
        if raw_unit is not None:
            unit_id = str(raw_unit)
    if unit_id is None:
        ref_id = str(payload.get("ref_id", ""))
        unit_id = service._parse_unit_id_from_ref(ref_id)

    if unit_id is None:
        return _missing_explanation(payload, "unit not found")

    row = service.store.execute(
        """
        SELECT u.unit_id, u.doc_id, u.unit_kind, u.level, u.node_id, u.group_id,
               u.offsets_json, d.source_type, d.source_ref, d.scope, d.tags_json,
               d.created_at, d.updated_at
        FROM retrievectl_units u
        JOIN retrievectl_docs d ON d.doc_id = u.doc_id
        WHERE u.unit_id = ?
        """,
        (unit_id,),
    ).fetchone()
    if row is None:
        return _missing_explanation(payload, "unit row not found")

    return {
        "ref_id": payload.get("ref_id"),
        "why": payload.get("why", ""),
        "score": float(payload.get("score", 0.0) or 0.0),
        "doc_id": str(row["doc_id"]),
        "unit_id": str(row["unit_id"]),
        "source_type": str(row["source_type"]),
        "source_ref": str(row["source_ref"]),
        "scope": str(row["scope"]),
        "unit_kind": str(row["unit_kind"]),
        "level": str(row["level"] or "none"),
        "node_id": row["node_id"],
        "group_id": row["group_id"],
        "offsets": _safe_json_loads(str(row["offsets_json"] or "{}"), {}),
        "tags": _safe_json_loads(str(row["tags_json"] or "[]"), []),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def lookup_unit_row(service: Any, unit_id: str) -> Mapping[str, Any] | None:
    return service.store.execute(
        """
        SELECT u.unit_id, u.doc_id, u.unit_kind, u.level, u.node_id, u.group_id,
               u.text_ref, u.offsets_json, d.source_type, d.source_ref, d.scope,
               d.tags_json, d.created_at
        FROM retrievectl_units u
        JOIN retrievectl_docs d ON d.doc_id = u.doc_id
        WHERE u.unit_id = ?
        """,
        (unit_id,),
    ).fetchone()


def lookup_unit_rows_batch(
    service: Any, unit_ids: Sequence[str]
) -> dict[str, Mapping[str, Any]]:
    normalized = list(
        dict.fromkeys(str(item) for item in unit_ids if str(item).strip())
    )
    if not normalized:
        return {}
    placeholders = ",".join("?" for _ in normalized)
    rows = service.store.execute(
        f"""
        SELECT u.unit_id, u.doc_id, u.unit_kind, u.level, u.node_id, u.group_id,
               u.text_ref, u.offsets_json, d.source_type, d.source_ref, d.scope,
               d.tags_json, d.created_at
        FROM retrievectl_units u
        JOIN retrievectl_docs d ON d.doc_id = u.doc_id
        WHERE u.unit_id IN ({placeholders})
        """,
        tuple(normalized),
    ).fetchall()
    return {str(row["unit_id"]): row for row in rows}


def leaf_ids_for_node(service: Any, node_id: str) -> list[str]:
    row = service.store.execute(
        "SELECT leaf_unit_ids_json FROM retrievectl_raptor_nodes WHERE node_id = ?",
        (node_id,),
    ).fetchone()
    if row is None:
        return []
    payload = _safe_json_loads(str(row["leaf_unit_ids_json"]), [])
    if not isinstance(payload, list):
        return []
    return [str(item) for item in payload if str(item).strip()]


def expand_node(service: Any, *, node_id: str, k: int) -> list[RetrievedItem]:
    leaf_ids = leaf_ids_for_node(service, node_id)
    if not leaf_ids:
        return []
    out: list[RetrievedItem] = []
    for idx, leaf_id in enumerate(leaf_ids[:k]):
        row = lookup_unit_row(service, leaf_id)
        if row is None:
            continue
        candidate = candidate_from_row(
            service, row, inherited_score=max(0.0, 0.95 - (idx * 0.03))
        )
        candidate["level"] = "leaf"
        candidate["node_id"] = node_id
        out.append(to_retrieved_item(service, candidate=candidate, strategy="raptor"))
    return out


def expand_group(service: Any, *, group_id: str, k: int) -> list[RetrievedItem]:
    rows = service.store.execute(
        """
        SELECT u.unit_id, u.doc_id, u.unit_kind, u.level, u.node_id, u.group_id,
               u.text_ref, u.offsets_json, d.source_type, d.source_ref, d.scope,
               d.tags_json, d.created_at
        FROM retrievectl_units u
        JOIN retrievectl_docs d ON d.doc_id = u.doc_id
        WHERE u.group_id = ?
        ORDER BY COALESCE(json_extract(u.offsets_json, '$.start_token'), 0), u.unit_id
        LIMIT ?
        """,
        (group_id, int(k)),
    ).fetchall()
    out: list[RetrievedItem] = []
    for idx, row in enumerate(rows):
        candidate = candidate_from_row(
            service, row, inherited_score=max(0.0, 0.95 - (idx * 0.05))
        )
        out.append(
            to_retrieved_item(
                service, candidate=candidate, strategy="longrag_doc_group"
            )
        )
    return out


def expand_window(service: Any, *, unit_id: str, k: int) -> list[RetrievedItem]:
    focus = service.store.execute(
        "SELECT doc_id, offsets_json FROM retrievectl_units WHERE unit_id = ?",
        (unit_id,),
    ).fetchone()
    if focus is None:
        return []
    doc_id = str(focus["doc_id"])

    rows = service.store.execute(
        """
        SELECT u.unit_id, u.doc_id, u.unit_kind, u.level, u.node_id, u.group_id,
               u.text_ref, u.offsets_json, d.source_type, d.source_ref, d.scope,
               d.tags_json, d.created_at
        FROM retrievectl_units u
        JOIN retrievectl_docs d ON d.doc_id = u.doc_id
        WHERE u.doc_id = ?
        ORDER BY COALESCE(json_extract(u.offsets_json, '$.start_token'), 0), u.unit_id
        """,
        (doc_id,),
    ).fetchall()
    if not rows:
        return []

    index = 0
    for i, row in enumerate(rows):
        if str(row["unit_id"]) == unit_id:
            index = i
            break

    left = max(0, index - (k // 2))
    right = min(len(rows), left + k)
    window = rows[left:right]

    out: list[RetrievedItem] = []
    for i, row in enumerate(window):
        distance = abs((left + i) - index)
        score = max(0.0, 1.0 - (distance * 0.2))
        candidate = candidate_from_row(service, row, inherited_score=score)
        out.append(
            to_retrieved_item(service, candidate=candidate, strategy="contextual")
        )
    return out


def expand_document(service: Any, *, unit_id: str, k: int) -> list[RetrievedItem]:
    focus = service.store.execute(
        "SELECT doc_id FROM retrievectl_units WHERE unit_id = ?",
        (unit_id,),
    ).fetchone()
    if focus is None:
        return []
    doc_id = str(focus["doc_id"])
    rows = service.store.execute(
        """
        SELECT u.unit_id, u.doc_id, u.unit_kind, u.level, u.node_id, u.group_id,
               u.text_ref, u.offsets_json, d.source_type, d.source_ref, d.scope,
               d.tags_json, d.created_at
        FROM retrievectl_units u
        JOIN retrievectl_docs d ON d.doc_id = u.doc_id
        WHERE u.doc_id = ?
        ORDER BY COALESCE(json_extract(u.offsets_json, '$.start_token'), 0), u.unit_id
        LIMIT ?
        """,
        (doc_id, int(k)),
    ).fetchall()
    out: list[RetrievedItem] = []
    for idx, row in enumerate(rows):
        score = max(0.0, 0.95 - (idx * 0.05))
        out.append(
            to_retrieved_item(
                service,
                candidate=candidate_from_row(service, row, score),
                strategy="contextual",
            )
        )
    return out
