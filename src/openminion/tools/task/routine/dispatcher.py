import json
import re
from collections.abc import Mapping
from typing import Any, Protocol

from pydantic import ValidationError

from openminion.tools.github.interfaces import TOOL_GITHUB_LIST_PRS
from openminion.tools.task.constants import WATCH_PAYLOAD_KEY
from openminion.tools.task.pr_review.renderer import (
    render_announce_summary,
    render_artifact_markdown,
)
from openminion.tools.task.pr_review.schemas import (
    FindingV1,
    PrFactsPayloadV1,
    ReviewOutcomePayloadV1,
    ReviewedPrV1,
    build_pr_facts_payload,
    finding_hash,
    validate_review_outcome,
)
from openminion.tools.task.routine.schemas import (
    ROUTINE_KIND_GITHUB_PR_REVIEW,
    GitHubPrReviewCursorV1,
    RoutinePayloadV1,
)


class PreTurnContext(Protocol):
    def invoke_tool(
        self, *, name: str, args: Mapping[str, Any]
    ) -> Mapping[str, Any]: ...


class PostTurnSink(Protocol):
    def write_artifact(self, *, routine_id: str, body: str) -> str: ...

    def announce(self, *, routine_id: str, summary: str) -> None: ...


_TRAILER_RE = re.compile(
    r"<routine_outcome>\s*(?P<body>.*?)\s*</routine_outcome>",
    re.DOTALL,
)


class TrailerParseResult:
    __slots__ = ("outcome", "reason_code", "detail")

    def __init__(
        self,
        *,
        outcome: ReviewOutcomePayloadV1 | None,
        reason_code: str | None = None,
        detail: str = "",
    ) -> None:
        self.outcome = outcome
        self.reason_code = reason_code
        self.detail = detail


def parse_routine_outcome_trailer(text: str) -> TrailerParseResult:
    """Parse the ``<routine_outcome>...</routine_outcome>`` trailer."""
    if not isinstance(text, str):
        return TrailerParseResult(
            outcome=None,
            reason_code="trailer_missing",
            detail="model returned non-string content",
        )
    match = _TRAILER_RE.search(text)
    if match is None:
        return TrailerParseResult(
            outcome=None,
            reason_code="trailer_missing",
            detail="no <routine_outcome> trailer found",
        )
    body = match.group("body").strip()
    try:
        parsed = json.loads(body)
    except (ValueError, json.JSONDecodeError) as exc:
        return TrailerParseResult(
            outcome=None,
            reason_code="trailer_malformed_json",
            detail=str(exc),
        )
    try:
        outcome = ReviewOutcomePayloadV1.model_validate(parsed)
    except ValidationError as exc:
        return TrailerParseResult(
            outcome=None,
            reason_code="outcome_validation_failed",
            detail=str(exc),
        )
    return TrailerParseResult(outcome=outcome)


class RoutineHandler(Protocol):
    routine_kind: str

    def pre_turn(
        self,
        *,
        routine: RoutinePayloadV1,
        routine_id: str,
        ctx: PreTurnContext,
    ) -> PrFactsPayloadV1: ...

    def post_turn(
        self,
        *,
        routine: RoutinePayloadV1,
        routine_id: str,
        facts: PrFactsPayloadV1,
        outcome_text: str,
        sink: PostTurnSink,
    ) -> "PostTurnResult": ...


class PostTurnResult:
    __slots__ = (
        "ok",
        "reason_code",
        "detail",
        "artifact_id",
        "summary_line",
        "kept_count",
        "dropped_count",
        "new_findings_count",
        "updated_routine",
    )

    def __init__(
        self,
        *,
        ok: bool,
        reason_code: str | None = None,
        detail: str = "",
        artifact_id: str | None = None,
        summary_line: str = "",
        kept_count: int = 0,
        dropped_count: int = 0,
        new_findings_count: int = 0,
        updated_routine: RoutinePayloadV1 | None = None,
    ) -> None:
        self.ok = ok
        self.reason_code = reason_code
        self.detail = detail
        self.artifact_id = artifact_id
        self.summary_line = summary_line
        self.kept_count = kept_count
        self.dropped_count = dropped_count
        self.new_findings_count = new_findings_count
        self.updated_routine = updated_routine


