import json
import math
from datetime import datetime, timezone
from typing import Any

from .schemas import (
    RetrievedContext,
    RetrievalEval,
    RetrievalFilters,
    RetrievalQuality,
    RLMConstraints,
    RetrievalStrategy,
)
from .payloads import _parse_iso, _tokenize


def retrieve(
    self,
    *,
    session_id: str,
    agent_id: str,
    query: str,
    k: int,
    purpose: str = "act",
    strategy: RetrievalStrategy = "auto",
    filters: RetrievalFilters | dict[str, Any] | None = None,
) -> list[RetrievedContext]:
    retrieval_filters = (
        filters
        if isinstance(filters, RetrievalFilters)
        else RetrievalFilters.model_validate(filters or {})
    )
    resolved_strategy = strategy if strategy != "auto" else retrieval_filters.strategy
    if resolved_strategy == "auto":
        resolved_strategy = self._resolve_retrieval_strategy(
            query=query, purpose=purpose, constraints=None
        )

    if self._retrievectl is not None:
        external = self._retrieve_external(
            session_id=session_id,
            agent_id=agent_id,
            query=query,
            purpose=purpose,
            strategy=resolved_strategy,
            k=k,
            filters=retrieval_filters,
        )
        if external:
            return self._filter_retrieval_items(external, retrieval_filters, k)

    local = self._retrieve_local(
        session_id=session_id,
        agent_id=agent_id,
        query=query,
        k=k,
        strategy=resolved_strategy,
        retrieval_filters=retrieval_filters,
    )
    return local


def expand(self, ref: str, mode: str, k: int) -> list[RetrievedContext]:
    target_k = max(1, int(k))
    normalized_mode = str(mode or "window").strip().lower()

    if self._retrievectl is not None and hasattr(self._retrievectl, "expand"):
        try:
            rows = self._retrievectl.expand(ref=ref, mode=normalized_mode, k=target_k)
        except TypeError:
            rows = self._retrievectl.expand(ref, normalized_mode, target_k)  # type: ignore[misc]
        except Exception:  # noqa: BLE001
            rows = []
        items = self._normalize_retrieval_rows(rows, strategy="auto")
        if items:
            return items[:target_k]

    if self._artifactctl is not None and str(ref).startswith("artifact://"):
        text = self._artifact_text_excerpt(
            ref=ref, fallback_meta={"mime": "text/plain"}
        )
        if not text.strip():
            return []
        units = [item.strip() for item in text.split("\n\n") if item.strip()]
        if not units:
            units = [item.strip() for item in text.split("\n") if item.strip()]
        out: list[RetrievedContext] = []
        for idx, item in enumerate(units[:target_k], start=1):
            out.append(
                RetrievedContext(
                    source="em",
                    ref_id=f"{ref}#expand-{idx}",
                    text=item,
                    score=1.0,
                    recency_score=0.0,
                    unit_kind="chunk" if normalized_mode != "document" else "document",
                    retrieval_strategy="auto",
                    metadata={"mode": normalized_mode, "parent_ref": ref},
                )
            )
        return out

    return []


def _resolve_retrieval_strategy(
    self,
    *,
    query: str,
    purpose: str,
    constraints: RLMConstraints | None,
) -> RetrievalStrategy:
    if constraints is not None and constraints.retrieval_strategy != "auto":
        return constraints.retrieval_strategy

    return "contextual"


def _alternate_strategy(self, strategy: RetrievalStrategy) -> RetrievalStrategy:
    if strategy == "contextual":
        return "raptor"
    if strategy == "raptor":
        return "longrag_doc_group"
    return "contextual"


