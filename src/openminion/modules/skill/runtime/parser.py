from __future__ import annotations

import re
from typing import Any

from openminion.modules.skill.constants import HIGH_RISK_CLASSES
from openminion.modules.skill.models import (
    RecipeStep,
    ToolRecipe,
    first_sentence,
    normalize_text_list,
    slugify,
)
from openminion.modules.skill.scalars import parse_scalar

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover
    yaml = None  # type: ignore[assignment]

_FRONTMATTER_BOUNDARY = "---"
_SECTION_RE = re.compile(r"^\s*#{1,3}\s+(.+?)\s*$")
_TOOL_RE = re.compile(r"\b[a-zA-Z][a-zA-Z0-9_]*\.[a-zA-Z][a-zA-Z0-9_\.\-]*\b")

RECOGNIZED_FRONT_MATTER_KEYS: frozenset[str] = frozenset(
    {
        "name",
        "id",
        "status",
        "risk",
        "tools",
        "tags",
        "applies_to",
        "inputs",
        "version",
        "verification",
        "rollback",
        "recipe",
        "references",
        "description",
        "metadata",
        "license",
        "compatibility",
        "allowed-tools",
        "objective",
        "preflight",
        "stop_conditions",
        "safety_notes",
        "idempotency_notes",
        "teaches",
        "requires_tools",
        "safe_for_domains",
        "forbidden_claims",
        "evidence_expectations",
    }
)


def front_matter_unknown_key_warnings(front_matter: dict[str, Any]) -> list[str]:
    """Return one warning per unique unknown top-level key."""
    if not isinstance(front_matter, dict):
        return []
    warnings: list[str] = []
    seen: set[str] = set()
    for raw_key in front_matter.keys():
        key = str(raw_key).strip()
        if not key or key in seen:
            continue
        if key in RECOGNIZED_FRONT_MATTER_KEYS:
            continue
        seen.add(key)
        warnings.append(f"parse.warning:unknown_front_matter_key:{key}")
    return warnings


def normalize_render_purpose(purpose: str, mode_name: str | None = None) -> str:
    normalized_mode = str(mode_name or "").strip().lower()
    normalized_purpose = str(purpose or "").strip().lower()

    if normalized_mode in {"respond", "act", "plan"}:
        return normalized_mode

    alias_map = {
        "decide": "plan",
        "respond": "respond",
        "act": "act",
        "plan": "plan",
        "verify": "verify",
    }
    return alias_map.get(normalized_purpose, normalized_purpose)


def parse_markdown(
    markdown: str,
) -> tuple[dict[str, Any], dict[str, str], str, list[str]]:
    text = (markdown or "").replace("\r\n", "\n")
    front_matter: dict[str, Any] = {}
    warnings: list[str] = []

    body = text
    lines = text.split("\n")
    if lines and lines[0].strip() == _FRONTMATTER_BOUNDARY:
        boundary_idx: int | None = None
        for idx in range(1, len(lines)):
            if lines[idx].strip() == _FRONTMATTER_BOUNDARY:
                boundary_idx = idx
                break
        if boundary_idx is None:
            warnings.append("front_matter.unclosed")
        else:
            fm_text = "\n".join(lines[1:boundary_idx])
            body = "\n".join(lines[boundary_idx + 1 :])
            parsed = _parse_front_matter(fm_text)
            if isinstance(parsed, dict):
                front_matter = parsed
            else:
                warnings.append("front_matter.invalid_mapping")

    sections, section_warnings = _split_sections(body)
    warnings.extend(section_warnings)
    summary = sections.get("summary", "")
    if not summary:
        summary = _first_nonempty_paragraph(body)

    return front_matter, sections, summary, warnings


