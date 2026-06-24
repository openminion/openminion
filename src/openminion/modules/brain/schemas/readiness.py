"""Brain schema readiness helpers."""

from dataclasses import dataclass
import re
from collections.abc import Mapping
from typing import Any

_UNRESOLVED_TEMPLATE_RE = re.compile(r"\{\{[^{}]+\}\}")
_BRACKET_PLACEHOLDER_RE = re.compile(r"\[(?:[A-Z][A-Z0-9]*(?:[ _-][A-Z0-9]+)*)\](?!\()")
_CONTENT_BLOB_FIELD_NAMES = frozenset({"content", "body", "text", "contents"})


@dataclass(frozen=True, slots=True)
class CommandReadinessIssue:
    code: str
    field_path: str
    placeholder_pattern: str = ""


def contains_unresolved_template_text(text: str) -> bool:
    value = str(text or "").strip()
    if not value:
        return False
    if _UNRESOLVED_TEMPLATE_RE.search(value):
        return True
    return _BRACKET_PLACEHOLDER_RE.search(value) is not None


def find_unknown_sentinel_path(value: Any, *, prefix: str) -> str | None:
    if isinstance(value, str):
        return prefix if value.strip() == "<UNKNOWN>" else None
    if isinstance(value, Mapping):
        for key, item in value.items():
            child = find_unknown_sentinel_path(
                item,
                prefix=f"{prefix}.{key}",
            )
            if child:
                return child
        return None
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            child = find_unknown_sentinel_path(
                item,
                prefix=f"{prefix}[{index}]",
            )
            if child:
                return child
    return None


def find_unresolved_template_path(
    value: Any,
    *,
    prefix: str,
) -> tuple[str, str] | None:
    if isinstance(value, str):
        field_name = str(prefix.rsplit(".", 1)[-1] or "").strip().lower()
        if field_name in _CONTENT_BLOB_FIELD_NAMES:
            return None
        candidate = str(value or "")
        template_match = _UNRESOLVED_TEMPLATE_RE.search(candidate)
        if template_match is not None:
            return prefix, template_match.group(0)
        bracket_match = _BRACKET_PLACEHOLDER_RE.search(candidate)
        if bracket_match is not None:
            return prefix, bracket_match.group(0)
        return None
    if isinstance(value, Mapping):
        for key, item in value.items():
            child = find_unresolved_template_path(
                item,
                prefix=f"{prefix}.{key}",
            )
            if child is not None:
                return child
        return None
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            child = find_unresolved_template_path(
                item,
                prefix=f"{prefix}[{index}]",
            )
            if child is not None:
                return child
    return None


def command_payload_prefix(command: Any, *, prefix: str) -> tuple[str, Any] | None:
    kind = str(getattr(command, "kind", "") or "").strip().lower()
    if kind == "tool":
        return f"{prefix}.args", getattr(command, "args", {}) or {}
    if kind == "agent":
        return f"{prefix}.params", getattr(command, "params", {}) or {}
    return None


def validate_command_readiness(
    command: Any,
    *,
    prefix: str,
) -> CommandReadinessIssue | None:
    payload = command_payload_prefix(command, prefix=prefix)
    if payload is None:
        return None
    payload_prefix, payload_value = payload
    unknown_path = find_unknown_sentinel_path(payload_value, prefix=payload_prefix)
    if unknown_path is not None:
        return CommandReadinessIssue(
            code="decision_readiness_unresolved_sentinel",
            field_path=unknown_path,
            placeholder_pattern="<UNKNOWN>",
        )
    unresolved_template = find_unresolved_template_path(
        payload_value,
        prefix=payload_prefix,
    )
    if unresolved_template is not None:
        field_path, pattern = unresolved_template
        return CommandReadinessIssue(
            code="decision_readiness_unresolved_template",
            field_path=field_path,
            placeholder_pattern=pattern,
        )
    return None


def payload_is_contextually_empty(value: Any) -> bool:
    if value in (None, "", {}, [], ()):
        return True
    saw_leaf = False
    saw_non_empty_string = False
    saw_non_string_leaf = False

    def _walk(node: Any) -> None:
        nonlocal saw_leaf, saw_non_empty_string, saw_non_string_leaf
        if isinstance(node, Mapping):
            for item in node.values():
                _walk(item)
            return
        if isinstance(node, (list, tuple)):
            for item in node:
                _walk(item)
            return
        saw_leaf = True
        if isinstance(node, str):
            if str(node).strip():
                saw_non_empty_string = True
            return
        if node is not None:
            saw_non_string_leaf = True

    _walk(value)
    if not saw_leaf:
        return True
    if saw_non_string_leaf:
        return False
    return not saw_non_empty_string
