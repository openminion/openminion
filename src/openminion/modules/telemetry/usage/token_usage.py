"""Detailed token usage records derived from typed session events."""

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from .types import coerce_non_negative_int

SURFACE_LLM_PROMPT = "llm_prompt"
SURFACE_LLM_OUTPUT = "llm_output"
SURFACE_LLM_CACHE_READ = "llm_cache_read"
SURFACE_LLM_CACHE_WRITE = "llm_cache_write"
SURFACE_CONTEXT_PACK = "context_pack"
SURFACE_CONTEXT_BUCKET = "context_bucket"

TOKEN_USAGE_SURFACES = frozenset(
    {
        SURFACE_LLM_PROMPT,
        SURFACE_LLM_OUTPUT,
        SURFACE_LLM_CACHE_READ,
        SURFACE_LLM_CACHE_WRITE,
        SURFACE_CONTEXT_PACK,
        SURFACE_CONTEXT_BUCKET,
    }
)

_INPUT_TOKEN_KEYS = ("input_tokens", "prompt_tokens", "total_input_tokens_used")
_OUTPUT_TOKEN_KEYS = (
    "output_tokens",
    "completion_tokens",
    "total_output_tokens_used",
)
_CACHE_READ_TOKEN_KEYS = (
    "cache_read_tokens",
    "cached_tokens",
    "cached_input_tokens",
    "usage_cached_tokens",
)
_CACHE_WRITE_TOKEN_KEYS = (
    "cache_write_tokens",
    "cache_creation_tokens",
    "cache_creation_input_tokens",
)
_ESTIMATED_TOKEN_KEYS = ("estimated_tokens", "used_tokens", "total_used_tokens")
_CAP_TOKEN_KEYS = ("cap_tokens", "total_cap_tokens")
_TOKEN_FIELD_NAMES = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "estimated_tokens",
    "cap_tokens",
    "saved_tokens",
)


def _first_token_int(payload: Mapping[str, Any], keys: tuple[str, ...]) -> int:
    for key in keys:
        if key in payload:
            return coerce_non_negative_int(payload.get(key))
    return 0


def _text(payload: Mapping[str, Any], key: str) -> str:
    return str(payload.get(key, "") or "").strip()


def _event_payload(event: Mapping[str, Any]) -> Mapping[str, Any]:
    payload = event.get("payload")
    return payload if isinstance(payload, Mapping) else {}


def _event_text(event: Mapping[str, Any], key: str) -> str:
    return str(event.get(key, "") or "").strip()


@dataclass(frozen=True)
class TokenUsageRecord:
    session_id: str
    run_id: str = ""
    turn_id: str = ""
    llm_call_id: str = ""
    prompt_context_id: str = ""
    provider: str = ""
    model: str = ""
    surface: str = ""
    bucket: str = ""
    source_event_type: str = ""
    source_event_id: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    estimated_tokens: int = 0
    cap_tokens: int = 0
    saved_tokens: int = 0
    original_ref: str = ""
    policy: str = ""
    estimated: bool = False

    def __post_init__(self) -> None:
        for field_name in _TOKEN_FIELD_NAMES:
            object.__setattr__(
                self,
                field_name,
                coerce_non_negative_int(getattr(self, field_name)),
            )

    @property
    def has_tokens(self) -> bool:
        return any(
            (
                self.input_tokens,
                self.output_tokens,
                self.cache_read_tokens,
                self.cache_write_tokens,
                self.estimated_tokens,
                self.saved_tokens,
            )
        )

    def as_payload(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "run_id": self.run_id,
            "turn_id": self.turn_id,
            "llm_call_id": self.llm_call_id,
            "prompt_context_id": self.prompt_context_id,
            "provider": self.provider,
            "model": self.model,
            "surface": self.surface,
            "bucket": self.bucket,
            "source_event_type": self.source_event_type,
            "source_event_id": self.source_event_id,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "estimated_tokens": self.estimated_tokens,
            "cap_tokens": self.cap_tokens,
            "saved_tokens": self.saved_tokens,
            "original_ref": self.original_ref,
            "policy": self.policy,
            "estimated": self.estimated,
        }