class GitHubPrReviewHandler:
    routine_kind = ROUTINE_KIND_GITHUB_PR_REVIEW

    def pre_turn(
        self,
        *,
        routine: RoutinePayloadV1,
        routine_id: str,
        ctx: PreTurnContext,
    ) -> PrFactsPayloadV1:
        cfg = routine.config
        result = ctx.invoke_tool(
            name=TOOL_GITHUB_LIST_PRS,
            args={
                "owner": cfg.owner,
                "repo": cfg.repo,
                "state": cfg.state_filter,
            },
        )
        if not isinstance(result, Mapping) or not result.get("ok", False):
            # Best-effort: hand the model an empty list rather than raising,
            return build_pr_facts_payload(
                routine_id=routine_id,
                repo=f"{cfg.owner}/{cfg.repo}",
                open_prs_raw=[],
                cursor=routine.cursor,
            )
        data = result.get("data") or {}
        raw_list = data.get("open_prs") if isinstance(data, Mapping) else []
        if not isinstance(raw_list, list):
            raw_list = []
        return build_pr_facts_payload(
            routine_id=routine_id,
            repo=f"{cfg.owner}/{cfg.repo}",
            open_prs_raw=list(raw_list),
            cursor=routine.cursor,
        )

    def post_turn(
        self,
        *,
        routine: RoutinePayloadV1,
        routine_id: str,
        facts: PrFactsPayloadV1,
        outcome_text: str,
        sink: PostTurnSink,
    ) -> PostTurnResult:
        parse = parse_routine_outcome_trailer(outcome_text)
        if parse.outcome is None:
            return _post_turn_parse_failure(routine, facts=facts, parse=parse)

        kept, dropped = validate_review_outcome(parse.outcome, facts=facts)
        if not kept and not dropped and not parse.outcome.skipped_prs:
            return _post_turn_noop_success(routine, facts=facts)

        deduped, fresh_hashes, fresh_count = _dedupe_review_entries(routine, kept)
        if not deduped and not parse.outcome.skipped_prs:
            return _post_turn_duplicate_success(
                routine, facts=facts, dropped_count=len(dropped)
            )

        outcome_after_dedupe = parse.outcome.model_copy(
            update={"reviewed_prs": deduped}
        )
        artifact_id, summary_line = _write_review_artifact(
            routine_id=routine_id, facts=facts, outcome=outcome_after_dedupe, sink=sink
        )
        updated = _advance_cursor(
            routine,
            checked_at=facts.checked_at,
            facts=facts,
            kept=deduped,
            new_finding_hashes_per_pr=fresh_hashes,
        )
        return PostTurnResult(
            ok=True,
            artifact_id=artifact_id,
            summary_line=summary_line,
            kept_count=len(deduped),
            dropped_count=len(dropped),
            new_findings_count=fresh_count,
            updated_routine=updated,
        )


def _post_turn_parse_failure(
    routine: RoutinePayloadV1,
    *,
    facts: PrFactsPayloadV1,
    parse: TrailerParseResult,
) -> PostTurnResult:
    return PostTurnResult(
        ok=False,
        reason_code=parse.reason_code,
        detail=parse.detail,
        updated_routine=_bump_failure(routine, last_check_iso=facts.checked_at),
    )


def _post_turn_noop_success(
    routine: RoutinePayloadV1, *, facts: PrFactsPayloadV1
) -> PostTurnResult:
    return PostTurnResult(
        ok=True,
        summary_line="",
        kept_count=0,
        dropped_count=0,
        new_findings_count=0,
        updated_routine=_advance_cursor(
            routine,
            checked_at=facts.checked_at,
            facts=facts,
            kept=[],
            new_finding_hashes_per_pr={},
        ),
    )


def _post_turn_duplicate_success(
    routine: RoutinePayloadV1, *, facts: PrFactsPayloadV1, dropped_count: int
) -> PostTurnResult:
    return PostTurnResult(
        ok=True,
        summary_line="",
        kept_count=0,
        dropped_count=dropped_count,
        new_findings_count=0,
        updated_routine=_advance_cursor(
            routine,
            checked_at=facts.checked_at,
            facts=facts,
            kept=[],
            new_finding_hashes_per_pr={},
        ),
    )


def _dedupe_review_entries(
    routine: RoutinePayloadV1, kept: list[ReviewedPrV1]
) -> tuple[list[ReviewedPrV1], dict[str, list[str]], int]:
    delivered = dict(routine.cursor.delivered_findings_hashes)
    deduped: list[ReviewedPrV1] = []
    fresh_hashes_per_pr: dict[str, list[str]] = {}
    fresh_total = 0
    for entry in kept:
        fresh_findings, fresh_hashes = _fresh_findings_for_entry(
            entry, seen=set(delivered.get(str(entry.number), []))
        )
        if not fresh_findings and not entry.summary.strip():
            continue
        deduped.append(entry.model_copy(update={"findings": fresh_findings}))
        fresh_hashes_per_pr[str(entry.number)] = fresh_hashes
        fresh_total += len(fresh_hashes)
    return deduped, fresh_hashes_per_pr, fresh_total


