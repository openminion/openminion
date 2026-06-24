import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Mapping, Sequence, Tuple

from openminion.base.config import OpenMinionConfig
from openminion.modules.memory.runtime.scope import (
    assert_scope_matches_agent,
    build_agent_write_scope,
    emit_read_decision,
)
from openminion.modules.storage.runtime.sqlite import resolve_database_path
from openminion.modules.tool.base import ToolExecutionResult
from openminion.services.config import SELF_IMPROVEMENT_MAX_CONTEXT_CHARS

from openminion.base.time import utc_now_iso as _utc_now_iso

_SEAM_LIFECYCLE_NOTE_READ = "services.lifecycle.self_improvement.list_notes"

_NOTE_STATUS_CANDIDATE = "candidate"
_NOTE_STATUS_ACTIVE = "active"
_INDEX_FILENAME = "notes_index.json"
_APPLICATION_MODE_AUTOMATIC = "automatic"
_APPLICATION_MODE_REVIEW_FIRST = "review_first"
_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "into",
        "your",
        "you",
        "are",
        "was",
        "were",
        "have",
        "has",
        "had",
        "can",
        "not",
        "then",
        "than",
        "what",
        "when",
        "where",
        "which",
        "while",
        "will",
        "would",
        "could",
        "should",
        "about",
        "after",
        "before",
        "over",
        "under",
        "again",
        "also",
        "they",
        "them",
    }
)


def _payload_str_tuple(
    payload: Mapping[str, object], key: str, *, lower: bool = False
) -> tuple[str, ...]:
    raw = payload.get(key, ())
    if not isinstance(raw, Iterable) or isinstance(raw, (str, bytes)):
        return ()
    values = []
    for item in raw:
        normalized = str(item).strip()
        if not normalized:
            continue
        values.append(normalized.lower() if lower else normalized)
    return tuple(sorted(set(values)))


def _payload_int(payload: Mapping[str, object], key: str, default: int) -> int:
    raw = payload.get(key, default)
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        try:
            return int(raw)
        except ValueError:
            return default
    return default


def _slugify(value: str, *, default: str = "na", max_len: int = 48) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    if not normalized:
        normalized = default
    return normalized[:max_len]


def _tokenize(value: str) -> set[str]:
    tokens = re.findall(r"[a-z0-9]{3,}", str(value or "").lower())
    return {token for token in tokens if token not in _STOPWORDS}


def _normalize_application_mode(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"review-first", "review_first"}:
        return _APPLICATION_MODE_REVIEW_FIRST
    return _APPLICATION_MODE_AUTOMATIC


