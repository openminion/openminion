import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, cast

from pydantic import BaseModel, ValidationError

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
    GitHubPrReviewConfigV1,
    GitHubPrReviewCursorV1,
    RoutinePayloadV1,
)


class PreTurnContext(Protocol):
    def invoke_tool(
        self, *, name: str, args: Mapping[str, Any]
    ) -> Mapping[str, Any]: ...

    def exact_provider_enabled(self, *, family: str, provider_id: str) -> bool: ...


_TRAILER_RE = re.compile(
    r"<routine_outcome>\s*(?P<body>.*?)\s*</routine_outcome>",
    re.DOTALL,
)


@dataclass(slots=True, kw_only=True)
class TrailerParseResult:
    outcome: ReviewOutcomePayloadV1 | None
    reason_code: str | None = None
    detail: str = ""


def parse_routine_outcome_trailer(text: str) -> TrailerParseResult:
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
    except ValueError as exc:
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
    model_turn_tools: tuple[str, ...]
    finalizer_watch_overrides: Mapping[str, Any]

    def pre_turn_tools_for(self, routine: RoutinePayloadV1) -> tuple[str, ...]: ...

    def pre_turn(
        self,
        *,
        routine: Any,
        routine_id: str,
        ctx: PreTurnContext,
    ) -> Any: ...

    def render_turn(self, *, check_instruction: str, facts: Any) -> str: ...

    def post_turn(
        self,
        *,
        routine: Any,
        routine_id: str,
        facts: Any,
        outcome_text: str,
    ) -> "PostTurnResult": ...


@dataclass(slots=True, kw_only=True)
class PostTurnResult:
    ok: bool
    condition_value: bool | None = None
    reason_code: str | None = None
    detail: str = ""
    summary_line: str = ""
    limitations: tuple[str, ...] = ()
    artifact_body: str | None = None
    artifact_mime: str = "text/markdown"
    updated_routine: RoutinePayloadV1 | None = None
    metadata: dict[str, Any] | None = None


def build_routine_run_result(
    *,
    routine_kind: str,
    post: PostTurnResult,
    artifact_id: str,
    isolated_session_id: Any,
) -> dict[str, Any]:
    summary_parts = [f"routine={routine_kind}"]
    if not post.ok:
        summary_parts.append(f"error_code={post.reason_code or 'unknown'}")
    elif not artifact_id:
        summary_parts.append("no-op")
    else:
        summary_parts.append(f"artifact={artifact_id}")
    return {
        "summary": post.summary_line or " | ".join(summary_parts),
        "isolated_session_id": isolated_session_id,
        "artifact_refs": (
            [{"ref": artifact_id, "role": "output"}] if artifact_id else []
        ),
        "metadata": {
            "routine_kind": routine_kind,
            "routine_ok": post.ok,
            "routine_reason_code": post.reason_code or "",
            "routine_artifact_id": artifact_id,
            "routine_limitations": list(post.limitations),
            **dict(post.metadata or {}),
        },
    }