def _fresh_findings_for_entry(
    entry: ReviewedPrV1, *, seen: set[str]
) -> tuple[list[FindingV1], list[str]]:
    fresh_findings: list[FindingV1] = []
    fresh_hashes: list[str] = []
    for finding in entry.findings:
        candidate = finding_hash(
            pr_number=entry.number,
            head_sha=entry.head_sha_reviewed,
            finding=finding,
        )
        if candidate in seen:
            continue
        fresh_findings.append(finding)
        fresh_hashes.append(candidate)
    return fresh_findings, fresh_hashes


def _write_review_artifact(
    *,
    routine_id: str,
    facts: PrFactsPayloadV1,
    outcome: ReviewOutcomePayloadV1,
    sink: PostTurnSink,
) -> tuple[str, str]:
    body = render_artifact_markdown(
        routine_id=routine_id,
        repo=facts.repo,
        checked_at=facts.checked_at,
        outcome=outcome,
    )
    artifact_id = sink.write_artifact(routine_id=routine_id, body=body)
    summary_line = render_announce_summary(repo=facts.repo, outcome=outcome)
    sink.announce(routine_id=routine_id, summary=summary_line)
    return artifact_id, summary_line


def _bump_failure(
    routine: RoutinePayloadV1, *, last_check_iso: str
) -> RoutinePayloadV1:
    cursor = routine.cursor.model_copy(
        update={
            "last_check_iso": last_check_iso,
            "consecutive_failures": routine.cursor.consecutive_failures + 1,
        }
    )
    return routine.model_copy(update={"cursor": cursor})


def _advance_cursor(
    routine: RoutinePayloadV1,
    *,
    checked_at: str,
    facts: PrFactsPayloadV1,
    kept: list[ReviewedPrV1],
    new_finding_hashes_per_pr: dict[str, list[str]],
) -> RoutinePayloadV1:
    last_review_per_pr = dict(routine.cursor.last_review_per_pr)
    for entry in kept:
        last_review_per_pr[str(entry.number)] = {  # type: ignore[assignment]
            "head_sha": entry.head_sha_reviewed,
            "reviewed_at": checked_at,
        }

    delivered = dict(routine.cursor.delivered_findings_hashes)
    for pr_number, hashes in new_finding_hashes_per_pr.items():
        existing = list(delivered.get(pr_number, []))
        existing.extend(hashes)
        delivered[pr_number] = existing

    seen = sorted(
        set(routine.cursor.seen_pr_numbers)
        | {pr.number for pr in facts.open_prs}
        | set(facts.previously_seen_prs)
        | set(facts.newly_opened_prs)
    )

    cursor = GitHubPrReviewCursorV1(
        last_check_iso=checked_at,
        last_review_per_pr=last_review_per_pr,
        seen_pr_numbers=seen,
        delivered_findings_hashes=delivered,
        consecutive_failures=0,  # success resets the counter
    )
    return routine.model_copy(update={"cursor": cursor})


class RoutineDispatcher:
    def __init__(self) -> None:
        self._handlers: dict[str, RoutineHandler] = {}

    def register(self, handler: RoutineHandler) -> None:
        self._handlers[handler.routine_kind] = handler

    def get(self, routine_kind: str) -> RoutineHandler | None:
        return self._handlers.get(routine_kind)

    def is_routine_payload(self, watch_payload: Mapping[str, Any] | None) -> bool:
        return self._extract_routine(watch_payload) is not None

    def _extract_routine(
        self, watch_payload: Mapping[str, Any] | None
    ) -> RoutinePayloadV1 | None:
        if not isinstance(watch_payload, Mapping):
            return None
        watch_block = watch_payload.get(WATCH_PAYLOAD_KEY)
        if not isinstance(watch_block, Mapping):
            return None
        raw = watch_block.get("routine")
        if not isinstance(raw, Mapping):
            return None
        try:
            return RoutinePayloadV1.model_validate(dict(raw))
        except ValidationError:
            return None

    def routine_for(
        self, watch_payload: Mapping[str, Any] | None
    ) -> RoutinePayloadV1 | None:
        return self._extract_routine(watch_payload)


def build_default_dispatcher() -> RoutineDispatcher:
    dispatcher = RoutineDispatcher()
    dispatcher.register(GitHubPrReviewHandler())
    return dispatcher


__all__ = [
    "PreTurnContext",
    "PostTurnSink",
    "PostTurnResult",
    "RoutineHandler",
    "RoutineDispatcher",
    "GitHubPrReviewHandler",
    "TrailerParseResult",
    "parse_routine_outcome_trailer",
    "build_default_dispatcher",
]
