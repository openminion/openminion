import logging
from typing import TYPE_CHECKING, Any, Callable, Protocol, cast

from openminion.modules.prompting.context_blocks import DYNAMIC_MEMORY_BLOCK_HEADER
from openminion.modules.memory.errors import MemoryQueryUnavailableError

if TYPE_CHECKING:
    from openminion.modules.retrieve.schemas import RetrievalFilters


class _RetrieveError(Protocol):
    code: str


def build_empty_meta(lane: str, limit_chars: int = 0) -> dict[str, str]:
    return {
        "memory_envelope_version": "v2",
        "memory_envelope_truncated": "false",
        "memory_envelope_truncation_reasons": "",
        "memory_envelope_limit_chars": str(limit_chars),
        "memory_lane": lane,
    }


class RetrievalPipeline:
    def __init__(
        self,
        *,
        retrieve_ctl: Any | None,
        config: Any,
        ranking_config: Any | None,
        logger: logging.Logger,
        agent_id: str,
        retrieval_max_chars: int,
        trace_fn: Callable[[str, dict[str, Any]], None] | None,
        retrieve_error_type: type[Exception],
    ) -> None:
        self._retrieve_ctl = retrieve_ctl
        self._config = config
        self._ranking_config = ranking_config
        self._logger = logger
        self._agent_id = str(agent_id or "").strip() or "openminion"
        self._retrieval_max_chars = max(256, int(retrieval_max_chars))
        self._trace_fn = trace_fn
        self._retrieve_error_type = retrieve_error_type

    def sync_runtime_state(
        self,
        *,
        config: Any,
        ranking_config: Any | None,
        retrieve_ctl: Any | None,
        trace_fn: Callable[[str, dict[str, Any]], None] | None,
    ) -> None:
        self._config = config
        self._ranking_config = ranking_config
        self._retrieve_ctl = retrieve_ctl
        self._trace_fn = trace_fn

    def _trace(self, event_type: str, payload: dict[str, Any]) -> None:
        if not callable(self._trace_fn):
            return
        try:
            self._trace_fn(event_type, payload)
        except Exception:
            pass

    def _config_default(self, key: str, fallback: Any) -> Any:
        defaults = getattr(self._config, "defaults", None)
        value = getattr(defaults, key, fallback)
        if value is None:
            return fallback
        return value

    @staticmethod
    def _normalize_text(value: Any) -> str:
        return " ".join(str(value or "").split()).strip()

    def _extract_hit_text(self, item: dict[str, Any]) -> str:
        text_val: Any = item.get("text", item.get("content", ""))
        if isinstance(text_val, dict):
            text_val = text_val.get("text", text_val.get("content", ""))
        return self._normalize_text(text_val)

    def _merge_and_dedup(self, *groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        seen: set[str] = set()
        for group in groups:
            for item in group:
                if not isinstance(item, dict):
                    continue
                meta = item.get("meta", {})
                stable_id = ""
                if isinstance(meta, dict):
                    stable_id = str(
                        meta.get("memory_id")
                        or meta.get("record_id")
                        or meta.get("unit_id")
                        or ""
                    ).strip()
                if stable_id and stable_id in seen:
                    continue
                if stable_id:
                    seen.add(stable_id)
                merged.append(item)
        return merged

    def _build_retrieve_scope_keys(
        self,
        *,
        session_id: str,
        agent_id: str | None = None,
        project_id: str | None = None,
    ) -> list[str]:
        keys = [f"session:{session_id}", f"agent:{agent_id or self._agent_id}"]
        if project_id:
            keys.append(f"project:{project_id}")
        return keys

    def _build_retrieval_filters(
        self,
        *,
        session_id: str,
        agent_id: str,
        project_id: str | None,
        source_types: list[str],
        time_window_hours: int | None,
    ) -> "RetrievalFilters":
        from openminion.modules.retrieve.schemas import RetrievalFilters

        return RetrievalFilters(
            scope_keys=self._build_retrieve_scope_keys(
                session_id=session_id,
                agent_id=agent_id,
                project_id=project_id,
            ),
            types=source_types,
            time_window_hours=time_window_hours,
        )

    def _retrieve_lane(
        self,
        retrieve_ctl: Any,
        *,
        lane: str,
        query: str,
        session_id: str,
        agent_id: str,
        k: int,
        strategy: str,
        filters: "RetrievalFilters",
    ) -> list[dict[str, Any]]:
        try:
            raw = retrieve_ctl.retrieve(
                query=query,
                purpose="act",
                scope={"session_id": session_id, "agent_id": agent_id},
                k=max(1, int(k)),
                strategy=strategy,
                filters=filters.model_dump(mode="python", exclude_none=True),
            )
            items = (
                [item for item in raw if isinstance(item, dict)]
                if isinstance(raw, list)
                else []
            )
            resolved_strategy = (
                str(items[0].get("retrieval_strategy", "") or "").strip() or None
                if items
                else None
            )
            self._trace(
                "memory.retrieval.lane",
                {
                    "session_id": session_id,
                    "lane": lane,
                    "requested_strategy": strategy,
                    "resolved_strategy": resolved_strategy,
                    "results": len(items),
                    "no_result_reason": None if items else "no_candidates",
                },
            )
            return items
        except self._retrieve_error_type as exc:
            error = cast(_RetrieveError, exc)
            self._logger.warning(
                "memory.retrieval.retrieve_split %s failed session_id=%s error=%s",
                lane,
                session_id,
                error,
            )
            self._trace(
                "memory.retrieval.retrieve_ctl_error",
                {
                    "session_id": session_id,
                    "lane": lane,
                    "requested_strategy": strategy,
                    "reason_code": error.code,
                },
            )
            raise MemoryQueryUnavailableError(
                "Memory retrieval is unavailable.",
                details={"reason_code": error.code},
            ) from exc

    def _retrieve_split(
        self,
        retrieve_ctl: Any,
        *,
        query: str,
        session_id: str,
        agent_id: str,
        project_id: str | None,
        k_conversational: int,
        k_knowledge: int,
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        conversational_filters = self._build_retrieval_filters(
            session_id=session_id,
            agent_id=agent_id,
            project_id=project_id,
            source_types=["mem", "episode"],
            time_window_hours=168,
        )
        from openminion.modules.retrieve.schemas import RetrievalFilters

        knowledge_filters = RetrievalFilters(
            scope_keys=[],
            types=["skill", "doc", "artifact"],
            time_window_hours=None,
        )

        conversational_hits = self._retrieve_lane(
            retrieve_ctl,
            lane="conversational",
            query=query,
            session_id=session_id,
            agent_id=agent_id,
            k=k_conversational,
            strategy="contextual",
            filters=conversational_filters,
        )
        knowledge_hits = self._retrieve_lane(
            retrieve_ctl,
            lane="knowledge",
            query=query,
            session_id=session_id,
            agent_id=agent_id,
            k=k_knowledge,
            strategy="auto",
            filters=knowledge_filters,
        )

        merged = self._merge_and_dedup(conversational_hits, knowledge_hits)
        return merged, {
            "conversational": len(conversational_hits),
            "knowledge": len(knowledge_hits),
        }

    def _format_retrieval_item(self, item: dict[str, Any], *, text: str) -> str:
        meta = item.get("meta", {})
        if not isinstance(meta, dict) or not str(meta.get("record_id", "")).strip():
            return f"  • {text}"
        record_id = str(meta["record_id"]).strip()
        title = str(meta.get("record_title", "") or "").strip()
        record_type = str(meta.get("record_type", "") or "").strip() or "memory"
        source = str(meta.get("record_source", "") or "").strip() or "unknown"
        state = (
            "historical" if str(meta.get("record_valid_to", "")).strip() else "current"
        )
        score = float(item.get("unified_score", item.get("score", 0.0)) or 0.0)
        breakdown = meta.get("score_breakdown", {})
        stages = (
            ", ".join(sorted(str(key) for key in breakdown))
            if isinstance(breakdown, dict)
            else ""
        )
        heading = f"  • {title}" if title else "  • Memory evidence"
        detail = (
            f"    id={record_id} · type={record_type} · state={state} · source={source}"
        )
        scoring = f"    score={score:.4f}" + (f" · stages={stages}" if stages else "")
        return "\n".join(
            [
                heading,
                f"    excerpt: {text}",
                detail,
                scoring,
                f"    full record: fetch authorized memory ID {record_id}",
            ]
        )

    def _format_retrieval_context(
        self,
        items: list[dict[str, Any]],
        *,
        max_chars: int,
    ) -> tuple[str, int, int]:
        header = DYNAMIC_MEMORY_BLOCK_HEADER
        blocks: list[str] = []
        used_chars = len(header)
        omitted = 0
        for item in items:
            text = self._extract_hit_text(item)
            if not text:
                continue
            block = self._format_retrieval_item(item, text=text)
            separator_chars = 2 if blocks else 1
            if used_chars + separator_chars + len(block) <= max_chars:
                blocks.append(block)
                used_chars += separator_chars + len(block)
                continue
            excerpt = text
            shortened = block
            while excerpt and used_chars + separator_chars + len(shortened) > max_chars:
                excerpt, _, _ = excerpt.rpartition(" ")
                shortened = self._format_retrieval_item(
                    item,
                    text=f"{excerpt} …" if excerpt else "",
                )
            if excerpt and used_chars + separator_chars + len(shortened) <= max_chars:
                blocks.append(shortened)
                used_chars += separator_chars + len(shortened)
                continue
            omitted += 1
        if not blocks:
            return "", 0, omitted
        return f"{header}\n" + "\n\n".join(blocks), len(blocks), omitted

    def rank_and_format(
        self,
        memory_hits: list[dict[str, Any]],
        *,
        session_id: str,
        user_message: str,
        max_chars: int | None = None,
        project_id: str | None = None,
    ) -> tuple[str, dict[str, str], list[dict[str, Any]], list[dict[str, Any]]]:
        limit = max(
            128,
            max_chars if max_chars is not None else self._retrieval_max_chars,
        )
        meta = build_empty_meta("retrieval", limit)

        retrieve_hits, split_counts = self._rank_retrieve_hits(
            user_message=user_message,
            session_id=session_id,
            project_id=project_id,
        )
        merged_hits = self._merge_and_dedup(memory_hits, retrieve_hits)
        self._trace(
            "memory.retrieval.dual_query",
            {
                "session_id": session_id,
                "memory_hits": len(memory_hits),
                "retrieve_hits": len(retrieve_hits),
                "merged_hits": len(merged_hits),
                "retrieve_ctl_available": str(self._retrieve_ctl is not None).lower(),
                "conversational_hits": split_counts["conversational"],
                "knowledge_hits": split_counts["knowledge"],
                "retrieve_no_result_reason": (
                    None if retrieve_hits else "no_candidates"
                ),
            },
        )
        if not merged_hits:
            meta["memory_envelope_limit_chars"] = str(limit)
            self._trace(
                "memory.retrieval.result",
                {
                    "session_id": session_id,
                    "query_len": len(user_message),
                    "results": 0,
                    "retrieval_chars": 0,
                    "no_result_reason": "no_candidates",
                },
            )
            return "", meta, retrieve_hits, merged_hits

        content, included_count, omitted_count = self._format_retrieval_context(
            merged_hits,
            max_chars=limit,
        )
        if omitted_count:
            meta["memory_envelope_truncated"] = "true"
            meta["memory_envelope_truncation_reasons"] = "item_budget"
        meta["memory_envelope_included_items"] = str(included_count)
        meta["memory_envelope_omitted_items"] = str(omitted_count)
        meta["memory_envelope_limit_chars"] = str(limit)
        self._trace(
            "memory.retrieval.result",
            {
                "session_id": session_id,
                "query_len": len(user_message),
                "results": included_count,
                "omitted": omitted_count,
                "retrieval_chars": len(content),
                "no_result_reason": None,
            },
        )
        return content, meta, retrieve_hits, merged_hits

    def _rank_retrieve_hits(
        self,
        *,
        user_message: str,
        session_id: str,
        project_id: str | None,
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        split_counts = {"conversational": 0, "knowledge": 0}
        if self._retrieve_ctl is None:
            return [], split_counts
        retrieve_hits, split_counts = self._retrieve_split(
            self._retrieve_ctl,
            query=user_message,
            session_id=session_id,
            agent_id=self._agent_id,
            project_id=project_id,
            k_conversational=int(self._config_default("k_conversational", 3)),
            k_knowledge=int(self._config_default("k_knowledge", 3)),
        )
        return self._select_retrieve_hits(retrieve_hits), split_counts

    def _select_retrieve_hits(
        self,
        retrieve_hits: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        total_k = max(
            1,
            int(self._config_default("k_conversational", 3))
            + int(self._config_default("k_knowledge", 3)),
        )
        return sorted(
            retrieve_hits,
            key=lambda item: (
                -float(item.get("score", 0.0) or 0.0),
                str(item.get("ref_id") or item.get("unit_id") or ""),
            ),
        )[:total_k]


__all__ = [
    "RetrievalPipeline",
    "build_empty_meta",
]
