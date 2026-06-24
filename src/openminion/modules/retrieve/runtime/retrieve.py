from __future__ import annotations

import re
import logging
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Mapping, Sequence

from openminion.modules.memory.config import RankingConfig, merge_ranking_config
from openminion.modules.memory.runtime.scorer import (
    recency_score,
    score_records,
)

from . import expansion as expansion_ops
from . import ingestion as ingestion_ops
from . import retrieval as retrieval_ops
from ..config import RetrieveCtlConfig, load_config, resolve_default_config_path
from ..interfaces import (
    RETRIEVE_INTERFACE_VERSION,
    ensure_retrieve_storage_compatibility,
)
from .retrieval import (
    RetrievalContext,
    resolve_retrieval_strategy,
)
from ..schemas import (
    IngestResult,
    RetrievalFilters,
    RetrievalStrategy,
    RetrievedItem,
)
from .storage import (
    StorageOpsContext,
    delete_raptor_for_doc,
    delete_units_for_doc,
    index_unit_fts,
    read_text_blob,
    record_run,
    row_exists,
    write_text_blob,
)
from .unitization import (
    build_context_text,
    split_by_token_windows,
    split_into_units,
    summarize_text,
    trim_tokens,
)
from ..diagnostics.events import emit_query_metrics, format_score_breakdown
from .time import parse_iso_timestamp
from openminion.modules.storage.backends.blob_store import BlobStoreFS
from ..storage.store import SQLiteRetrieveStore

from openminion.base.time import utc_now_iso as _iso_now


_UNIT_REF_RE = re.compile(r"#u=([0-9a-f\-]+)$")
_LOGGER = logging.getLogger(__name__)