@dataclass(frozen=True)
class TokenUsageSummary:
    session_id: str
    run_id: str = ""
    records: tuple[TokenUsageRecord, ...] = ()

    @property
    def total_input_tokens(self) -> int:
        return sum(record.input_tokens for record in self.records)

    @property
    def total_output_tokens(self) -> int:
        return sum(record.output_tokens for record in self.records)

    @property
    def total_cache_read_tokens(self) -> int:
        return sum(record.cache_read_tokens for record in self.records)

    @property
    def total_cache_write_tokens(self) -> int:
        return sum(record.cache_write_tokens for record in self.records)

    @property
    def total_estimated_tokens(self) -> int:
        return sum(record.estimated_tokens for record in self.records)

    @property
    def total_saved_tokens(self) -> int:
        return sum(record.saved_tokens for record in self.records)

    @property
    def totals_by_surface(self) -> dict[str, int]:
        totals: dict[str, int] = defaultdict(int)
        for record in self.records:
            totals[record.surface] += _record_total(record)
        return dict(totals)

    @property
    def totals_by_context_bucket(self) -> dict[str, int]:
        totals: dict[str, int] = defaultdict(int)
        for record in self.records:
            if record.surface == SURFACE_CONTEXT_BUCKET and record.bucket:
                totals[record.bucket] += record.estimated_tokens
        return dict(totals)

    def as_payload(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "run_id": self.run_id,
            "records": [record.as_payload() for record in self.records],
            "totals": {
                "input_tokens": self.total_input_tokens,
                "output_tokens": self.total_output_tokens,
                "cache_read_tokens": self.total_cache_read_tokens,
                "cache_write_tokens": self.total_cache_write_tokens,
                "estimated_tokens": self.total_estimated_tokens,
                "saved_tokens": self.total_saved_tokens,
            },
            "totals_by_surface": self.totals_by_surface,
            "totals_by_context_bucket": self.totals_by_context_bucket,
        }


def records_from_session_event(
    event: Mapping[str, Any],
    *,
    session_id: str,
) -> tuple[TokenUsageRecord, ...]:
    event_type = _event_text(event, "event_type")
    if event_type == "llm.call.completed":
        return _records_from_llm_completed(event, session_id=session_id)
    if event_type == "context.manifest.created":
        return _records_from_context_manifest(event, session_id=session_id)
    if event_type == "llm.cache.metrics":
        return _records_from_cache_metrics(event, session_id=session_id)
    return ()


def summary_to_json_payload(summary: TokenUsageSummary) -> dict[str, Any]:
    return summary.as_payload()


def _records_from_llm_completed(
    event: Mapping[str, Any],
    *,
    session_id: str,
) -> tuple[TokenUsageRecord, ...]:
    payload = _event_payload(event)
    usage = payload.get("usage")
    if not isinstance(usage, Mapping):
        return ()
    base = _base_record(event, payload, session_id=session_id)
    records = (
        _record_with_tokens(
            base,
            surface=SURFACE_LLM_PROMPT,
            input_tokens=_first_token_int(usage, _INPUT_TOKEN_KEYS),
        ),
        _record_with_tokens(
            base,
            surface=SURFACE_LLM_OUTPUT,
            output_tokens=_first_token_int(usage, _OUTPUT_TOKEN_KEYS),
        ),
        _record_with_tokens(
            base,
            surface=SURFACE_LLM_CACHE_READ,
            cache_read_tokens=_first_token_int(usage, _CACHE_READ_TOKEN_KEYS),
        ),
        _record_with_tokens(
            base,
            surface=SURFACE_LLM_CACHE_WRITE,
            cache_write_tokens=_first_token_int(usage, _CACHE_WRITE_TOKEN_KEYS),
        ),
    )
    return tuple(record for record in records if record.has_tokens)


