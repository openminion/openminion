# mypy: ignore-errors
from __future__ import annotations


from openminion.modules.controlplane.contracts.models import (
    CommandResult,
    ParsedCommand,
    ResolvedContext,
)


class CommandRegistryMemorySkillMixin:
    def _memory_ls(self, command: ParsedCommand, ctx: ResolvedContext) -> CommandResult:
            if self.memory_client is not None and hasattr(
                self.memory_client, "query_facts"
            ):
                limit = 10
                if command.args:
                    try:
                        limit = max(1, int(command.args[0]))
                    except (TypeError, ValueError):
                        limit = 10
                query = ""
                if len(command.args) > 1:
                    query = " ".join(command.args[1:]).strip()
                try:
                    rows = self.memory_client.query_facts(
                        session_id=ctx.session_id,
                        agent_id=ctx.agent_id,
                        query=query,
                        limit=limit,
                    )
                    lines: list[str] = []
                    for row in rows:
                        text = str(getattr(row, "text", "") or "")
                        if not text and isinstance(row, dict):
                            text = str(row.get("text", "") or "")
                        if text:
                            lines.append(f"- {text}")
                    return CommandResult(
                        ok=True,
                        text="Memory facts:\n" + ("\n".join(lines) if lines else "- none"),
                        data={
                            "candidates": [],
                            "facts": [
                                text[2:] if text.startswith("- ") else text
                                for text in lines
                            ],
                        },
                    )
                except Exception as exc:
                    return CommandResult(
                        ok=False,
                        text=f"Memory listing failed: {type(exc).__name__}: {str(exc)}",
                        error={"code": "MEMORY_QUERY_FAILED", "message": str(exc)},
                        data={"candidates": []},
                    )
            return CommandResult(
                ok=True,
                text="Memory listing not wired (no memctl config).",
                data={"candidates": []},
            )

    def _memory_promote(
            self, command: ParsedCommand, ctx: ResolvedContext
        ) -> CommandResult:
            if not command.args:
                return CommandResult(ok=False, text="Usage: /memory promote <memory_id>")
            if self.memory_client is not None and hasattr(
                self.memory_client, "promote_candidate"
            ):
                candidate_id = str(command.args[0]).strip()
                if not candidate_id:
                    return CommandResult(
                        ok=False, text="Usage: /memory promote <memory_id>"
                    )
                target_scope = (
                    str(command.args[1]).strip()
                    if len(command.args) > 1
                    else f"agent:{ctx.agent_id}"
                )
                try:
                    promoted = self.memory_client.promote_candidate(
                        candidate_id=candidate_id,
                        target_scope=target_scope,
                    )
                    promoted_id = str(getattr(promoted, "id", "") or "")
                    return CommandResult(
                        ok=True,
                        text=f"Memory {candidate_id} promoted to {target_scope}.",
                        data={
                            "candidate_id": candidate_id,
                            "target_scope": target_scope,
                            "record_id": promoted_id,
                        },
                    )
                except Exception as exc:
                    return CommandResult(
                        ok=False,
                        text=f"Memory promotion failed: {type(exc).__name__}: {str(exc)}",
                        error={"code": "MEMORY_PROMOTE_FAILED", "message": str(exc)},
                        data={"candidate_id": candidate_id, "target_scope": target_scope},
                    )
            return CommandResult(
                ok=True, text=f"Memory {command.args[0]} promoted (stub).", data={}
            )

    def _skill_ingest(
            self, command: ParsedCommand, ctx: ResolvedContext
        ) -> CommandResult:
            """SMLF-07: Handle /skill ingest command in controlplane."""
            if not command.args:
                return CommandResult(
                    ok=False,
                    text="Usage: /skill ingest <path>\nExample: /skill ingest /path/to/SKILL.md",
                )
            path = command.args[0]
            try:
                from openminion.modules.skill import Skill
                from openminion.modules.skill.constants import DEFAULT_CONFIG_FILENAME
                from openminion.modules.skill.errors import SkillError
            except ModuleNotFoundError:
                return CommandResult(
                    ok=False,
                    text="Error: openminion-skill module not available.\nInstall with: pip install openminion-skill",
                    error={
                        "code": "MODULE_NOT_FOUND",
                        "message": "openminion-skill not installed",
                    },
                )
            try:
                ctl = Skill(DEFAULT_CONFIG_FILENAME)
                try:
                    skill_id, version_hash, warnings = ctl.ingest_file(path=path)
                    lines = [
                        "Successfully ingested skill",
                        f"  skill_id: {skill_id}",
                        f"  version_hash: {version_hash[:16]}...",
                    ]
                    if warnings:
                        lines.append(f"  warnings: {len(warnings)}")
                    return CommandResult(
                        ok=True,
                        text="\n".join(lines),
                        data={
                            "skill_id": skill_id,
                            "version_hash": version_hash,
                            "warnings": warnings,
                        },
                    )
                finally:
                    ctl.close()
            except SkillError as exc:
                return CommandResult(
                    ok=False,
                    text=f"Error: {exc.code}\n  {exc.message}",
                    error=exc.to_dict(),
                )
            except Exception as exc:
                return CommandResult(
                    ok=False,
                    text=f"Error: {type(exc).__name__}: {str(exc)}",
                    error={"code": "UNKNOWN", "message": str(exc)},
                )

    def _skill_list(
            self, command: ParsedCommand, ctx: ResolvedContext
        ) -> CommandResult:
            """SMLF-07: Handle /skill list command in controlplane."""
            try:
                from openminion.modules.skill import Skill
                from openminion.modules.skill.constants import DEFAULT_CONFIG_FILENAME
                from openminion.modules.skill.errors import SkillError
            except ModuleNotFoundError:
                return CommandResult(
                    ok=False,
                    text="Error: openminion-skill module not available.\nInstall with: pip install openminion-skill",
                    error={
                        "code": "MODULE_NOT_FOUND",
                        "message": "openminion-skill not installed",
                    },
                )
            try:
                ctl = Skill(DEFAULT_CONFIG_FILENAME)
                try:
                    skills = ctl.list_skills({})
                    if not skills:
                        return CommandResult(
                            ok=True, text="No skills found.", data={"skills": []}
                        )
                    lines = [f"Found {len(skills)} skill(s):"]
                    for s in skills:
                        lines.append(
                            f"  {s['skill_id']} | {s.get('name', 'N/A')} | {s.get('status', 'N/A')}"
                        )
                    return CommandResult(
                        ok=True, text="\n".join(lines), data={"skills": skills}
                    )
                finally:
                    ctl.close()
            except SkillError as exc:
                return CommandResult(
                    ok=False,
                    text=f"Error: {exc.code}\n  {exc.message}",
                    error=exc.to_dict(),
                )
            except Exception as exc:
                return CommandResult(
                    ok=False,
                    text=f"Error: {type(exc).__name__}: {str(exc)}",
                    error={"code": "UNKNOWN", "message": str(exc)},
                )

    def _modules(self, command: ParsedCommand, ctx: ResolvedContext) -> CommandResult:
            module_info = self.list_modules()
            lines = ["Module Diagnostics:"]

            if module_info["built_in"]:
                lines.append(f"\nBuilt-in ({len(module_info['built_in'])}):")
                for mod in module_info["built_in"]:
                    lines.append(f"  - {mod} [system]")

            if module_info["loaded"]:
                lines.append(f"\nLoaded ({len(module_info['loaded'])}):")
                for mod in module_info["loaded"]:
                    version = module_info["module_details"].get(mod, "unknown")
                    lines.append(f"  - {mod}: v{version}")

            if module_info["shadowed"]:
                lines.append(f"\nShadowed ({len(module_info['shadowed'])}):")
                for mod in module_info["shadowed"]:
                    lines.append(f"  - {mod} [shadowed by builtin]")

            if module_info["broken"]:
                lines.append(f"\nBroken ({len(module_info['broken'])}):")
                for mod in module_info["broken"]:
                    error_info = module_info["errors"].get(mod, {})
                    msg = error_info.get("error_message", "unknown error")
                    timestamp = error_info.get("timestamp", "no timestamp")
                    lines.append(f"  - {mod}: {msg} [at {timestamp}]")

            if not any(
                [
                    module_info["built_in"],
                    module_info["loaded"],
                    module_info["shadowed"],
                    module_info["broken"],
                ]
            ):
                lines.append("\nNo modules detected.")

            return CommandResult(ok=True, text="\n".join(lines), data=module_info)