def _retrieve_external(
    self,
    *,
    session_id: str,
    agent_id: str,
    query: str,
    purpose: str,
    strategy: RetrievalStrategy,
    k: int,
    filters: RetrievalFilters,
) -> list[RetrievedContext]:
    if self._retrievectl is None:
        return []

    scope = {"session_id": session_id, "agent_id": agent_id}
    if filters.scope:
        scope.update({str(key): str(value) for key, value in filters.scope.items()})

    rows: list[Any]
    try:
        rows = self._retrievectl.retrieve(
            query=query,
            purpose=purpose,
            scope=scope,
            k=max(1, int(k)),
            strategy=strategy,
            filters=filters.model_dump(mode="json"),
        )
    except TypeError:
        try:
            rows = self._retrievectl.retrieve(
                query=query,
                purpose=purpose,
                scope=scope,
                k=max(1, int(k)),
                strategy=strategy,
            )
        except Exception:  # noqa: BLE001
            rows = []
    except Exception:  # noqa: BLE001
        rows = []

    return self._normalize_retrieval_rows(rows, strategy=strategy)


def _normalize_retrieval_rows(
    self, rows: Any, *, strategy: RetrievalStrategy
) -> list[RetrievedContext]:
    out: list[RetrievedContext] = []
    if not isinstance(rows, list):
        return out

    for raw in rows:
        item = self._to_plain(raw)
        text = str(item.get("text", item.get("content", ""))).strip()
        if not text:
            continue
        out.append(
            RetrievedContext(
                source=self._normalize_source(item.get("source")),
                ref_id=str(
                    item.get(
                        "ref_id",
                        item.get("ref", item.get("id", "retrieve://unknown")),
                    )
                ),
                text=text[: self.config.retrieval.text_snippet_chars],
                score=float(item.get("score", 0.0) or 0.0),
                recency_score=float(item.get("recency_score", 0.0) or 0.0),
                tags=[str(tag) for tag in item.get("tags", [])]
                if isinstance(item.get("tags"), list)
                else [],
                created_at=item.get("created_at") or item.get("updated_at"),
                unit_kind=self._normalize_unit_kind(item.get("unit_kind")),
                retrieval_strategy=self._normalize_strategy(
                    item.get("retrieval_strategy") or strategy
                ),
                raptor_level=self._normalize_raptor_level(item.get("raptor_level")),
                node_id=(
                    str(item.get("node_id"))
                    if item.get("node_id") is not None
                    else None
                ),
                doc_group_id=(
                    str(item.get("doc_group_id"))
                    if item.get("doc_group_id") is not None
                    else None
                ),
                trust_score=float(
                    item.get(
                        "trust_score",
                        self._trust_score_from_source(item.get("source")),
                    )
                    or 0.0
                ),
                metadata=item,
            )
        )

    out.sort(key=lambda row: (row.score, row.recency_score), reverse=True)
    return out


def _retrieve_local(
    self,
    *,
    session_id: str,
    agent_id: str,
    query: str,
    k: int,
    strategy: RetrievalStrategy,
    retrieval_filters: RetrievalFilters,
) -> list[RetrievedContext]:
    target_k = max(1, int(k))

    collected: list[RetrievedContext] = []
    if "sm" in retrieval_filters.include_sources:
        collected.extend(
            self._retrieve_semantic(
                session_id=session_id,
                agent_id=agent_id,
                query=query,
                strategy=strategy,
            )
        )
    if "em" in retrieval_filters.include_sources:
        collected.extend(
            self._retrieve_episodic(
                session_id=session_id,
                agent_id=agent_id,
                query=query,
                strategy=strategy,
            )
        )
    if "skill" in retrieval_filters.include_sources:
        collected.extend(
            self._retrieve_skills(agent_id=agent_id, query=query, strategy=strategy)
        )

    return self._filter_retrieval_items(collected, retrieval_filters, target_k)


