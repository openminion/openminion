import logging
from typing import Any

from pydantic import ValidationError

from openminion.modules.tool.contracts.model_ids import (
    MODEL_SKILL_GET,
    MODEL_SKILL_INGEST,
    MODEL_SKILL_INGEST_URL,
    MODEL_SKILL_INSPECT,
    MODEL_SKILL_LIST,
    MODEL_SKILL_REMOVE,
)
from openminion.modules.tool.registry import ToolRegistry, ToolSpec

from .inspect import scan
from .schemas import (
    SkillGetArgs,
    SkillIngestArgs,
    SkillIngestUrlArgs,
    SkillInspectArgs,
    SkillListArgs,
    SkillRemoveArgs,
)
from .url_ingest import ingest_skill_url

_LOG = logging.getLogger(__name__)


def _invalid_args_error(exc: ValidationError) -> dict[str, Any]:
    return {"ok": False, "error": {"code": "INVALID_ARGS", "message": str(exc)}}


def _h_skill_inspect(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    del ctx
    try:
        parsed = SkillInspectArgs.model_validate(args)
    except ValidationError as exc:
        return _invalid_args_error(exc)
    risk_level, issues = scan(parsed.markdown)
    safe = risk_level != "critical"
    return {
        "ok": True,
        "risk_level": risk_level,
        "safe": safe,
        "issues": issues,
    }


def _h_skill_ingest(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    skill = getattr(ctx, "skill_api", None)
    if skill is None:
        return {
            "ok": False,
            "error": {
                "code": "SKILL_UNAVAILABLE",
                "message": "Skill service is not available in this runtime context.",
            },
        }

    try:
        parsed_args = SkillIngestArgs.model_validate(args)
    except ValidationError as exc:
        return _invalid_args_error(exc)

    name = parsed_args.name
    markdown = parsed_args.markdown
    scope = parsed_args.scope
    max_snippet_tokens = parsed_args.max_snippet_tokens
    enforce_safety = parsed_args.enforce_safety
    trust = parsed_args.trust

    risk_level, issues = scan(markdown)
    safe = risk_level != "critical"
    if enforce_safety and not safe:
        return {
            "ok": False,
            "error": {
                "code": "SAFETY_REJECTED",
                "message": "Skill ingest blocked due to critical safety findings.",
            },
            "risk_level": risk_level,
            "safe": False,
            "issues": issues,
        }

    try:
        skill_id, version_hash, warnings = skill.ingest_text(
            name=name,
            markdown=markdown,
            scope=scope,
            trust=trust,
            promotion_path="runtime",
        )
    except Exception as exc:
        return {
            "ok": False,
            "error": {
                "code": "INGEST_FAILED",
                "message": str(exc),
            },
        }

    snippet = ""
    snippet_hash = ""
    try:
        snippet, snippet_hash = skill.render_snippet(
            skill_id=skill_id,
            version_hash=version_hash,
            purpose="act",
            max_tokens=max_snippet_tokens,
        )
    except Exception as exc:
        _LOG.warning(
            "skill snippet render failed: skill_id=%s version=%s err=%s: %s",
            skill_id,
            version_hash,
            type(exc).__name__,
            exc,
        )

    return {
        "ok": True,
        "skill_id": skill_id,
        "version_hash": version_hash,
        "snippet": snippet,
        "snippet_hash": snippet_hash,
        "warnings": list(warnings or []),
        "risk_level": risk_level,
        "safe": safe,
        "issues": issues,
        "safety_enforced": enforce_safety,
    }


def _h_skill_ingest_url(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    skill = getattr(ctx, "skill_api", None)
    if skill is None:
        return {
            "ok": False,
            "error": {
                "code": "SKILL_UNAVAILABLE",
                "message": "Skill service is not available in this runtime context.",
            },
        }

    try:
        parsed_args = SkillIngestUrlArgs.model_validate(args)
    except ValidationError as exc:
        return _invalid_args_error(exc)

    return ingest_skill_url(
        skill,
        url=parsed_args.url,
        name=parsed_args.name,
        scope=parsed_args.scope,
        max_snippet_tokens=parsed_args.max_snippet_tokens,
        enforce_safety=parsed_args.enforce_safety,
        trust=parsed_args.trust,
    )


def _h_skill_list(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    skill = getattr(ctx, "skill_api", None)
    if skill is None:
        return {
            "ok": False,
            "error": {
                "code": "SKILL_UNAVAILABLE",
                "message": "Skill service is not available in this runtime context.",
            },
        }

    try:
        parsed_args = SkillListArgs.model_validate(args)
    except ValidationError as exc:
        return _invalid_args_error(exc)

    filters: dict[str, Any] = {}
    for key in ("scope", "status", "tag", "tool"):
        value = getattr(parsed_args, key, None)
        if value is not None and str(value).strip():
            filters[key] = str(value).strip()

    limit = parsed_args.limit

    try:
        skills = skill.list_skills(filters)
    except Exception as exc:
        return {
            "ok": False,
            "error": {
                "code": "SKILL_LIST_FAILED",
                "message": str(exc),
            },
        }

    normalized = skills if isinstance(skills, list) else []
    result = normalized[: max(1, limit)]
    return {
        "ok": True,
        "skills": result,
        "total": len(normalized),
    }


def _h_skill_get(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    skill = getattr(ctx, "skill_api", None)
    if skill is None:
        return {
            "ok": False,
            "error": {
                "code": "SKILL_UNAVAILABLE",
                "message": "Skill service is not available in this runtime context.",
            },
        }

    try:
        parsed_args = SkillGetArgs.model_validate(args)
    except ValidationError as exc:
        return _invalid_args_error(exc)

    skill_id = parsed_args.skill_id
    version_hash = parsed_args.version_hash

    try:
        package = skill.get_skill(skill_id=skill_id, version_hash=version_hash)
    except Exception as exc:
        return {
            "ok": False,
            "error": {
                "code": str(getattr(exc, "code", "SKILL_GET_FAILED")),
                "message": str(exc),
            },
        }

    payload: dict[str, Any]
    if isinstance(package, dict):
        payload = package
    elif hasattr(package, "to_dict"):
        payload = package.to_dict()
    elif hasattr(package, "model_dump"):
        payload = package.model_dump()
    else:
        payload = {"value": str(package)}

    return {
        "ok": True,
        "skill": payload,
    }


def _h_skill_remove(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    skill = getattr(ctx, "skill_api", None)
    if skill is None:
        return {
            "ok": False,
            "error": {
                "code": "SKILL_UNAVAILABLE",
                "message": "Skill service is not available in this runtime context.",
            },
        }

    try:
        parsed_args = SkillRemoveArgs.model_validate(args)
    except ValidationError as exc:
        return _invalid_args_error(exc)

    skill_id = parsed_args.skill_id
    version_hash = parsed_args.version_hash

    try:
        deleted = skill.delete_skill(skill_id=skill_id, version_hash=version_hash)
    except Exception as exc:
        return {
            "ok": False,
            "error": {
                "code": str(getattr(exc, "code", "SKILL_REMOVE_FAILED")),
                "message": str(exc),
            },
        }

    deleted_count = 0
    if isinstance(deleted, dict):
        for value in deleted.values():
            try:
                deleted_count += int(value)
            except (ValueError, TypeError):
                continue

    return {
        "ok": True,
        "skill_id": skill_id,
        "deleted": deleted_count,
    }


def register(registry: ToolRegistry) -> None:
    registry.add(
        ToolSpec(
            name=MODEL_SKILL_INSPECT,
            args_model=SkillInspectArgs,
            min_scope="READ_ONLY",
            handler=_h_skill_inspect,
            dangerous=False,
            idempotent=True,
            tags=("plugin", "skill", "safety"),
            capabilities=("skill", "inspect"),
        )
    )
    registry.add(
        ToolSpec(
            name=MODEL_SKILL_INGEST,
            args_model=SkillIngestArgs,
            min_scope="WRITE_SAFE",
            handler=_h_skill_ingest,
            dangerous=False,
            idempotent=False,
            tags=("plugin", "skill"),
            capabilities=("skill",),
            block_under_readonly=True,
        )
    )
    registry.add(
        ToolSpec(
            name=MODEL_SKILL_INGEST_URL,
            args_model=SkillIngestUrlArgs,
            min_scope="WRITE_SAFE",
            handler=_h_skill_ingest_url,
            dangerous=False,
            idempotent=False,
            tags=("plugin", "skill", "fetch"),
            capabilities=("skill", "fetch"),
            block_under_readonly=True,
        )
    )
    registry.add(
        ToolSpec(
            name=MODEL_SKILL_LIST,
            args_model=SkillListArgs,
            min_scope="READ_ONLY",
            handler=_h_skill_list,
            dangerous=False,
            idempotent=True,
            tags=("plugin", "skill"),
            capabilities=("skill",),
        )
    )
    registry.add(
        ToolSpec(
            name=MODEL_SKILL_GET,
            args_model=SkillGetArgs,
            min_scope="READ_ONLY",
            handler=_h_skill_get,
            dangerous=False,
            idempotent=True,
            tags=("plugin", "skill"),
            capabilities=("skill",),
        )
    )
    registry.add(
        ToolSpec(
            name=MODEL_SKILL_REMOVE,
            args_model=SkillRemoveArgs,
            min_scope="WRITE_SAFE",
            handler=_h_skill_remove,
            dangerous=False,
            idempotent=True,
            tags=("plugin", "skill"),
            capabilities=("skill",),
            block_under_readonly=True,
        )
    )


__all__ = ["register"]