class RetrieveCtl:
    """Selective evidence retrieval engine with contextual, RAPTOR, and LongRAG modes."""

    contract_version = RETRIEVE_INTERFACE_VERSION

    def __init__(
        self,
        config: str | Path | dict[str, Any] | RetrieveCtlConfig | None = None,
        vector_adapter: Any = None,
        ranking_config: RankingConfig | None = None,
        telemetryctl: Any | None = None,
        telemetry_session_id: str | None = None,
        telemetry_turn_id: str | None = None,
    ) -> None:
        if config is None:
            config = resolve_default_config_path()
        self.config = load_config(config)
        self.vector_adapter = vector_adapter
        self._ranking_config = (
            ranking_config
            if ranking_config is not None
            else merge_ranking_config(None, retrieve_defaults=self.config.defaults)
        )
        self.blob_store = BlobStoreFS(self.config.storage.blob_root)
        self.store = SQLiteRetrieveStore(
            self.config.storage.sqlite_path, wal=self.config.storage.wal_mode
        )
        ensure_retrieve_storage_compatibility(self.store, strict=True)
        self.record_store = self.store.record_store
        self._fts_enabled = self.store.ensure_schema()
        self._storage_context = StorageOpsContext(
            blob_store=self.blob_store,
            store=self.store,
            fts_enabled=self._fts_enabled,
        )
        self._telemetryctl = telemetryctl
        self._telemetry_session_id = str(telemetry_session_id or "").strip() or None
        self._telemetry_turn_id = str(telemetry_turn_id or "").strip() or None

    def close(self) -> None:
        self.store.close()

    def set_ranking_config(self, ranking_config: RankingConfig) -> None:
        self._ranking_config = ranking_config

    def set_telemetry_context(
        self,
        *,
        session_id: str,
        turn_id: str,
    ) -> None:
        self._telemetry_session_id = str(session_id or "").strip() or None
        self._telemetry_turn_id = str(turn_id or "").strip() or None

    def _resolve_telemetry_session_id(self, *, scope: Mapping[str, Any]) -> str:
        if self._telemetry_session_id:
            return str(self._telemetry_session_id)
        session_id = str(scope.get("session_id", "") or "").strip()
        if session_id:
            return session_id
        scope_value = str(scope.get("scope", "") or "").strip()
        if scope_value.startswith("session:"):
            return scope_value.split(":", 1)[1]
        return ""

    def _resolve_telemetry_turn_id(self, *, scope: Mapping[str, Any]) -> str:
        if self._telemetry_turn_id:
            return str(self._telemetry_turn_id)
        return str(scope.get("turn_id", "") or "").strip()

    def _emit_query_metrics(
        self,
        *,
        session_id: str,
        turn_id: str,
        operation: str,
        result_count: int,
        latency_ms: float,
        token_estimate: int,
        status: str = "ok",
        extra: dict[str, Any] | None = None,
    ) -> None:
        emit_query_metrics(
            telemetryctl=self._telemetryctl,
            session_id=session_id,
            turn_id=turn_id,
            operation=operation,
            result_count=result_count,
            latency_ms=latency_ms,
            token_estimate=token_estimate,
            status=status,
            extra=extra,
        )

    def status(self) -> dict[str, Any]:
        sqlite_path = Path(self.config.storage.sqlite_path)
        blob_root = Path(self.config.storage.blob_root)
        return {
            "ok": True,
            "storage": {
                "sqlite_path": str(sqlite_path),
                "sqlite_exists": sqlite_path.exists(),
                "blob_root": str(blob_root),
                "blob_root_exists": blob_root.exists(),
                "wal_mode": bool(self.config.storage.wal_mode),
            },
            "vector_adapter": bool(self.vector_adapter),
        }

    def retrieve(
        self,
        *,
        query: str,
        purpose: str,
        scope: dict[str, Any],
        k: int,
        strategy: str,
        filters: dict[str, Any] | RetrievalFilters | None = None,
    ) -> list[dict[str, Any]]:
        started = perf_counter()
        normalized_query = str(query or "").strip()
        if not normalized_query:
            return []

        retrieval_k = max(1, int(k))
        retrieval_filters = (
            filters
            if isinstance(filters, RetrievalFilters)
            else RetrievalFilters.model_validate(filters or {})
        )
        resolved_strategy = self._resolve_strategy(
            query=normalized_query,
            purpose=str(purpose or "act"),
            strategy=str(strategy or "auto"),
            scope=scope,
            filters=retrieval_filters,
        )

        candidates = self._generate_candidates(
            query=normalized_query,
            scope=scope,
            filters=retrieval_filters,
            limit=max(retrieval_k, int(self.config.defaults.lexical_candidate_count)),
        )
        candidates = score_records(
            candidates,
            ranking_config=self._ranking_config,
            now=datetime.now(timezone.utc),
        )
        retrieval_ops.apply_title_identity_boost_in_place(
            candidates=candidates,
            query=normalized_query,
            max_boost=self.config.defaults.title_identity_max_boost,
        )
        for candidate in candidates:
            unified_score = float(
                candidate.get(
                    "unified_score",
                    candidate.get("score", 0.0),
                )
                or 0.0
            )
            candidate["score"] = unified_score
            candidate["why"] = self._format_score_breakdown(candidate)

        if str(purpose).lower() == "verify":
            candidates = [
                item
                for item in candidates
                if float(item.get("unified_score", item.get("score", 0.0)) or 0.0)
                >= float(self.config.defaults.verify_min_score)
            ]

        selected = self._select_candidates(
            candidates=candidates,
            strategy=resolved_strategy,
            k=retrieval_k,
        )

        items = [
            self._to_retrieved_item(candidate=item, strategy=resolved_strategy)
            for item in selected
        ]
        telemetry_session_id = self._resolve_telemetry_session_id(scope=scope)
        telemetry_turn_id = self._resolve_telemetry_turn_id(scope=scope)
        elapsed_ms = (perf_counter() - started) * 1000.0
        telemetry_extra = {
            "strategy": str(resolved_strategy),
            "purpose": str(purpose or "").strip().lower() or "act",
            "requested_strategy": str(strategy or "").strip().lower() or "auto",
        }
        token_estimate = len(normalized_query.split())
        self._emit_query_metrics(
            session_id=telemetry_session_id,
            turn_id=telemetry_turn_id,
            operation="query",
            result_count=len(candidates),
            latency_ms=elapsed_ms,
            token_estimate=token_estimate,
            extra=telemetry_extra,
        )
        self._emit_query_metrics(
            session_id=telemetry_session_id,
            turn_id=telemetry_turn_id,
            operation="rerank",
            result_count=len(items),
            latency_ms=elapsed_ms,
            token_estimate=token_estimate,
            extra=telemetry_extra,
        )
        if not items:
            self._emit_query_metrics(
                session_id=telemetry_session_id,
                turn_id=telemetry_turn_id,
                operation="fallback",
                result_count=0,
                latency_ms=elapsed_ms,
                token_estimate=token_estimate,
                extra={**telemetry_extra, "reason": "no_results"},
            )
        self._record_run(
            session_id=str(scope.get("session_id") or ""),
            query=normalized_query,
            strategy=resolved_strategy,
            k=retrieval_k,
            unit_ids=[
                str(item.meta.get("unit_id", ""))
                for item in items
                if str(item.meta.get("unit_id", "")).strip()
            ],
        )
        return [item.model_dump(mode="json") for item in items]

    def expand(self, *, ref: str, mode: str, k: int) -> list[dict[str, Any]]:
        normalized_ref = str(ref or "").strip()
        if not normalized_ref:
            return []
        target_k = max(1, int(k))
        normalized_mode = str(mode or "window").strip().lower()

        if normalized_ref.startswith("node://"):
            out = self._expand_node(
                node_id=normalized_ref[len("node://") :], k=target_k
            )
            return [item.model_dump(mode="json") for item in out]

        if normalized_ref.startswith("group://"):
            out = self._expand_group(
                group_id=normalized_ref[len("group://") :], k=target_k
            )
            return [item.model_dump(mode="json") for item in out]

        unit_id = self._parse_unit_id_from_ref(normalized_ref)
        if unit_id is None:
            unit_id = (
                normalized_ref
                if self._row_exists("retrievectl_units", "unit_id", normalized_ref)
                else None
            )
        if unit_id is None:
            return []

        if normalized_mode == "document":
            out = self._expand_document(unit_id=unit_id, k=target_k)
        else:
            out = self._expand_window(unit_id=unit_id, k=target_k)
        return [item.model_dump(mode="json") for item in out]

    def explain(self, item: dict[str, Any] | RetrievedItem | str) -> dict[str, Any]:
        return expansion_ops.explain_item(self, item)

    def ingest_artifact(
        self, artifact_ref: str, meta: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return ingestion_ops.ingest_artifact(self, artifact_ref, meta)

    def ingest_skill(
        self,
        skill_id: str,
        version_hash: str,
        source_ref: str,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return ingestion_ops.ingest_skill(
            self,
            skill_id=skill_id,
            version_hash=version_hash,
            source_ref=source_ref,
            meta=meta,
        )

    def ingest_memory(
        self, mem_id: str, text: str, meta: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return ingestion_ops.ingest_memory(self, mem_id=mem_id, text=text, meta=meta)

    def ingest_source(
        self,
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
        return ingestion_ops.ingest_source(
            self,
            source_type=source_type,
            source_ref=source_ref,
            text=text,
            scope=scope,
            tags=tags,
            title=title,
            corpus_id=corpus_id,
            unit_kind=unit_kind,
            created_at=created_at,
        )

    def ingest_event(
        self, event_type: str, payload: dict[str, Any]
    ) -> dict[str, Any] | None:
        return ingestion_ops.ingest_event(self, event_type, payload)

    def build_raptor_tree(self, doc_id: str) -> dict[str, Any]:
        return ingestion_ops.build_raptor_tree(self, doc_id)

    def group_long_units(
        self, corpus_id: str, grouping_policy: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return ingestion_ops.group_long_units(self, corpus_id, grouping_policy)

    def record_hits(
        self, unit_ids: Sequence[str], *, observed_at: str | None = None
    ) -> int:
        timestamp = str(observed_at or _iso_now())
        return self.store.record_hits(unit_ids, observed_at=timestamp)

    def set_feedback_scores(self, scores_by_unit: Mapping[str, float]) -> int:
        return self.store.set_feedback_scores(scores_by_unit)

    def feedback_state(self, unit_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
        return self.store.get_feedback_state(unit_ids)

    def apply_decay(
        self,
        *,
        halflife_days: int | None = None,
        min_feedback_score: float | None = None,
    ) -> int:
        resolved_halflife = int(
            halflife_days
            if halflife_days is not None
            else self.config.defaults.feedback_decay_halflife_days
        )
        resolved_min = float(
            min_feedback_score
            if min_feedback_score is not None
            else self.config.defaults.decay_min_feedback_score
        )
        try:
            return int(
                self.store.apply_feedback_decay(
                    halflife_days=max(1, resolved_halflife),
                    min_feedback_score=max(0.0, min(1.0, resolved_min)),
                )
            )
        except Exception as exc:
            _LOGGER.warning(
                "retrieve.apply_decay failed halflife_days=%s min_feedback_score=%s error=%s",
                resolved_halflife,
                resolved_min,
                exc,
            )
            return 0

    def _write_text_blob(self, text: str) -> str:
        return write_text_blob(self._storage_context, text)

    def _read_text_blob(self, text_ref: str) -> str:
        return read_text_blob(self._storage_context, text_ref)

    def _index_unit_fts(
        self, *, unit_id: str, title: str, fts_text: str, tags: Sequence[str]
    ) -> None:
        index_unit_fts(
            self._storage_context,
            unit_id=unit_id,
            title=title,
            fts_text=fts_text,
            tags=tags,
        )

    def _delete_units_for_doc(self, doc_id: str, unit_kind: str | None = None) -> None:
        delete_units_for_doc(self._storage_context, doc_id, unit_kind)

    def _delete_raptor_for_doc(self, doc_id: str) -> None:
        delete_raptor_for_doc(self._storage_context, doc_id)

    def _row_exists(self, table: str, key: str, value: str) -> bool:
        return row_exists(self._storage_context, table, key, value)

    def _record_run(
        self, *, session_id: str, query: str, strategy: str, k: int, unit_ids: list[str]
    ) -> None:
        record_run(
            self._storage_context,
            session_id=session_id,
            query=query,
            strategy=strategy,
            k=k,
            unit_ids=unit_ids,
        )

    def _generate_candidates(
        self,
        *,
        query: str,
        scope: dict[str, Any],
        filters: RetrievalFilters,
        limit: int,
    ) -> list[dict[str, Any]]:
        return retrieval_ops.generate_candidates(
            self,
            query=query,
            scope=scope,
            filters=filters,
            limit=limit,
        )

    def _select_candidates(
        self,
        *,
        candidates: list[dict[str, Any]],
        strategy: RetrievalStrategy,
        k: int,
    ) -> list[dict[str, Any]]:
        return retrieval_ops.select_candidates(
            self,
            candidates=candidates,
            strategy=strategy,
            k=k,
        )

    def _select_candidates_semantic(
        self,
        *,
        candidates: list[dict[str, Any]],
        k: int,
    ) -> list[dict[str, Any]]:
        return retrieval_ops.select_candidates_semantic(
            self,
            candidates=candidates,
            k=k,
        )

    def _to_retrieved_item(
        self, *, candidate: dict[str, Any], strategy: RetrievalStrategy
    ) -> RetrievedItem:
        return retrieval_ops.to_retrieved_item(
            self,
            candidate=candidate,
            strategy=strategy,
        )

    def _search_rows(
        self,
        *,
        tokens: list[str],
        allowed_scopes: list[str],
        filters: RetrievalFilters,
        limit: int,
    ) -> list[Mapping[str, Any]]:
        return retrieval_ops.search_rows(
            self,
            tokens=tokens,
            allowed_scopes=allowed_scopes,
            filters=filters,
            limit=limit,
        )

    def _recent_rows(
        self, *, allowed_scopes: list[str], filters: RetrievalFilters, limit: int
    ) -> list[Mapping[str, Any]]:
        return retrieval_ops.recent_rows(
            self,
            allowed_scopes=allowed_scopes,
            filters=filters,
            limit=limit,
        )

    def _candidate_from_row(
        self, row: Mapping[str, Any], inherited_score: float
    ) -> dict[str, Any]:
        return retrieval_ops.candidate_from_row(self, row, inherited_score)

    def _lookup_unit_row(self, unit_id: str) -> Mapping[str, Any] | None:
        return expansion_ops.lookup_unit_row(self, unit_id)

    def _lookup_unit_rows_batch(
        self, unit_ids: list[str]
    ) -> dict[str, Mapping[str, Any]]:
        return expansion_ops.lookup_unit_rows_batch(self, unit_ids)

    def _leaf_ids_for_node(self, node_id: str) -> list[str]:
        return expansion_ops.leaf_ids_for_node(self, node_id)

    def _expand_node(self, *, node_id: str, k: int) -> list[RetrievedItem]:
        return expansion_ops.expand_node(self, node_id=node_id, k=k)

    def _expand_group(self, *, group_id: str, k: int) -> list[RetrievedItem]:
        return expansion_ops.expand_group(self, group_id=group_id, k=k)

    def _expand_window(self, *, unit_id: str, k: int) -> list[RetrievedItem]:
        return expansion_ops.expand_window(self, unit_id=unit_id, k=k)

    def _expand_document(self, *, unit_id: str, k: int) -> list[RetrievedItem]:
        return expansion_ops.expand_document(self, unit_id=unit_id, k=k)

    def _split_into_units(
        self, *, text: str, unit_kind: str
    ) -> list[tuple[str, int, int]]:
        return split_into_units(
            text=text,
            unit_kind=unit_kind,
            chunk_min_tokens=self.config.defaults.chunk_min_tokens,
            chunk_max_tokens=self.config.defaults.chunk_max_tokens,
            doc_group_min_tokens=self.config.defaults.doc_group_min_tokens,
            doc_group_max_tokens=self.config.defaults.doc_group_max_tokens,
        )

    def _split_by_token_windows(
        self,
        *,
        text: str,
        min_tokens: int,
        max_tokens: int,
        prefer_paragraphs: bool,
    ) -> list[tuple[str, int, int]]:
        return split_by_token_windows(
            text=text,
            min_tokens=min_tokens,
            max_tokens=max_tokens,
            prefer_paragraphs=prefer_paragraphs,
        )

    def _build_context_text(
        self,
        *,
        source_type: str,
        source_ref: str,
        scope: str,
        tags: Sequence[str],
        title: str,
        chunk_text: str,
    ) -> str:
        return build_context_text(
            contextual_enabled=self.config.defaults.contextual_enabled,
            source_type=source_type,
            source_ref=source_ref,
            scope=scope,
            tags=tags,
            title=title,
            chunk_text=chunk_text,
        )

    def _summarize_text(self, text: str, *, max_tokens: int) -> str:
        return summarize_text(text, max_tokens=max_tokens)

    def _trim_tokens(self, text: str, *, max_tokens: int) -> str:
        return trim_tokens(text, max_tokens=max_tokens)

    def _recency_score(self, created_at: str) -> float:
        created = parse_iso_timestamp(created_at)
        if created is None:
            return 0.5
        age_h = max(
            0.0,
            (datetime.now(timezone.utc) - created).total_seconds() / 3600.0,
        )
        half_life_hours = float(self.config.defaults.recency_half_life_hours)
        if half_life_hours <= 0:
            return 0.0
        return recency_score(age_h / 24.0, half_life_hours / 24.0)

    def _normalize_source_type(self, source_type: str) -> str:
        value = str(source_type or "doc").strip().lower()
        if value in {"episode", "artifact", "skill", "mem", "doc"}:
            return value
        return "doc"

    def _normalize_scope(self, scope: str) -> str:
        value = str(scope or "project").strip().lower()
        base = value.split(":", 1)[0] if ":" in value else value
        if base in {"session", "agent", "global", "project"}:
            return base
        return "project"

    def _normalize_unit_kind(self, unit_kind: str) -> str:
        value = str(unit_kind or "chunk").strip().lower()
        if value in {"chunk", "doc_group", "document"}:
            return value
        return "chunk"

    def _normalize_level(self, level: str) -> str:
        value = str(level or "none").strip().lower()
        if value in {"none", "root", "internal", "leaf"}:
            return value
        return "none"

    def _resolve_strategy(
        self,
        *,
        query: str,
        purpose: str,
        strategy: str,
        scope: dict[str, Any],
        filters: RetrievalFilters,
    ) -> RetrievalStrategy:
        ctx = RetrievalContext(
            query=query,
            purpose=purpose,
            requested_strategy=strategy,
            scope=scope,
            filters=filters,
            k=1,
        )
        return resolve_retrieval_strategy(
            requested_strategy=ctx.requested_strategy,
            purpose=ctx.purpose,
            query=ctx.query,
            scope=ctx.scope,
            filters=ctx.filters,
            default_strategy=str(self.config.defaults.strategy),
            vector_adapter_enabled=self.vector_adapter is not None,
            embeddings_enabled=self.config.defaults.embeddings_enabled,
        )

    def _to_rlm_source(self, source_type: str) -> str:
        if source_type == "mem":
            return "sm"
        if source_type == "skill":
            return "skill"
        if source_type == "episode":
            return "session"
        return "em"

    def _allowed_scopes(self, scope: dict[str, Any]) -> list[str]:
        if not isinstance(scope, dict):
            return ["session", "agent", "global", "project"]

        explicit: list[str] = []
        for key in ["session", "agent", "global", "project"]:
            raw = scope.get(key)
            if (
                isinstance(raw, bool)
                and raw
                or isinstance(raw, str)
                and raw.strip().lower()
                in {
                    "1",
                    "true",
                    "yes",
                    "on",
                    "include",
                }
            ):
                explicit.append(key)

        scope_name = scope.get("scope")
        if isinstance(scope_name, str) and scope_name.strip().lower() in {
            "session",
            "agent",
            "global",
            "project",
        }:
            explicit.append(scope_name.strip().lower())

        if explicit:
            return sorted(set(explicit))

        return ["session", "agent", "global", "project"]

    def _format_score_breakdown(self, candidate: Mapping[str, Any]) -> str:
        return format_score_breakdown(candidate)

    def _dedupe_candidates(
        self, items: Sequence[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        seen: set[str] = set()
        out: list[dict[str, Any]] = []
        for item in items:
            unit_id = str(item.get("unit_id", "")).strip()
            if not unit_id or unit_id in seen:
                continue
            seen.add(unit_id)
            out.append(item)
        return out

    def _parse_unit_id_from_ref(self, ref: str) -> str | None:
        match = _UNIT_REF_RE.search(str(ref or "").strip())
        if not match:
            return None
        return match.group(1)

    def _extract_ingest_text(self, payload: dict[str, Any]) -> str:
        return ingestion_ops.extract_ingest_text(payload)

    def _read_doc_text(self, doc_id: str) -> str:
        return ingestion_ops.read_doc_text(self, doc_id)
