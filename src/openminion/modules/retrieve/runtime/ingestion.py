from __future__ import annotations

import json
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from ..errors import RetrieveCtlError
from ..schemas import (
    DocUnit,
    GroupLongUnitsResult,
    IngestResult,
    RaptorBuildResult,
)
from .unitization import estimate_tokens

from openminion.base.time import utc_now_iso as _iso_now


def _stable_id(namespace: str, value: str) -> str:
    return str(uuid5(uuid5(NAMESPACE_URL, namespace), value))


def _optional_payload_str(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    return None if value is None else (str(value).strip() or None)


def _payload_ingest_kwargs(
    payload: dict[str, Any],
    *,
    default_scope: str,
    default_title: str,
    extra_tags: tuple[str, ...] = (),
) -> dict[str, Any]:
    raw_tags = payload.get("tags")
    tags = raw_tags if isinstance(raw_tags, list) else []
    return {
        "scope": str(payload.get("scope", default_scope)),
        "tags": [str(tag) for tag in tags] + list(extra_tags),
        "title": str(payload.get("title", default_title)),
        "corpus_id": _optional_payload_str(payload, "corpus_id"),
        "unit_kind": _optional_payload_str(payload, "unit_kind"),
        "created_at": _optional_payload_str(payload, "created_at"),
    }


def ingest_artifact(
    service: Any, artifact_ref: str, meta: dict[str, Any] | None = None
) -> dict[str, Any]:
    payload = dict(meta or {})
    text = extract_ingest_text(payload)
    kwargs = _payload_ingest_kwargs(
        payload,
        default_scope="project",
        default_title=str(payload.get("label", artifact_ref)),
    )
    return ingest_source(
        service,
        source_type="artifact",
        source_ref=artifact_ref,
        text=text,
        **kwargs,
    ).model_dump(mode="json")


def ingest_skill(
    service: Any,
    skill_id: str,
    version_hash: str,
    source_ref: str,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = dict(meta or {})
    text = extract_ingest_text(payload)
    effective_source_ref = source_ref or f"skill:{skill_id}@{version_hash}"
    kwargs = _payload_ingest_kwargs(
        payload,
        default_scope="agent",
        default_title=skill_id,
        extra_tags=("skill",),
    )
    return ingest_source(
        service,
        source_type="skill",
        source_ref=effective_source_ref,
        text=text,
        **kwargs,
    ).model_dump(mode="json")


def ingest_memory(
    service: Any, mem_id: str, text: str, meta: dict[str, Any] | None = None
) -> dict[str, Any]:
    payload = dict(meta or {})
    kwargs = _payload_ingest_kwargs(
        payload,
        default_scope="agent",
        default_title=mem_id,
        extra_tags=("memory",),
    )
    return ingest_source(
        service,
        source_type="mem",
        source_ref=f"mem:{mem_id}",
        text=str(text or ""),
        **kwargs,
    ).model_dump(mode="json")


def ingest_source(
    service: Any,
    *,
    source_type: str,
    source_ref: str,
    text: str,
    scope: str,
    tags: list[str] | None = None,
    title: str | None = None,
    corpus_id: str | None = None,
    unit_kind: str | None = None,
    created_at: str | None = None,
) -> IngestResult:
    normalized_text = str(text or "").strip()
    if not normalized_text:
        raise RetrieveCtlError("INVALID_ARGUMENT", "ingest text cannot be empty")

    normalized_source_type = service._normalize_source_type(source_type)
    normalized_scope = service._normalize_scope(scope)
    normalized_unit_kind = service._normalize_unit_kind(unit_kind or "chunk")
    normalized_tags = sorted(
        {str(tag).strip() for tag in (tags or []) if str(tag).strip()}
    )
    created_ts = created_at or _iso_now()
    updated_ts = _iso_now()
    doc_id = _stable_id("retrievectl-doc", f"{normalized_source_type}:{source_ref}")

    doc = DocUnit(
        doc_id=doc_id,
        source_ref=str(source_ref),
        source_type=normalized_source_type,
        text="",
        scope=normalized_scope,
        tags=normalized_tags,
        created_at=created_ts,
        updated_at=updated_ts,
        title=str(title or "").strip(),
        corpus_id=corpus_id,
    )

    with service.record_store.transaction():
        service.store.execute(
            """
            INSERT INTO retrievectl_docs(doc_id, source_type, source_ref, scope, tags_json, created_at, updated_at, title, corpus_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(doc_id) DO UPDATE SET
                source_type=excluded.source_type,
                source_ref=excluded.source_ref,
                scope=excluded.scope,
                tags_json=excluded.tags_json,
                updated_at=excluded.updated_at,
                title=excluded.title,
                corpus_id=excluded.corpus_id
            """,
            (
                doc.doc_id,
                doc.source_type,
                doc.source_ref,
                doc.scope,
                json.dumps(
                    doc.tags,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=True,
                ),
                doc.created_at,
                doc.updated_at,
                doc.title,
                doc.corpus_id,
            ),
        )
        service._delete_units_for_doc(doc.doc_id)
        service._delete_raptor_for_doc(doc.doc_id)

        spans = service._split_into_units(
            text=normalized_text, unit_kind=normalized_unit_kind
        )
        for idx, (chunk_text, start_token, end_token) in enumerate(spans):
            text_ref = service._write_text_blob(chunk_text)
            context_text = service._build_context_text(
                source_type=doc.source_type,
                source_ref=doc.source_ref,
                scope=doc.scope,
                tags=doc.tags,
                title=doc.title,
                chunk_text=chunk_text,
            )
            context_text_ref = (
                service._write_text_blob(context_text) if context_text else None
            )
            fts_text = f"{context_text}\n\n{chunk_text}" if context_text else chunk_text

            unit_id = _stable_id(
                "retrievectl-unit",
                f"{doc.doc_id}:{normalized_unit_kind}:{idx}:{start_token}:{end_token}:{chunk_text[:80]}",
            )
            offsets = {
                "index": idx,
                "start_token": start_token,
                "end_token": end_token,
            }
            token_count = max(0, end_token - start_token)

            service.store.execute(
                """
                INSERT INTO retrievectl_units(
                    unit_id, doc_id, unit_kind, level, node_id, text_ref, context_text_ref,
                    fts_text, created_at, token_count, group_id, offsets_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    unit_id,
                    doc.doc_id,
                    normalized_unit_kind,
                    None,
                    None,
                    text_ref,
                    context_text_ref,
                    fts_text,
                    updated_ts,
                    token_count,
                    None,
                    json.dumps(
                        offsets,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=True,
                    ),
                ),
            )
            service._index_unit_fts(
                unit_id=unit_id,
                title=doc.title,
                fts_text=fts_text,
                tags=doc.tags,
            )

    return IngestResult(
        doc_id=doc.doc_id,
        source_ref=doc.source_ref,
        source_type=doc.source_type,
        unit_kind=normalized_unit_kind,
        unit_count=len(spans),
    )


def ingest_event(
    service: Any, event_type: str, payload: dict[str, Any]
) -> dict[str, Any] | None:
    normalized = str(event_type or "").strip().lower()
    data = dict(payload or {})

    if normalized == "artifact.created":
        ref = str(data.get("artifact_ref", data.get("ref", ""))).strip()
        if not ref:
            raise RetrieveCtlError(
                "INVALID_ARGUMENT", "artifact.created requires artifact_ref/ref"
            )
        return ingest_artifact(service, ref, data)

    if normalized == "episode.note.created":
        ref = (
            str(data.get("source_ref", data.get("event_ref", ""))).strip()
            or f"sess:event:{uuid4().hex}"
        )
        text = extract_ingest_text(data)
        return ingest_source(
            service,
            source_type="episode",
            source_ref=ref,
            text=text,
            scope=str(data.get("scope", "session")),
            tags=data.get("tags")
            if isinstance(data.get("tags"), list)
            else ["episode"],
            title=str(data.get("title", "episode-note")),
            unit_kind="chunk",
            created_at=str(data.get("created_at")) if data.get("created_at") else None,
        ).model_dump(mode="json")

    if normalized == "skill.ingested":
        skill_id = str(data.get("skill_id", "")).strip()
        version_hash = str(data.get("version_hash", "")).strip() or "unknown"
        source_ref = str(
            data.get("source_ref", f"skill:{skill_id}@{version_hash}")
        ).strip()
        return ingest_skill(
            service,
            skill_id=skill_id,
            version_hash=version_hash,
            source_ref=source_ref,
            meta=data,
        )

    if normalized == "mem.promoted":
        mem_id = str(data.get("mem_id", data.get("record_id", ""))).strip()
        text = extract_ingest_text(data)
        return ingest_memory(service, mem_id=mem_id, text=text, meta=data)

    return None


def build_raptor_tree(service: Any, doc_id: str) -> dict[str, Any]:
    normalized_doc_id = str(doc_id or "").strip()
    if not normalized_doc_id:
        raise RetrieveCtlError("INVALID_ARGUMENT", "doc_id is required")

    rows = service.store.execute(
        """
        SELECT unit_id, doc_id, text_ref, offsets_json
        FROM retrievectl_units
        WHERE doc_id = ? AND unit_kind = 'chunk'
        ORDER BY COALESCE(json_extract(offsets_json, '$.start_token'), 0), unit_id
        """,
        (normalized_doc_id,),
    ).fetchall()
    if not rows:
        raise RetrieveCtlError(
            "NOT_FOUND", f"no chunk units found for doc_id={normalized_doc_id}"
        )

    with service.record_store.transaction():
        service._delete_raptor_for_doc(normalized_doc_id)
        service.store.execute(
            "UPDATE retrievectl_units SET level = NULL, node_id = NULL WHERE doc_id = ?",
            (normalized_doc_id,),
        )

        leaf_ids = [str(row["unit_id"]) for row in rows]
        clusters: list[list[Any]] = []
        current: list[Any] = []
        for row in rows:
            current.append(row)
            if len(current) >= 4:
                clusters.append(current)
                current = []
        if current:
            clusters.append(current)

        internal_nodes: list[tuple[str, str, list[str]]] = []
        for idx, cluster in enumerate(clusters):
            node_id = _stable_id(
                "retrievectl-raptor", f"{normalized_doc_id}:internal:{idx}"
            )
            cluster_leaf_ids = [str(item["unit_id"]) for item in cluster]
            summary_lines = []
            for item in cluster:
                leaf_text = service._read_text_blob(str(item["text_ref"]))
                summary_lines.append(service._summarize_text(leaf_text, max_tokens=60))
            summary_text = "\n".join(line for line in summary_lines if line)
            summary_ref = service._write_text_blob(summary_text)

            service.store.execute(
                """
                INSERT INTO retrievectl_raptor_nodes(node_id, doc_id, parent_id, level_int, summary_text_ref, leaf_unit_ids_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    node_id,
                    normalized_doc_id,
                    None,
                    1,
                    summary_ref,
                    json.dumps(
                        cluster_leaf_ids,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=True,
                    ),
                    _iso_now(),
                ),
            )

            service.store.execute(
                """
                UPDATE retrievectl_units
                SET level = 'leaf', node_id = ?
                WHERE unit_id IN ({placeholders})
                """.format(placeholders=",".join("?" for _ in cluster_leaf_ids)),
                (node_id, *cluster_leaf_ids),
            )

            internal_unit_id = _stable_id(
                "retrievectl-unit", f"{normalized_doc_id}:internal:{node_id}"
            )
            context_text = service._build_context_text(
                source_type="doc",
                source_ref=f"node://{node_id}",
                scope="project",
                tags=["raptor", "internal"],
                title=f"RAPTOR internal node {idx + 1}",
                chunk_text=summary_text,
            )
            fts_text = (
                f"{context_text}\n\n{summary_text}" if context_text else summary_text
            )
            service.store.execute(
                """
                INSERT INTO retrievectl_units(
                    unit_id, doc_id, unit_kind, level, node_id, text_ref, context_text_ref,
                    fts_text, created_at, token_count, group_id, offsets_json
                ) VALUES (?, ?, 'chunk', 'internal', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    internal_unit_id,
                    normalized_doc_id,
                    node_id,
                    summary_ref,
                    service._write_text_blob(context_text) if context_text else None,
                    fts_text,
                    _iso_now(),
                    estimate_tokens(summary_text),
                    None,
                    json.dumps(
                        {
                            "index": idx,
                            "start_token": 0,
                            "end_token": estimate_tokens(summary_text),
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=True,
                    ),
                ),
            )
            service._index_unit_fts(
                unit_id=internal_unit_id,
                title=f"RAPTOR internal node {idx + 1}",
                fts_text=fts_text,
                tags=["raptor", "internal"],
            )
            internal_nodes.append((node_id, summary_text, cluster_leaf_ids))

        root_id = _stable_id("retrievectl-raptor", f"{normalized_doc_id}:root")
        root_summary = "\n".join(
            f"- {service._summarize_text(text, max_tokens=40)}"
            for _, text, _ in internal_nodes
        )
        root_ref = service._write_text_blob(root_summary)

        service.store.execute(
            """
            INSERT INTO retrievectl_raptor_nodes(node_id, doc_id, parent_id, level_int, summary_text_ref, leaf_unit_ids_json, created_at)
            VALUES (?, ?, ?, 0, ?, ?, ?)
            """,
            (
                root_id,
                normalized_doc_id,
                None,
                root_ref,
                json.dumps(
                    leaf_ids,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=True,
                ),
                _iso_now(),
            ),
        )

        for node_id, _, _ in internal_nodes:
            service.store.execute(
                "UPDATE retrievectl_raptor_nodes SET parent_id = ? WHERE node_id = ?",
                (root_id, node_id),
            )

        root_unit_id = _stable_id(
            "retrievectl-unit", f"{normalized_doc_id}:root:{root_id}"
        )
        root_context = service._build_context_text(
            source_type="doc",
            source_ref=f"node://{root_id}",
            scope="project",
            tags=["raptor", "root"],
            title="RAPTOR root summary",
            chunk_text=root_summary,
        )
        root_fts = f"{root_context}\n\n{root_summary}" if root_context else root_summary
        service.store.execute(
            """
            INSERT INTO retrievectl_units(
                unit_id, doc_id, unit_kind, level, node_id, text_ref, context_text_ref,
                fts_text, created_at, token_count, group_id, offsets_json
            ) VALUES (?, ?, 'chunk', 'root', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                root_unit_id,
                normalized_doc_id,
                root_id,
                root_ref,
                service._write_text_blob(root_context) if root_context else None,
                root_fts,
                _iso_now(),
                estimate_tokens(root_summary),
                None,
                json.dumps(
                    {
                        "index": 0,
                        "start_token": 0,
                        "end_token": estimate_tokens(root_summary),
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=True,
                ),
            ),
        )
        service._index_unit_fts(
            unit_id=root_unit_id,
            title="RAPTOR root summary",
            fts_text=root_fts,
            tags=["raptor", "root"],
        )

    return RaptorBuildResult(
        doc_id=normalized_doc_id,
        root_node_id=root_id,
        internal_node_count=len(internal_nodes),
        leaf_count=len(leaf_ids),
    ).model_dump(mode="json")


def group_long_units(
    service: Any, corpus_id: str, grouping_policy: dict[str, Any] | None = None
) -> dict[str, Any]:
    normalized_corpus = str(corpus_id or "").strip()
    if not normalized_corpus:
        raise RetrieveCtlError("INVALID_ARGUMENT", "corpus_id is required")

    policy = dict(grouping_policy or {})
    min_tokens = int(
        policy.get("min_tokens", service.config.defaults.doc_group_min_tokens)
    )
    max_tokens = int(
        policy.get("max_tokens", service.config.defaults.doc_group_max_tokens)
    )

    docs = service.store.execute(
        """
        SELECT doc_id FROM retrievectl_docs
        WHERE corpus_id = ?
           OR EXISTS (
                SELECT 1
                FROM json_each(retrievectl_docs.tags_json)
                WHERE json_each.value = ?
           )
        ORDER BY doc_id
        """,
        (normalized_corpus, normalized_corpus),
    ).fetchall()
    if not docs:
        return GroupLongUnitsResult(
            corpus_id=normalized_corpus, docs_updated=0, groups_created=0
        ).model_dump(mode="json")

    groups_created = 0
    with service.record_store.transaction():
        for row in docs:
            doc_id = str(row["doc_id"])
            full_text = read_doc_text(service, doc_id)
            spans = service._split_by_token_windows(
                text=full_text,
                min_tokens=min_tokens,
                max_tokens=max_tokens,
                prefer_paragraphs=True,
            )
            service._delete_units_for_doc(doc_id=doc_id, unit_kind="doc_group")

            for idx, (segment, start_token, end_token) in enumerate(spans):
                _insert_doc_group_unit(
                    service,
                    corpus_id=normalized_corpus,
                    doc_id=doc_id,
                    index=idx,
                    segment=segment,
                    start_token=start_token,
                    end_token=end_token,
                )
                groups_created += 1

    return GroupLongUnitsResult(
        corpus_id=normalized_corpus,
        docs_updated=len(docs),
        groups_created=groups_created,
    ).model_dump(mode="json")


def _insert_doc_group_unit(
    service: Any,
    *,
    corpus_id: str,
    doc_id: str,
    index: int,
    segment: str,
    start_token: int,
    end_token: int,
) -> None:
    unit_id = _stable_id(
        "retrievectl-unit",
        f"{doc_id}:doc_group:{index}:{start_token}:{end_token}",
    )
    group_id = f"{doc_id}:g{index + 1}"
    text_ref = service._write_text_blob(segment)
    tags = ["longrag", "doc_group", corpus_id]
    title = f"Doc group {index + 1}"
    context_text = service._build_context_text(
        source_type="doc",
        source_ref=group_id,
        scope="project",
        tags=tags,
        title=title,
        chunk_text=segment,
    )
    context_ref = service._write_text_blob(context_text) if context_text else None
    fts_text = f"{context_text}\n\n{segment}" if context_text else segment
    offsets = {"index": index, "start_token": start_token, "end_token": end_token}
    service.store.execute(
        """
        INSERT INTO retrievectl_units(
            unit_id, doc_id, unit_kind, level, node_id, text_ref, context_text_ref,
            fts_text, created_at, token_count, group_id, offsets_json
        ) VALUES (?, ?, 'doc_group', NULL, NULL, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            unit_id,
            doc_id,
            text_ref,
            context_ref,
            fts_text,
            _iso_now(),
            max(0, end_token - start_token),
            group_id,
            json.dumps(
                offsets,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ),
        ),
    )
    service._index_unit_fts(unit_id=unit_id, title=title, fts_text=fts_text, tags=tags)


def extract_ingest_text(payload: dict[str, Any]) -> str:
    for key in ("text", "content", "body", "snippet", "markdown"):
        value = payload.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    raise RetrieveCtlError(
        "INVALID_ARGUMENT", "ingest payload must include text/content/body"
    )


def read_doc_text(service: Any, doc_id: str) -> str:
    rows = service.store.execute(
        """
        SELECT text_ref, offsets_json
        FROM retrievectl_units
        WHERE doc_id = ? AND unit_kind IN ('chunk', 'document')
        ORDER BY COALESCE(json_extract(offsets_json, '$.start_token'), 0), unit_id
        """,
        (doc_id,),
    ).fetchall()
    parts = [service._read_text_blob(str(row["text_ref"])) for row in rows]
    return "\n\n".join(part for part in parts if part.strip())