def build_recipe(
    *,
    front_matter: dict[str, Any],
    skill_name: str,
    risk_class: str,
    known_tools: list[str],
) -> tuple[ToolRecipe | None, list[str]]:
    raw_recipe = front_matter.get("recipe")
    if raw_recipe is None:
        return None, []
    if not isinstance(raw_recipe, dict):
        return None, ["parse.warning:invalid_recipe"]

    raw_steps = raw_recipe.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        return None, ["parse.warning:invalid_recipe"]

    known = set(known_tools)
    seen_step_ids: set[str] = set()
    steps: list[RecipeStep] = []
    for raw_step in raw_steps:
        if not isinstance(raw_step, dict):
            return None, ["parse.warning:invalid_recipe"]
        step_id = str(raw_step.get("step_id", "")).strip()
        instruction = str(raw_step.get("instruction", "")).strip()
        if not step_id or step_id in seen_step_ids or not instruction:
            return None, ["parse.warning:invalid_recipe"]
        seen_step_ids.add(step_id)

        requested_tool = str(raw_step.get("tool_id", "")).strip()
        steps.append(
            RecipeStep(
                step_id=step_id,
                instruction=instruction,
                tool_id=requested_tool if requested_tool in known else None,
                input_schema=(
                    dict(raw_step["input_schema"])
                    if isinstance(raw_step.get("input_schema"), dict)
                    else None
                ),
            )
        )

    safety_notes = normalize_text_list(raw_recipe.get("safety_notes"))
    if risk_class in HIGH_RISK_CLASSES:
        safety_notes.append(
            f"Risk class is {risk_class}; require policy gate before side effects."
        )

    idempotency_notes = raw_recipe.get("idempotency_notes")
    return ToolRecipe(
        objective=str(raw_recipe.get("objective", "")).strip()
        or first_sentence(skill_name),
        preflight=normalize_text_list(raw_recipe.get("preflight")),
        steps=steps,
        verification=normalize_text_list(raw_recipe.get("verification")),
        rollback=normalize_text_list(raw_recipe.get("rollback")),
        stop_conditions=normalize_text_list(raw_recipe.get("stop_conditions")),
        idempotency_notes=(
            str(idempotency_notes).strip()
            if isinstance(idempotency_notes, str) and idempotency_notes.strip()
            else None
        ),
        safety_notes=safety_notes,
    ), []