def _records_from_context_manifest(
    event: Mapping[str, Any],
    *,
    session_id: str,
) -> tuple[TokenUsageRecord, ...]:
    payload = _event_payload(event)
    base = _base_record(event, payload, session_id=session_id)
    records: list[TokenUsageRecord] = []
    pack_tokens = _first_token_int(payload, _ESTIMATED_TOKEN_KEYS)
    if pack_tokens > 0:
        records.append(
            _record_with_tokens(
                base,
                surface=SURFACE_CONTEXT_PACK,
                estimated_tokens=pack_tokens,
                cap_tokens=_first_token_int(payload, _CAP_TOKEN_KEYS),
                estimated=True,
                policy=_text(payload, "pack_policy_used"),
            )
        )
    for bucket_name, bucket_payload in _iter_bucket_payloads(payload):
        bucket_tokens = _first_token_int(bucket_payload, _ESTIMATED_TOKEN_KEYS)
        if bucket_tokens <= 0:
            continue
        records.append(
            _record_with_tokens(
                base,
                surface=SURFACE_CONTEXT_BUCKET,
                bucket=bucket_name,
                estimated_tokens=bucket_tokens,
                cap_tokens=_first_token_int(bucket_payload, _CAP_TOKEN_KEYS),
                estimated=True,
                policy=_text(payload, "pack_policy_used"),
            )
        )
    return tuple(records)


def _records_from_cache_metrics(
    event: Mapping[str, Any],
    *,
    session_id: str,
) -> tuple[TokenUsageRecord, ...]:
    payload = _event_payload(event)
    cached_tokens = _first_token_int(payload, _CACHE_READ_TOKEN_KEYS)
    if cached_tokens <= 0:
        return ()
    return (
        _record_with_tokens(
            _base_record(event, payload, session_id=session_id),
            surface=SURFACE_LLM_CACHE_READ,
            cache_read_tokens=cached_tokens,
        ),
    )


def _base_record(
    event: Mapping[str, Any],
    payload: Mapping[str, Any],
    *,
    session_id: str,
) -> TokenUsageRecord:
    return TokenUsageRecord(
        session_id=session_id,
        run_id=_text(payload, "run_id"),
        turn_id=_text(payload, "turn_id"),
        llm_call_id=_text(payload, "llm_call_id"),
        prompt_context_id=_text(payload, "prompt_context_id"),
        provider=_text(payload, "provider"),
        model=_text(payload, "model"),
        source_event_type=_event_text(event, "event_type"),
        source_event_id=_event_text(event, "event_id"),
    )


def _record_with_tokens(
    base: TokenUsageRecord,
    *,
    surface: str,
    bucket: str = "",
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    estimated_tokens: int = 0,
    cap_tokens: int = 0,
    saved_tokens: int = 0,
    policy: str = "",
    estimated: bool = False,
) -> TokenUsageRecord:
    return replace(
        base,
        surface=surface,
        bucket=bucket,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        estimated_tokens=estimated_tokens,
        cap_tokens=cap_tokens,
        saved_tokens=saved_tokens,
        policy=policy,
        estimated=estimated,
    )


def _iter_bucket_payloads(
    payload: Mapping[str, Any],
) -> tuple[tuple[str, Mapping[str, Any]], ...]:
    raw_buckets = payload.get("token_budget_buckets")
    if raw_buckets is None:
        raw_buckets = payload.get("buckets")
    if isinstance(raw_buckets, Mapping):
        return tuple(
            (str(name), item)
            for name, item in raw_buckets.items()
            if isinstance(item, Mapping)
        )
    if isinstance(raw_buckets, (list, tuple)):
        items: list[tuple[str, Mapping[str, Any]]] = []
        for item in raw_buckets:
            if not isinstance(item, Mapping):
                continue
            name = str(item.get("name") or item.get("bucket") or "").strip()
            if name:
                items.append((name, item))
        return tuple(items)
    return ()


def _record_total(record: TokenUsageRecord) -> int:
    return (
        record.input_tokens
        + record.output_tokens
        + record.cache_read_tokens
        + record.cache_write_tokens
        + record.estimated_tokens
        + record.saved_tokens
    )