def _filter_retrieval_items(
    self,
    items: list[RetrievedContext],
    retrieval_filters: RetrievalFilters,
    target_k: int,
) -> list[RetrievedContext]:
    collected = list(items)
    if retrieval_filters.tags:
        tags = {item.strip().lower() for item in retrieval_filters.tags if item.strip()}
        if tags:
            collected = [
                item
                for item in collected
                if (
                    not item.tags
                    or tags.intersection(
                        {tag.strip().lower() for tag in item.tags if tag.strip()}
                    )
                )
            ]

    if retrieval_filters.time_window_hours is not None:
        now = datetime.now(timezone.utc)
        window_s = int(retrieval_filters.time_window_hours) * 3600
        filtered_items: list[RetrievedContext] = []
        for item in collected:
            created = _parse_iso(item.created_at)
            if created is None:
                filtered_items.append(item)
                continue
            age_s = (now - created).total_seconds()
            if age_s <= window_s:
                filtered_items.append(item)
        collected = filtered_items

    collected.sort(key=lambda row: (row.score, row.recency_score), reverse=True)

    deduped: list[RetrievedContext] = []
    seen_refs: set[str] = set()
    for item in collected:
        key = f"{item.source}:{item.ref_id}"
        if key in seen_refs:
            continue
        seen_refs.add(key)
        deduped.append(item)
        if len(deduped) >= target_k:
            break
    return deduped


def _evaluate_retrieval_quality(self, items: list[RetrievedContext]) -> RetrievalEval:
    if not items:
        return RetrievalEval(
            quality="BAD",
            action="no_items",
            score_histogram={
                "0.0-0.2": 0,
                "0.2-0.4": 0,
                "0.4-0.6": 0,
                "0.6-0.8": 0,
                "0.8-1.0": 0,
            },
        )

    scores = [max(0.0, min(1.0, float(item.score))) for item in items]
    top_score = max(scores)
    mean_score = sum(scores) / max(1, len(scores))
    trusted_ratio = sum(1 for item in items if item.source in {"sm", "skill"}) / float(
        len(items)
    )

    normalized_texts = [
        " ".join(_tokenize(item.text)) for item in items if item.text.strip()
    ]
    duplicate_ratio = 0.0
    if normalized_texts:
        unique_texts = len(set(normalized_texts))
        duplicate_ratio = 1.0 - (float(unique_texts) / float(len(normalized_texts)))

    quality: RetrievalQuality
    action = ""
    if top_score >= self.config.quality_good_threshold and duplicate_ratio <= (
        self.config.duplication_bad_threshold * 0.5
    ):
        quality = "GOOD"
        action = "use"
    elif (
        top_score >= self.config.quality_ok_threshold
        and duplicate_ratio <= self.config.duplication_bad_threshold
    ):
        quality = "OK"
        action = "compress_harder"
    elif trusted_ratio >= 0.6 and mean_score >= self.config.quality_ok_threshold:
        quality = "OK"
        action = "compress_harder"
    else:
        quality = "BAD"
        action = "fallback_or_empty"

    return RetrievalEval(
        quality=quality,
        top_score=top_score,
        mean_score=mean_score,
        trusted_ratio=trusted_ratio,
        duplicate_ratio=duplicate_ratio,
        score_histogram=self._score_histogram(scores),
        action=action,
    )


def _score_histogram(self, scores: list[float]) -> dict[str, int]:
    bins = {
        "0.0-0.2": 0,
        "0.2-0.4": 0,
        "0.4-0.6": 0,
        "0.6-0.8": 0,
        "0.8-1.0": 0,
    }
    for value in scores:
        score = max(0.0, min(1.0, float(value)))
        if score < 0.2:
            bins["0.0-0.2"] += 1
        elif score < 0.4:
            bins["0.2-0.4"] += 1
        elif score < 0.6:
            bins["0.4-0.6"] += 1
        elif score < 0.8:
            bins["0.6-0.8"] += 1
        else:
            bins["0.8-1.0"] += 1
    return bins


def _retrieve_semantic(
    self,
    *,
    session_id: str,
    agent_id: str,
    query: str,
    strategy: RetrievalStrategy,
) -> list[RetrievedContext]:
    if self._memctl is None:
        return []
    out: list[RetrievedContext] = []

    if hasattr(self._memctl, "retrieve"):
        try:
            rows = self._memctl.retrieve(
                session_id=session_id,
                agent_id=agent_id,
                query=query,
                k=self.config.retrieval.k_sm,
                filters=None,
            )
        except TypeError:
            rows = self._memctl.retrieve(query=query, k=self.config.retrieval.k_sm)  # type: ignore[call-arg]
        except Exception:  # noqa: BLE001
            rows = []
        out.extend(self._normalize_semantic_rows(rows, strategy=strategy))
        if out:
            return out

    if hasattr(self._memctl, "query_facts"):
        try:
            rows = self._memctl.query_facts(
                session_id=session_id,
                agent_id=agent_id,
                query=query,
                limit=self.config.retrieval.k_sm,
            )
        except Exception:  # noqa: BLE001
            rows = []
        out.extend(self._normalize_semantic_rows(rows, strategy=strategy))
    return out[: self.config.retrieval.k_sm]


