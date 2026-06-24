from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from openminion.modules.skill.runtime.selection_rag import narrow_catalog_by_bm25
from openminion.modules.skill.constants import (
    DEFAULT_STATUS_FILTER,
    RISK_CLASS_HIGH,
    RISK_CLASS_LOW,
    SKILL_MATCH_EXACT_ID_SCORE,
    SKILL_MATCH_EXACT_NAME_SCORE,
    SKILL_MATCH_EXACT_PHRASE_SCORE,
    SKILL_MATCH_IDENTITY_TOKEN_CAP,
    SKILL_MATCH_IDENTITY_TOKEN_SCORE,
    SKILL_STATUS_BLESSED,
    SKILL_STATUS_DRAFT,
    SKILL_STATUS_VERIFIED,
)
from openminion.modules.skill.models import (
    SkillMatch,
    SkillPackage,
    normalize_risk,
    normalize_status,
)


@dataclass(frozen=True)
class _SkillMatchTieBreak:
    exact_match_count: int
    compact_phrase_count: int
    compact_signal_score: float
    identity_score: float
    status_rank: int
    skill_id: str


class SkillMatchingMixin:
    config: Any
    store: Any
    _emit_skill_counter: Any
    _emit_skill_operation: Any

    def match(
        self,
        intent_text: str,
        step_hint: dict[str, Any] | None,
        agent_id: str,
        k: int = 3,
        status_filter: list[str] | str | None = None,
    ) -> list[SkillMatch]:
        normalized_step = step_hint or {}
        risk_hint = normalize_risk(str(normalized_step.get("risk", RISK_CLASS_LOW)))

        statuses = self._resolve_status_filter(status_filter, risk_hint)
        rows = self.store.list_latest_skills(status_filter=statuses, agent_id=agent_id)

        narrow_threshold = int(getattr(self.config, "selection_rag_threshold", 10))
        narrow_topk = int(getattr(self.config, "selection_rag_topk", 5))
        narrow_extra: dict[str, Any] | None = None
        if len(rows) > narrow_threshold:
            narrow_candidates = []
            for row in rows:
                package = SkillPackage.from_dict(row["package"])
                description = str(package.short_description or package.summary or "")
                when_to_use = str(package.sections.get("when_to_use", ""))
                narrow_candidates.append(
                    {
                        "id": str(row.get("skill_id", "")),
                        "name": str(row.get("name", "") or package.name),
                        "description": description,
                        "when_to_use": when_to_use,
                        "_original_row": row,
                    }
                )
            narrowed = narrow_catalog_by_bm25(
                narrow_candidates,
                query=intent_text or "",
                top_k=max(int(k) * 3, narrow_topk),
            )
            pre_count = len(rows)
            rows = [entry["_original_row"] for entry in narrowed]
            narrow_extra = {
                "narrowed": True,
                "narrow_threshold": narrow_threshold,
                "narrow_topk": narrow_topk,
                "pre_narrow_count": pre_count,
                "narrowed_count": len(rows),
            }

        intent_lower = (intent_text or "").lower()

        matches: list[SkillMatch] = []
        tie_breakers: dict[tuple[str, str], _SkillMatchTieBreak] = {}
        for row in rows:
            package = SkillPackage.from_dict(row["package"])
            score, reasons, tie_break = self._score_match(
                package, intent_lower, normalized_step
            )
            if score <= 0:
                continue
            tie_breakers[(package.skill_id, package.version_hash)] = tie_break
            matches.append(
                SkillMatch(
                    skill_id=package.skill_id,
                    version_hash=package.version_hash,
                    name=package.name,
                    status=package.status,
                    score=score,
                    reasons=reasons,
                    tags=package.tags,
                    tools=package.tools,
                    risk_class=package.risk_class,
                )
            )

        matches.sort(
            key=lambda item: (
                -item.score,
                -tie_breakers[(item.skill_id, item.version_hash)].exact_match_count,
                -tie_breakers[(item.skill_id, item.version_hash)].compact_phrase_count,
                -tie_breakers[(item.skill_id, item.version_hash)].compact_signal_score,
                -tie_breakers[(item.skill_id, item.version_hash)].identity_score,
                -tie_breakers[(item.skill_id, item.version_hash)].status_rank,
                tie_breakers[(item.skill_id, item.version_hash)].skill_id,
            )
        )
        limited = matches[: max(1, int(k))]
        extra = {
            "requested_candidates": max(1, int(k)),
            "returned_candidates": len(limited),
        }
        if narrow_extra is not None:
            extra.update(narrow_extra)
        self._emit_skill_operation(
            operation="shortlist",
            status="ok",
            extra=extra,
        )
        self._emit_skill_counter(
            counter_name="candidate_count",
            value=float(max(0, len(limited))),
            extra=extra,
        )
        if not limited:
            self._emit_skill_operation(
                operation="fallback",
                status="ok",
                extra={"reason": "no_match"},
            )
        return limited

    def _resolve_status_filter(
        self, status_filter: list[str] | str | None, risk_hint: str
    ) -> list[str]:
        if status_filter is None:
            configured = (
                self.config.high_risk_status_filter
                if risk_hint == RISK_CLASS_HIGH
                else self.config.default_status_filter
            )
            values = list(configured)
        elif isinstance(status_filter, str):
            values = [status_filter]
        else:
            values = [str(item) for item in status_filter]

        normalized = list(dict.fromkeys(normalize_status(item) for item in values))
        return normalized or list(DEFAULT_STATUS_FILTER)

    def _score_match(
        self,
        package: SkillPackage,
        intent_lower: str,
        step_hint: dict[str, Any],
    ) -> tuple[float, list[str], _SkillMatchTieBreak]:
        score = 0.0
        reasons: list[str] = []
        exact_match_count = 0
        compact_phrase_count = 0
        compact_signal_score = 0.0
        identity_score = 0.0
        has_signal = False
        normalized_query = intent_lower.strip()

        skill_id_lower = package.skill_id.lower()
        name_lower = package.name.lower()
        display_name_lower = str(package.display_name or "").strip().lower()

        if normalized_query == skill_id_lower:
            score += SKILL_MATCH_EXACT_ID_SCORE
            exact_match_count += 1
            has_signal = True
            reasons.append(f"exact skill_id matched: {package.skill_id}")

        exact_name_fields: list[str] = []
        for field_name, candidate in (
            ("name", name_lower),
            ("display_name", display_name_lower),
        ):
            if candidate and normalized_query == candidate:
                score += SKILL_MATCH_EXACT_NAME_SCORE
                exact_match_count += 1
                has_signal = True
                exact_name_fields.append(field_name)
                reasons.append(f"exact {field_name} matched")

        compact_phrase_sources = _compact_phrase_sources(package)
        seen_compact_sources: set[str] = set()
        for field_name, candidate in compact_phrase_sources:
            candidate_normalized = str(candidate or "").strip().lower()
            if not candidate_normalized or candidate_normalized in seen_compact_sources:
                continue
            seen_compact_sources.add(candidate_normalized)
            if normalized_query == candidate_normalized or _contains_exact_phrase(
                intent_lower, candidate_normalized
            ):
                score += SKILL_MATCH_EXACT_PHRASE_SCORE
                compact_phrase_count += 1
                compact_signal_score += SKILL_MATCH_EXACT_PHRASE_SCORE
                has_signal = True
                reasons.append(f"exact {field_name} phrase matched")

        for phrase in _explicit_match_phrases(package):
            if _contains_exact_phrase(intent_lower, phrase):
                score += SKILL_MATCH_EXACT_PHRASE_SCORE
                compact_phrase_count += 1
                compact_signal_score += SKILL_MATCH_EXACT_PHRASE_SCORE
                has_signal = True
                reasons.append(f"explicit phrase matched: {phrase}")

        identity_tokens = _collect_tokens(
            package.skill_id,
            package.name,
            package.display_name or "",
        )
        identity_hits = sorted(
            _collect_tokens(intent_lower).intersection(identity_tokens)
        )
        if len(identity_hits) >= 2:
            identity_score = min(
                SKILL_MATCH_IDENTITY_TOKEN_CAP,
                float(len(identity_hits)) * SKILL_MATCH_IDENTITY_TOKEN_SCORE,
            )
            score += identity_score
            has_signal = True
            reasons.append(f"identity overlap: {', '.join(identity_hits[:5])}")

        for phrase in package.applies_to.get("intents", []):
            phrase_l = phrase.lower()
            if phrase_l and phrase_l in intent_lower:
                score += 4.0
                has_signal = True
                reasons.append(f"applies_to intent matched: {phrase}")

        tool_hint = str(step_hint.get("tool_id", "")).strip()
        if tool_hint:
            if tool_hint in package.tools:
                score += 7.0
                has_signal = True
                reasons.append(f"tool matched: {tool_hint}")
            elif any(tool_hint in step for step in package.applies_to.get("steps", [])):
                score += 4.0
                has_signal = True
                reasons.append(f"tool-step pattern matched: {tool_hint}")

        if not has_signal:
            return (
                0.0,
                [],
                _SkillMatchTieBreak(
                    exact_match_count=0,
                    compact_phrase_count=0,
                    compact_signal_score=0.0,
                    identity_score=0.0,
                    status_rank=_status_preference_rank(package.status),
                    skill_id=package.skill_id,
                ),
            )

        verify_hint = bool(step_hint.get("verify", False))
        if verify_hint:
            if package.verification_rules:
                score += 5.0
                reasons.append("verification rules available")
            else:
                score -= 3.0
                reasons.append("missing verification rules")

        risk_hint = normalize_risk(str(step_hint.get("risk", RISK_CLASS_LOW)))
        if risk_hint == RISK_CLASS_HIGH:
            if package.status == SKILL_STATUS_BLESSED:
                score += 6.0
                reasons.append("blessed status for high risk")
            elif package.status == SKILL_STATUS_VERIFIED:
                score += 3.0
                reasons.append("verified status for high risk")
            elif package.status == SKILL_STATUS_DRAFT:
                score -= 6.0
                reasons.append("draft status penalized for high risk")

        if package.status == SKILL_STATUS_BLESSED:
            score += 2.5
        elif package.status == SKILL_STATUS_VERIFIED:
            score += 1.5
        elif package.status == SKILL_STATUS_DRAFT:
            score += 0.5

        return (
            score,
            reasons,
            _SkillMatchTieBreak(
                exact_match_count=exact_match_count,
                compact_phrase_count=compact_phrase_count,
                compact_signal_score=compact_signal_score,
                identity_score=0.0,
                status_rank=_status_preference_rank(package.status),
                skill_id=package.skill_id,
            ),
        )