class GitHubPrReviewHandler:
    routine_kind: str = ROUTINE_KIND_GITHUB_PR_REVIEW
    model_turn_tools: tuple[str, ...] = (
        "file.read",
        "file.list_dir",
        "file.find",
        "web.fetch",
        "web.search",
        "exec.run",
        "time",
    )
    finalizer_watch_overrides: Mapping[str, Any] = {
        "stop_on_condition": False,
        "deliver_resolution": False,
        "delivery_cooldown_minutes": 0,
    }

    def pre_turn_tools_for(self, routine: RoutinePayloadV1) -> tuple[str, ...]:
        del routine
        return (TOOL_GITHUB_LIST_PRS,)

    def pre_turn(
        self,
        *,
        routine: RoutinePayloadV1,
        routine_id: str,
        ctx: PreTurnContext,
    ) -> PrFactsPayloadV1:
        cfg = cast(GitHubPrReviewConfigV1, routine.config)
        cursor = cast(GitHubPrReviewCursorV1, routine.cursor)
        result = ctx.invoke_tool(
            name=TOOL_GITHUB_LIST_PRS,
            args={
                "owner": cfg.owner,
                "repo": cfg.repo,
                "state": cfg.state_filter,
            },
        )
        if not result.get("ok", False):
            return build_pr_facts_payload(
                routine_id=routine_id,
                repo=f"{cfg.owner}/{cfg.repo}",
                open_prs_raw=[],
                cursor=cursor,
            )
        data = result.get("data") or {}
        raw_list = data.get("open_prs") if isinstance(data, Mapping) else []
        if not isinstance(raw_list, list):
            raw_list = []
        return build_pr_facts_payload(
            routine_id=routine_id,
            repo=f"{cfg.owner}/{cfg.repo}",
            open_prs_raw=raw_list,
            cursor=cursor,
        )

    def render_turn(self, *, check_instruction: str, facts: BaseModel) -> str:
        instruction = check_instruction or "Review the supplied PR facts."
        return (
            f"{instruction}\n\n"
            "PR facts (typed, read-only):\n"
            f"{facts.model_dump_json()}\n\n"
            "Emit exactly one trailer block of the form:\n"
            "<routine_outcome>{...}</routine_outcome>\n"
            "where the JSON conforms to ReviewOutcomePayloadV1: "
            '{ "reviewed_prs": [ ... ], "skipped_prs": [ ... ] }. '
            "For each reviewed PR, set head_sha_reviewed to the head_sha "
            "supplied in the facts. The runtime drops entries whose "
            "head_sha_reviewed does not match. Free prose outside the "
            "trailer is recorded but not actionable."
        )

    def post_turn(
        self,
        *,
        routine: RoutinePayloadV1,
        routine_id: str,
        facts: BaseModel,
        outcome_text: str,
    ) -> PostTurnResult:
        typed_facts = cast(PrFactsPayloadV1, facts)
        parse = parse_routine_outcome_trailer(outcome_text)
        if parse.outcome is None:
            return _post_turn_parse_failure(routine, facts=typed_facts, parse=parse)

        kept, dropped = validate_review_outcome(parse.outcome, facts=typed_facts)
        if not kept and not dropped and not parse.outcome.skipped_prs:
            return _post_turn_empty_success(routine, facts=typed_facts)

        deduped, fresh_hashes, fresh_count = _dedupe_review_entries(routine, kept)
        if not deduped and not parse.outcome.skipped_prs:
            return _post_turn_empty_success(
                routine, facts=typed_facts, dropped_count=len(dropped)
            )

        outcome_after_dedupe = parse.outcome.model_copy(
            update={"reviewed_prs": deduped}
        )
        artifact_body = render_artifact_markdown(
            routine_id=routine_id,
            repo=typed_facts.repo,
            checked_at=typed_facts.checked_at,
            outcome=outcome_after_dedupe,
        )
        summary_line = render_announce_summary(
            repo=typed_facts.repo, outcome=outcome_after_dedupe
        )
        updated = _advance_cursor(
            routine,
            checked_at=typed_facts.checked_at,
            facts=typed_facts,
            kept=deduped,
            new_finding_hashes_per_pr=fresh_hashes,
        )
        return PostTurnResult(
            ok=True,
            condition_value=True,
            summary_line=summary_line,
            artifact_body=artifact_body,
            updated_routine=updated,
            metadata={
                "kept_count": len(deduped),
                "dropped_count": len(dropped),
                "new_findings_count": fresh_count,
            },
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


def _post_turn_empty_success(
    routine: RoutinePayloadV1,
    *,
    facts: PrFactsPayloadV1,
    dropped_count: int = 0,
) -> PostTurnResult:
    return PostTurnResult(
        ok=True,
        condition_value=False,
        summary_line="",
        updated_routine=_advance_cursor(
            routine,
            checked_at=facts.checked_at,
            facts=facts,
            kept=[],
            new_finding_hashes_per_pr={},
        ),
        metadata={
            "kept_count": 0,
            "dropped_count": dropped_count,
            "new_findings_count": 0,
        },
    )


def _dedupe_review_entries(
    routine: RoutinePayloadV1, kept: list[ReviewedPrV1]
) -> tuple[list[ReviewedPrV1], dict[str, list[str]], int]:
    cursor = cast(GitHubPrReviewCursorV1, routine.cursor)
    delivered = dict(cursor.delivered_findings_hashes)
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


def _bump_failure(
    routine: RoutinePayloadV1, *, last_check_iso: str
) -> RoutinePayloadV1:
    current = cast(GitHubPrReviewCursorV1, routine.cursor)
    cursor = current.model_copy(
        update={
            "last_check_iso": last_check_iso,
            "consecutive_failures": current.consecutive_failures + 1,
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
    current = cast(GitHubPrReviewCursorV1, routine.cursor)
    last_review_per_pr = dict(current.last_review_per_pr)
    for entry in kept:
        last_review_per_pr[str(entry.number)] = {  # type: ignore[assignment]
            "head_sha": entry.head_sha_reviewed,
            "reviewed_at": checked_at,
        }

    delivered = dict(current.delivered_findings_hashes)
    for pr_number, hashes in new_finding_hashes_per_pr.items():
        existing = list(delivered.get(pr_number, []))
        existing.extend(hashes)
        delivered[pr_number] = existing

    seen = sorted(
        set(current.seen_pr_numbers)
        | {pr.number for pr in facts.open_prs}
        | set(facts.previously_seen_prs)
        | set(facts.newly_opened_prs)
    )

    cursor = GitHubPrReviewCursorV1(
        last_check_iso=checked_at,
        last_review_per_pr=last_review_per_pr,
        seen_pr_numbers=seen,
        delivered_findings_hashes=delivered,
        consecutive_failures=0,
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
        return self.has_routine_block(watch_payload)

    def has_routine_block(self, watch_payload: Mapping[str, Any] | None) -> bool:
        if not isinstance(watch_payload, Mapping):
            return False
        watch_block = watch_payload.get(WATCH_PAYLOAD_KEY)
        return isinstance(watch_block, Mapping) and "routine" in watch_block

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
    from openminion.tools.task.routine.social import SocialSignalHandler

    dispatcher = RoutineDispatcher()
    dispatcher.register(GitHubPrReviewHandler())
    dispatcher.register(SocialSignalHandler())
    return dispatcher


__all__ = [
    "PreTurnContext",
    "PostTurnResult",
    "RoutineHandler",
    "RoutineDispatcher",
    "build_routine_run_result",
    "GitHubPrReviewHandler",
    "TrailerParseResult",
    "parse_routine_outcome_trailer",
    "build_default_dispatcher",
]