def _normalize_semantic_rows(
    self, rows: Any, *, strategy: RetrievalStrategy
) -> list[RetrievedContext]:
    out: list[RetrievedContext] = []
    if not isinstance(rows, list):
        return out
    for raw in rows:
        item = self._to_plain(raw)
        text = str(item.get("text", item.get("content", ""))).strip()
        if not text:
            continue
        ref_id = str(
            item.get("record_id", item.get("id", item.get("ref", "sm://unknown")))
        )
        score = float(item.get("score", 0.0) or 0.0)
        tags_raw = item.get("tags", [])
        tags = [str(tag) for tag in tags_raw] if isinstance(tags_raw, list) else []
        out.append(
            RetrievedContext(
                source="sm",
                ref_id=ref_id,
                text=text[: self.config.retrieval.text_snippet_chars],
                score=score,
                recency_score=0.0,
                tags=tags,
                created_at=item.get("updated_at") or item.get("created_at"),
                unit_kind="chunk",
                retrieval_strategy=strategy,
                raptor_level="none",
                trust_score=1.0,
                metadata={"raw": item},
            )
        )
    out.sort(key=lambda item: item.score, reverse=True)
    return out


def _retrieve_episodic(
    self,
    *,
    session_id: str,
    agent_id: str,
    query: str,
    strategy: RetrievalStrategy,
) -> list[RetrievedContext]:
    if self._artifactctl is None:
        return []
    try:
        rows = self._artifactctl.list_recent(
            limit=self.config.retrieval.artifact_scan_limit,
            scope_filters={"session_id": session_id, "agent_id": agent_id},
        )
    except TypeError:
        rows = self._artifactctl.list_recent(
            limit=self.config.retrieval.artifact_scan_limit
        )
    except Exception:  # noqa: BLE001
        rows = []

    query_tokens = set(_tokenize(query))
    now = datetime.now(timezone.utc)
    out: list[RetrievedContext] = []
    for raw in rows:
        meta = self._to_plain(raw)
        ref = self._artifact_ref_from_meta(meta)
        text = self._artifact_text_excerpt(ref=ref, fallback_meta=meta)
        label = str(meta.get("label", ""))
        name = str(meta.get("original_name", ""))
        meta_blob = self._meta_to_searchable_text(
            meta.get("meta") or meta.get("meta_json")
        )
        combined = " ".join(part for part in [label, name, text, meta_blob] if part)
        keyword_score = self._keyword_score(query_tokens, combined)

        created_at = str(meta.get("created_at", ""))
        recency = self._recency_score(
            now=now,
            created_at=created_at,
            half_life_hours=self.config.retrieval.recency_half_life_hours,
        )
        total_score = keyword_score + recency * 0.2
        if total_score <= 0.0:
            continue

        tags = self._extract_tags(meta.get("meta") or meta.get("meta_json"))
        out.append(
            RetrievedContext(
                source="em",
                ref_id=ref,
                text=combined[: self.config.retrieval.text_snippet_chars],
                score=total_score,
                recency_score=recency,
                tags=tags,
                created_at=created_at or None,
                unit_kind=("doc_group" if strategy == "longrag_doc_group" else "chunk"),
                retrieval_strategy=strategy,
                raptor_level=("internal" if strategy == "raptor" else "none"),
                trust_score=0.6,
                metadata={"label": label, "original_name": name},
            )
        )

    out.sort(key=lambda item: (item.score, item.recency_score), reverse=True)
    return out[: self.config.retrieval.k_em]


