import json
from dataclasses import dataclass
from collections.abc import Iterable, Sequence

ALLOWED_PIN_SOURCES: tuple[str, ...] = ("user", "operator", "system", "policy")


@dataclass(frozen=True)
class PinnedContextPolicy:
    max_pins: int = 12
    max_chars_per_pin: int = 500
    max_total_chars: int = 3000


@dataclass(frozen=True)
class PinnedContextEntry:
    pin_id: str
    source: str
    text: str
    created_at: str = ""


DEFAULT_PINNED_CONTEXT_POLICY = PinnedContextPolicy()


def normalize_pin_source(source: str) -> str:
    normalized = str(source or "").strip().lower()
    if normalized not in ALLOWED_PIN_SOURCES:
        raise ValueError(f"Invalid pin source: {source!r}")
    return normalized


def normalize_pin_entries(
    entries: Iterable[PinnedContextEntry],
    *,
    policy: PinnedContextPolicy = DEFAULT_PINNED_CONTEXT_POLICY,
) -> list[PinnedContextEntry]:
    normalized: list[PinnedContextEntry] = []
    dedupe_keys: set[tuple[str, str]] = set()
    total_chars = 0
    for index, entry in enumerate(entries):
        source = normalize_pin_source(entry.source)
        text = str(entry.text or "").strip()
        if not text:
            continue
        if len(text) > policy.max_chars_per_pin:
            raise ValueError(
                f"Pin text too long at index {index}: {len(text)} > {policy.max_chars_per_pin}"
            )
        key = (source, text.lower())
        if key in dedupe_keys:
            continue
        dedupe_keys.add(key)
        pin_id = str(entry.pin_id or "").strip() or f"pin-{len(normalized) + 1}"
        created_at = str(entry.created_at or "").strip()
        normalized.append(
            PinnedContextEntry(
                pin_id=pin_id,
                source=source,
                text=text,
                created_at=created_at,
            )
        )
        total_chars += len(text)
        if len(normalized) > policy.max_pins:
            raise ValueError(
                f"Pin count exceeds limit: {len(normalized)} > {policy.max_pins}"
            )
        if total_chars > policy.max_total_chars:
            raise ValueError(
                f"Pinned context too large: {total_chars} > {policy.max_total_chars} chars"
            )
    return normalized


def encode_pinned_context(
    entries: Sequence[PinnedContextEntry],
    *,
    policy: PinnedContextPolicy = DEFAULT_PINNED_CONTEXT_POLICY,
) -> str:
    normalized = normalize_pin_entries(entries, policy=policy)
    if not normalized:
        return ""
    payload = {
        "version": 1,
        "pins": [
            {
                "id": entry.pin_id,
                "source": entry.source,
                "text": entry.text,
                "created_at": entry.created_at,
            }
            for entry in normalized
        ],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def decode_pinned_context(raw: str) -> list[PinnedContextEntry]:
    value = str(raw or "").strip()
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return [
            PinnedContextEntry(
                pin_id="legacy-1",
                source="system",
                text=value,
                created_at="",
            )
        ]

    if not isinstance(parsed, dict):
        return []

    pins = parsed.get("pins")
    if not isinstance(pins, list):
        return []

    decoded: list[PinnedContextEntry] = []
    for idx, item in enumerate(pins):
        if not isinstance(item, dict):
            continue
        source = str(item.get("source", "system") or "system").strip().lower()
        if source not in ALLOWED_PIN_SOURCES:
            source = "system"
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        pin_id = str(item.get("id", "")).strip() or f"pin-{idx + 1}"
        created_at = str(item.get("created_at", "")).strip()
        decoded.append(
            PinnedContextEntry(
                pin_id=pin_id,
                source=source,
                text=text,
                created_at=created_at,
            )
        )
    try:
        return normalize_pin_entries(decoded, policy=DEFAULT_PINNED_CONTEXT_POLICY)
    except ValueError:
        # Read path is fail-open; write path remains strict via encode/normalize.
        return decoded[: DEFAULT_PINNED_CONTEXT_POLICY.max_pins]


def render_pinned_context(raw: str) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    entries = decode_pinned_context(value)
    if not entries:
        return ""
    is_structured = value.startswith("{") and '"pins"' in value
    if (
        not is_structured
        and len(entries) == 1
        and entries[0].pin_id.startswith("legacy-")
    ):
        return entries[0].text
    lines = [f"- [{entry.source}] {entry.text}" for entry in entries]
    return "\n".join(lines)