def _compact_phrase_sources(package: SkillPackage) -> list[tuple[str, str]]:
    summary_text = str(
        package.sections.get("summary", "") or package.summary or ""
    ).strip()
    compact_summary = str(package.compact_summary_text() or "").strip()
    sources: list[tuple[str, str]] = []
    if package.short_description:
        sources.append(("short_description", str(package.short_description).strip()))
    if summary_text:
        sources.append(("summary", summary_text))
    if (
        not package.short_description
        and compact_summary
        and compact_summary != summary_text
    ):
        sources.append(("compact_summary", compact_summary))
    return sources


def _collect_tokens(*values: str) -> set[str]:
    tokens: set[str] = set()
    for value in values:
        tokens.update(re.findall(r"[a-z0-9]+", str(value or "").lower()))
    return tokens


def _explicit_match_phrases(package: SkillPackage) -> list[str]:
    phrases: list[str] = []
    seen: set[str] = set()
    for _field_name, candidate in _compact_phrase_sources(package):
        for phrase in _quoted_phrases(candidate):
            normalized = str(phrase or "").strip().lower()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            phrases.append(normalized)
    return phrases


def _quoted_phrases(value: str) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    matches = re.findall(r'"([^"\n]{3,})"', text)
    return [match.strip() for match in matches if match.strip()]


def _contains_exact_phrase(haystack: str, needle: str) -> bool:
    text = str(haystack or "").strip().lower()
    phrase = str(needle or "").strip().lower()
    if not text or not phrase:
        return False
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text) is not None


def _status_preference_rank(status: str) -> int:
    if status == SKILL_STATUS_BLESSED:
        return 3
    if status == SKILL_STATUS_VERIFIED:
        return 2
    if status == SKILL_STATUS_DRAFT:
        return 1
    return 0