def _retrieve_skills(
    self, *, agent_id: str, query: str, strategy: RetrievalStrategy
) -> list[RetrievedContext]:
    if self._skillctl is None or self.config.retrieval.k_skill <= 0:
        return []
    try:
        matches = self._skillctl.match(
            query, None, agent_id, k=self.config.retrieval.k_skill
        )
    except Exception:  # noqa: BLE001
        return []

    out: list[RetrievedContext] = []
    for match in matches[: self.config.retrieval.k_skill]:
        row = self._to_plain(match)
        skill_id = str(row.get("skill_id", "")).strip()
        if not skill_id:
            continue
        version_hash = row.get("version_hash")
        try:
            snippet, snippet_hash = self._skillctl.render_snippet(
                skill_id, version_hash, "plan", max_tokens=200
            )
        except Exception:  # noqa: BLE001
            continue
        out.append(
            RetrievedContext(
                source="skill",
                ref_id=f"{skill_id}@{version_hash or 'latest'}",
                text=snippet[: self.config.retrieval.text_snippet_chars],
                score=float(row.get("score", 0.0) or 0.0),
                recency_score=0.0,
                tags=[str(item) for item in row.get("tags", [])]
                if isinstance(row.get("tags"), list)
                else [],
                created_at=None,
                unit_kind="document",
                retrieval_strategy=strategy,
                raptor_level="none",
                trust_score=0.9,
                metadata={
                    "snippet_hash": snippet_hash,
                    "skill_name": row.get("name"),
                },
            )
        )
    return out


def _keyword_score(self, query_tokens: set[str], text: str) -> float:
    if not query_tokens:
        return 0.0
    text_tokens = set(_tokenize(text))
    if not text_tokens:
        return 0.0
    overlap = len(query_tokens.intersection(text_tokens))
    return overlap / max(1.0, float(len(query_tokens)))


def _recency_score(
    self, *, now: datetime, created_at: str, half_life_hours: int
) -> float:
    created = _parse_iso(created_at)
    if created is None:
        return 0.0
    age_hours = max(0.0, (now - created).total_seconds() / 3600.0)
    if half_life_hours <= 0:
        return 0.0
    return math.exp(-math.log(2.0) * (age_hours / float(half_life_hours)))


def _artifact_ref_from_meta(self, meta: dict[str, Any]) -> str:
    ref = meta.get("ref")
    if ref:
        return str(ref)
    sha = str(meta.get("sha256", "")).strip()
    if len(sha) == 64:
        return f"artifact://sha256/{sha}"
    return "artifact://unknown"


def _artifact_text_excerpt(self, *, ref: str, fallback_meta: dict[str, Any]) -> str:
    if self._artifactctl is None:
        return ""
    mime = str(fallback_meta.get("mime", ""))
    if mime and not (
        mime.startswith("text/")
        or mime in {"application/json", "application/markdown", "application/x-ndjson"}
    ):
        return ""
    try:
        raw = self._artifactctl.read_bytes(ref)
    except Exception:  # noqa: BLE001
        return ""
    if not isinstance(raw, (bytes, bytearray)):
        return str(raw)[: self.config.retrieval.text_snippet_chars]
    try:
        decoded = bytes(raw).decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return ""
    return decoded.strip()[: self.config.retrieval.text_snippet_chars]


def _extract_tags(self, raw_meta: Any) -> list[str]:
    if not isinstance(raw_meta, dict):
        return []
    tags_raw = raw_meta.get("tags")
    if not isinstance(tags_raw, list):
        return []
    return [str(item) for item in tags_raw if str(item).strip()]


def _meta_to_searchable_text(self, raw_meta: Any) -> str:
    if not isinstance(raw_meta, dict):
        return ""
    try:
        return json.dumps(raw_meta, sort_keys=True, ensure_ascii=True)
    except TypeError:
        return ""


def _artifact_ref_to_text(self, ref: Any) -> str | None:
    row = self._to_plain(ref)
    if isinstance(row.get("ref"), str):
        return str(row["ref"])
    if isinstance(ref, str):
        return ref
    if isinstance(row.get("sha256"), str):
        return f"artifact://sha256/{row['sha256']}"
    return None