@dataclass(frozen=True)
class ImprovementNote:
    agent_id: str
    signature: str
    status: str
    source: str
    context: str
    guidance: str
    trigger_tokens: Tuple[str, ...]
    tags: Tuple[str, ...]
    occurrence_count: int
    apply_count: int
    created_at: str
    updated_at: str
    last_applied_at: str = ""

    def to_dict(self) -> Dict[str, object]:
        return {
            "agent_id": self.agent_id,
            "signature": self.signature,
            "status": self.status,
            "source": self.source,
            "context": self.context,
            "guidance": self.guidance,
            "trigger_tokens": list(self.trigger_tokens),
            "tags": list(self.tags),
            "occurrence_count": int(self.occurrence_count),
            "apply_count": int(self.apply_count),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_applied_at": self.last_applied_at,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "ImprovementNote":
        return cls(
            agent_id=str(payload.get("agent_id", "")).strip(),
            signature=str(payload.get("signature", "")).strip(),
            status=str(payload.get("status", _NOTE_STATUS_CANDIDATE)).strip(),
            source=str(payload.get("source", "tool_failure")).strip() or "tool_failure",
            context=str(payload.get("context", "")).strip(),
            guidance=str(payload.get("guidance", "")).strip(),
            trigger_tokens=_payload_str_tuple(payload, "trigger_tokens", lower=True),
            tags=_payload_str_tuple(payload, "tags"),
            occurrence_count=max(1, _payload_int(payload, "occurrence_count", 1)),
            apply_count=max(0, _payload_int(payload, "apply_count", 0)),
            created_at=str(payload.get("created_at", "")).strip() or _utc_now_iso(),
            updated_at=str(payload.get("updated_at", "")).strip() or _utc_now_iso(),
            last_applied_at=str(payload.get("last_applied_at", "")).strip(),
        )


class SelfImprovementEngine:
    def __init__(
        self,
        *,
        enabled: bool,
        notes_root: Path,
        application_mode: str = _APPLICATION_MODE_AUTOMATIC,
        activation_threshold: int = 2,
        auto_capture_tool_failures: bool = True,
    ) -> None:
        # `max_applied_notes` and `min_token_overlap` constructor
        self._enabled = bool(enabled)
        self._notes_root = notes_root
        self._index_path = notes_root / _INDEX_FILENAME
        self._application_mode = _normalize_application_mode(application_mode)
        self._activation_threshold = max(1, int(activation_threshold))
        self._auto_capture_tool_failures = bool(auto_capture_tool_failures)

    @classmethod
    def from_config(cls, config: OpenMinionConfig) -> "SelfImprovementEngine":
        configured_root = str(config.self_improvement.notes_path or "").strip()
        if configured_root:
            notes_root = Path(configured_root).expanduser().resolve()
        else:
            storage_parent = resolve_database_path(config.storage.path).parent
            notes_root = (storage_parent / "notes").resolve()
        # `config.self_improvement.max_applied_notes` and
        return cls(
            enabled=bool(config.self_improvement.enabled),
            notes_root=notes_root,
            application_mode=config.self_improvement.application_mode,
            activation_threshold=config.self_improvement.activation_threshold,
            auto_capture_tool_failures=config.self_improvement.auto_capture_tool_failures,
        )

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def notes_root(self) -> Path:
        return self._notes_root

    @property
    def application_mode(self) -> str:
        return self._application_mode

    @property
    def is_review_first(self) -> bool:
        return self._application_mode == _APPLICATION_MODE_REVIEW_FIRST

    # `build_guardrail_block` removed (LOSG forbids runtime

    def capture_tool_failures(
        self,
        *,
        agent_id: str,
        user_message: str,
        tool_results: Sequence[ToolExecutionResult],
    ) -> list[str]:
        if not self._enabled or not self._auto_capture_tool_failures:
            return []
        if not tool_results:
            return []

        captured_signatures: list[str] = []
        seen: set[str] = set()
        for result in tool_results:
            if result.ok and result.verified:
                continue
            if str(result.error or "").strip().startswith("security_"):
                # Policy denials are configuration outcomes, not model-learnable mistakes.
                continue
            signature = _failure_signature_for_tool_result(result)
            if signature in seen:
                continue
            seen.add(signature)
            note = self._upsert_failure_note(
                agent_id=agent_id,
                signature=signature,
                user_message=user_message,
                result=result,
            )
            captured_signatures.append(note.signature)
        return captured_signatures

    def list_notes(self, *, agent_id: str) -> list[ImprovementNote]:
        # CAMI-01a/01c: caller-declared `agent_only` mode for self-improvement
        normalized_agent_id = str(agent_id or "").strip()
        if not normalized_agent_id:
            return []
        _scopes, _event = emit_read_decision(
            normalized_agent_id,
            mode="agent_only",
            caller_seam=_SEAM_LIFECYCLE_NOTE_READ,
        )
        index = self._read_index()
        notes: list[ImprovementNote] = []
        for payload in index.values():
            note = ImprovementNote.from_dict(payload)
            if note.agent_id != normalized_agent_id:
                continue
            # Defense-in-depth: assert the note's owning agent scope matches.
            assert_scope_matches_agent(
                build_agent_write_scope(note.agent_id),
                normalized_agent_id,
            )
            if not note.signature:
                continue
            notes.append(note)
        notes.sort(key=lambda item: item.updated_at, reverse=True)
        return notes

    def find_notes_for_context(
        self,
        *,
        agent_id: str,
        tool_names: tuple[str, ...],
        error_slugs: tuple[str, ...] = (),
        status_filter: tuple[str, ...] = (_NOTE_STATUS_ACTIVE,),
    ) -> list[ImprovementNote]:
        """Return structurally matching notes for the current tool context."""

        normalized_agent_id = str(agent_id or "").strip()
        if not normalized_agent_id:
            return []
        tool_tags = {
            f"tool:{_slugify(name, default='tool')}"
            for name in tool_names
            if str(name or "").strip()
        }
        if not tool_tags:
            return []
        allowed_statuses = {
            str(item or "").strip().lower()
            for item in status_filter
            if str(item or "").strip()
        } or {_NOTE_STATUS_ACTIVE}
        error_tags = {
            f"error:{_slugify(item, default='error', max_len=40)}"
            for item in error_slugs
            if str(item or "").strip()
        }

        matches: list[tuple[int, int, str, ImprovementNote]] = []
        for note in self.list_notes(agent_id=normalized_agent_id):
            if str(note.status or "").strip().lower() not in allowed_statuses:
                continue
            note_tags = {
                str(item or "").strip() for item in note.tags if str(item).strip()
            }
            tool_match_count = len(tool_tags.intersection(note_tags))
            if tool_match_count <= 0:
                continue
            error_match_count = len(error_tags.intersection(note_tags))
            matches.append(
                (
                    tool_match_count,
                    error_match_count,
                    str(note.updated_at or "").strip(),
                    note,
                )
            )

        matches.sort(key=lambda item: str(item[3].signature or "").strip())
        matches.sort(key=lambda item: item[2], reverse=True)
        matches.sort(key=lambda item: item[1], reverse=True)
        matches.sort(key=lambda item: item[0], reverse=True)
        return [item[3] for item in matches]

    def set_note_status(self, *, agent_id: str, signature: str, status: str) -> bool:
        normalized_status = str(status or "").strip().lower()
        if normalized_status not in {_NOTE_STATUS_CANDIDATE, _NOTE_STATUS_ACTIVE}:
            raise ValueError("status must be `candidate` or `active`")
        key = _note_key(agent_id=agent_id, signature=signature)
        index = self._read_index()
        payload = index.get(key)
        if payload is None:
            return False
        note = ImprovementNote.from_dict(payload)
        now = _utc_now_iso()
        updated = ImprovementNote(
            agent_id=note.agent_id,
            signature=note.signature,
            status=normalized_status,
            source=note.source,
            context=note.context,
            guidance=note.guidance,
            trigger_tokens=note.trigger_tokens,
            tags=note.tags,
            occurrence_count=note.occurrence_count,
            apply_count=note.apply_count,
            created_at=note.created_at,
            updated_at=now,
            last_applied_at=note.last_applied_at,
        )
        index[key] = updated.to_dict()
        self._write_index(index)
        self._write_markdown_note(updated)
        return True

    def promote_note(self, *, agent_id: str, signature: str) -> bool:
        return self.set_note_status(
            agent_id=agent_id, signature=signature, status=_NOTE_STATUS_ACTIVE
        )

    def _upsert_failure_note(
        self,
        *,
        agent_id: str,
        signature: str,
        user_message: str,
        result: ToolExecutionResult,
    ) -> ImprovementNote:
        key = _note_key(agent_id=agent_id, signature=signature)
        index = self._read_index()
        now = _utc_now_iso()
        existing = index.get(key)
        if existing is not None:
            note = ImprovementNote.from_dict(existing)
            occurrence_count = note.occurrence_count + 1
            status = note.status
            if (
                self._application_mode == _APPLICATION_MODE_AUTOMATIC
                and occurrence_count >= self._activation_threshold
            ):
                status = _NOTE_STATUS_ACTIVE
            updated = ImprovementNote(
                agent_id=note.agent_id,
                signature=note.signature,
                status=status,
                source=note.source,
                context=_build_context_excerpt(
                    user_message=user_message, result=result
                ),
                guidance=_build_guidance_for_tool_failure(result),
                trigger_tokens=tuple(
                    sorted(
                        set(note.trigger_tokens)
                        | _build_trigger_tokens(user_message, result)
                    )
                ),
                tags=tuple(sorted(set(note.tags) | set(_build_tags(result)))),
                occurrence_count=occurrence_count,
                apply_count=note.apply_count,
                created_at=note.created_at,
                updated_at=now,
                last_applied_at=note.last_applied_at,
            )
            index[key] = updated.to_dict()
            self._write_index(index)
            self._write_markdown_note(updated)
            return updated

        initial_status = _NOTE_STATUS_CANDIDATE
        if (
            self._application_mode == _APPLICATION_MODE_AUTOMATIC
            and self._activation_threshold <= 1
        ):
            initial_status = _NOTE_STATUS_ACTIVE
        created = ImprovementNote(
            agent_id=agent_id,
            signature=signature,
            status=initial_status,
            source="tool_failure",
            context=_build_context_excerpt(user_message=user_message, result=result),
            guidance=_build_guidance_for_tool_failure(result),
            trigger_tokens=tuple(sorted(_build_trigger_tokens(user_message, result))),
            tags=tuple(sorted(_build_tags(result))),
            occurrence_count=1,
            apply_count=0,
            created_at=now,
            updated_at=now,
            last_applied_at="",
        )
        index[key] = created.to_dict()
        self._write_index(index)
        self._write_markdown_note(created)
        return created

    # `_record_applied` was removed. Its sole caller was

    def _read_index(self) -> Dict[str, Dict[str, object]]:
        if not self._index_path.exists():
            return {}
        try:
            payload = json.loads(self._index_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        if not isinstance(payload, dict):
            return {}
        notes_payload = payload.get("notes")
        if not isinstance(notes_payload, list):
            return {}
        notes_by_key: Dict[str, Dict[str, object]] = {}
        for item in notes_payload:
            if not isinstance(item, dict):
                continue
            note = ImprovementNote.from_dict(item)
            if not note.agent_id or not note.signature:
                continue
            notes_by_key[
                _note_key(agent_id=note.agent_id, signature=note.signature)
            ] = note.to_dict()
        return notes_by_key

    def _write_index(self, notes_by_key: Mapping[str, Mapping[str, object]]) -> None:
        # SRR-5-05: atomic index write via tempfile + os.replace. Prevents
        self._notes_root.mkdir(parents=True, exist_ok=True)
        notes = [dict(item) for item in notes_by_key.values()]
        notes.sort(key=lambda item: str(item.get("updated_at", "")), reverse=True)
        payload = {
            "version": 1,
            "notes": notes,
        }
        serialized = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        # Write to a tempfile in the same directory so os.replace is atomic.
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(self._notes_root),
            prefix=".notes_index.",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            tmp.write(serialized)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_path = Path(tmp.name)
        os.replace(tmp_path, self._index_path)

    def _write_markdown_note(self, note: ImprovementNote) -> None:
        self._notes_root.mkdir(parents=True, exist_ok=True)
        path = self._note_markdown_path(note)
        tags = ", ".join(note.tags)
        triggers = ", ".join(note.trigger_tokens)
        markdown = (
            f"# Improvement Note: {note.signature}\n\n"
            f"- agent_id: {note.agent_id}\n"
            f"- signature: {note.signature}\n"
            f"- status: {note.status}\n"
            f"- source: {note.source}\n"
            f"- occurrence_count: {note.occurrence_count}\n"
            f"- apply_count: {note.apply_count}\n"
            f"- created_at: {note.created_at}\n"
            f"- updated_at: {note.updated_at}\n"
            f"- last_applied_at: {note.last_applied_at or 'n/a'}\n"
            f"- tags: {tags or 'n/a'}\n"
            f"- trigger_tokens: {triggers or 'n/a'}\n\n"
            "## Context\n\n"
            f"{note.context or 'n/a'}\n\n"
            "## Guidance\n\n"
            f"{note.guidance or 'n/a'}\n"
        )
        path.write_text(markdown, encoding="utf-8")

    def _note_markdown_path(self, note: ImprovementNote) -> Path:
        agent_slug = _slugify(note.agent_id, default="agent")
        signature_slug = _slugify(note.signature, default="note")
        return self._notes_root / f"{agent_slug}--{signature_slug}.md"


def _note_key(*, agent_id: str, signature: str) -> str:
    return f"{str(agent_id).strip()}::{str(signature).strip()}"


def _failure_signature_for_tool_result(result: ToolExecutionResult) -> str:
    tool_name = _slugify(result.tool_name, default="tool")
    if not result.ok:
        error_text = str(result.error or "").strip().lower()
        if (
            error_text
            and "missing" in error_text
            and ("arg" in error_text or "required" in error_text)
        ):
            reason = "missing_required_args"
        else:
            reason = _slugify(result.error, default="error", max_len=40)
    elif not result.verified:
        reason = "unverified"
    else:
        reason = "unknown"
    return f"tool.{tool_name}.{reason}"


def _build_context_excerpt(*, user_message: str, result: ToolExecutionResult) -> str:
    user_excerpt = str(user_message or "").strip()
    if len(user_excerpt) > SELF_IMPROVEMENT_MAX_CONTEXT_CHARS:
        user_excerpt = (
            user_excerpt[:SELF_IMPROVEMENT_MAX_CONTEXT_CHARS].rstrip() + "..."
        )
    error_excerpt = str(result.error or "").strip()
    if len(error_excerpt) > SELF_IMPROVEMENT_MAX_CONTEXT_CHARS:
        error_excerpt = (
            error_excerpt[:SELF_IMPROVEMENT_MAX_CONTEXT_CHARS].rstrip() + "..."
        )
    return (
        f"user_message: {user_excerpt or 'n/a'}\n"
        f"tool_name: {result.tool_name}\n"
        f"error: {error_excerpt or 'n/a'}\n"
        f"verified: {str(result.verified).lower()}"
    )


def _build_tags(result: ToolExecutionResult) -> Iterable[str]:
    tags = [f"tool:{_slugify(result.tool_name, default='tool')}"]
    if result.error:
        tags.append(f"error:{_slugify(result.error, default='error', max_len=40)}")
    if not result.verified:
        tags.append("verification:false")
    return tags


def _build_trigger_tokens(user_message: str, result: ToolExecutionResult) -> set[str]:
    tokens = set(_tokenize(user_message))
    tool_token = _slugify(result.tool_name, default="")
    if tool_token:
        tokens.add(tool_token)
    if result.error:
        tokens |= _tokenize(result.error)
    return tokens


def _build_guidance_for_tool_failure(result: ToolExecutionResult) -> str:
    tool_name = str(result.tool_name or "tool")
    if result.error:
        return (
            f"Before calling `{tool_name}`, validate required arguments and constraints, "
            "then retry once with corrected structured arguments. Do not claim success when verification fails."
        )
    if not result.verified:
        return (
            f"Treat `{tool_name}` output as untrusted until verified; if verification fails, "
            "retry with stricter arguments or ask for clarification."
        )
    return f"Validate `{tool_name}` inputs and verification signals before finalizing the response."