def detect_tools(text: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for match in _TOOL_RE.findall(text or ""):
        tool = match.strip()
        if not tool or tool in seen:
            continue
        seen.add(tool)
        out.append(tool)
    return out


def build_default_snippets(sections: dict[str, str]) -> dict[str, str]:
    plan = _join_sections(
        sections, ["summary", "when_to_use", "preconditions", "procedure"]
    )
    act = _join_sections(sections, ["procedure", "pitfalls", "rollback"])
    verify = _join_sections(sections, ["verification", "summary"])
    return {
        "plan": plan,
        "act": act,
        "verify": verify,
    }


def purpose_to_section_keys(purpose: str) -> list[str]:
    normalized = purpose.strip().lower()
    return {
        "plan": ["summary", "when_to_use", "preconditions", "procedure"],
        "respond": ["summary", "when_to_use"],
        "act": ["procedure", "pitfalls", "rollback", "verification"],
        "verify": ["verification", "summary", "pitfalls"],
    }.get(normalized, ["summary", "procedure"])


def normalize_section_name(title: str) -> str:
    alias_map = {
        "skill_card": "summary",
        "checks": "verification",
        "verification": "verification",
        "failure_recovery": "rollback",
        "failure_and_recovery": "rollback",
        "pitfalls_and_recovery": "rollback",
        "prerequisites": "preconditions",
        "precondition": "preconditions",
        "when_to_use": "when_to_use",
        "usage": "when_to_use",
        "overview": "summary",
        "purpose": "summary",
        "quick_reference": "summary",
        "quick_start": "procedure",
        "process": "procedure",
        "recipe": "procedure",
        "the_process": "procedure",
        "references": "references",
        "reference_files": "references",
    }
    slug = slugify(title, fallback="section")
    return alias_map.get(slug, slug)


def _parse_front_matter(raw: str) -> dict[str, Any] | None:
    if yaml is not None:
        try:
            parsed = yaml.safe_load(raw) or {}
        except Exception:
            parsed = None
        if isinstance(parsed, dict):
            return parsed
    return _parse_front_matter_fallback(raw)


def _split_sections(body: str) -> tuple[dict[str, str], list[str]]:
    lines = body.split("\n")
    current = "body"
    chunks: dict[str, list[str]] = {current: []}
    warnings: list[str] = []
    seen_h1 = False
    seen_h2_warning_slugs: set[str] = set()

    for line in lines:
        h3_match = re.match(r"^\s*(#{3})\s+(.+?)\s*$", line)
        h2_match = None if h3_match else re.match(r"^\s*(#{2})\s+(.+?)\s*$", line)
        h1_match = (
            None
            if (h3_match or h2_match)
            else re.match(r"^\s*(#{1})\s+(.+?)\s*$", line)
        )

        if h1_match:
            seen_h1 = True
            current = normalize_section_name(h1_match.group(2))
            if current not in chunks:
                chunks[current] = []
        elif h2_match:
            h2_title = h2_match.group(2)
            if not seen_h1:
                current = normalize_section_name(h2_title)
                if current not in chunks:
                    chunks[current] = []
            else:
                flatten_slug = normalize_section_name(h2_title)
                if flatten_slug not in seen_h2_warning_slugs:
                    seen_h2_warning_slugs.add(flatten_slug)
                    warnings.append(
                        f"parse.warning:h2_flattened_into_parent:{flatten_slug}"
                    )
                if current in chunks:
                    chunks[current].append(f"## {h2_title}")
                else:
                    chunks[current] = [f"## {h2_title}"]
        elif h3_match:
            if current in chunks:
                chunks[current].append(f"### {h3_match.group(2)}")
            else:
                chunks[current] = [f"### {h3_match.group(2)}"]
        else:
            if current in chunks:
                chunks[current].append(line)
            else:
                chunks[current] = [line]

    out: dict[str, str] = {}
    for key, value in chunks.items():
        text = "\n".join(value).strip()
        if text:
            out[key] = text
    return out, warnings


def _first_nonempty_paragraph(body: str) -> str:
    paragraphs = [part.strip() for part in (body or "").split("\n\n") if part.strip()]
    if not paragraphs:
        return ""
    return paragraphs[0]


def _join_sections(sections: dict[str, str], keys: list[str]) -> str:
    parts: list[str] = []
    for key in keys:
        section = sections.get(key, "").strip()
        if not section:
            continue
        title = key.replace("_", " ").title()
        parts.append(f"{title}:\n{section}")
    return "\n\n".join(parts).strip()


def _dedupe_parser_values(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in values:
        text = str(raw).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _parse_front_matter_fallback(raw: str) -> dict[str, Any] | None:
    """Minimal front-matter parser used when PyYAML is unavailable."""

    result: dict[str, Any] = {}
    current_parent: str | None = None
    current_inputs_item: dict[str, Any] | None = None

    for raw_line in raw.splitlines():
        if not raw_line.strip() or raw_line.strip().startswith("#"):
            continue

        indent = len(raw_line) - len(raw_line.lstrip(" "))
        line = raw_line.strip()

        if indent == 0:
            current_inputs_item = None
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip()
            if not key:
                continue
            if value == "":
                if key in {"applies_to"}:
                    result[key] = {}
                else:
                    result[key] = []
                current_parent = key
                continue
            result[key] = parse_scalar(value)
            current_parent = None
            continue

        if current_parent is None:
            continue
        container = result.get(current_parent)
        if isinstance(container, dict):
            if ":" not in line:
                continue
            sub_key, sub_value = line.split(":", 1)
            sub_key = sub_key.strip()
            if not sub_key:
                continue
            container[sub_key] = parse_scalar(sub_value.strip())
            continue

        if not isinstance(container, list):
            continue

        if line.startswith("- "):
            payload = line[2:].strip()
            if current_parent == "inputs":
                if ":" in payload:
                    item_key, item_value = payload.split(":", 1)
                    item = {item_key.strip(): parse_scalar(item_value.strip())}
                elif payload:
                    item = {"name": parse_scalar(payload)}
                else:
                    item = {}
                container.append(item)
                current_inputs_item = item
                continue

            if ":" in payload and current_parent not in {
                "tags",
                "tools",
                "verification",
                "rollback",
            }:
                item_key, item_value = payload.split(":", 1)
                container.append({item_key.strip(): parse_scalar(item_value.strip())})
                current_inputs_item = None
                continue

            container.append(parse_scalar(payload))
            current_inputs_item = None
            continue

        if (
            current_parent == "inputs"
            and current_inputs_item is not None
            and ":" in line
        ):
            item_key, item_value = line.split(":", 1)
            item_key = item_key.strip()
            if item_key:
                current_inputs_item[item_key] = parse_scalar(item_value.strip())

    return result
