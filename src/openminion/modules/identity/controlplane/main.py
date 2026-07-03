from collections.abc import Callable
import logging
import sys
from pathlib import Path
from typing import List

try:
    from openminion.base.version import OPENMINION_VERSION
    from openminion.modules.identity.config import (
        IdentityCtlConfig,
        load_config as load_identity_config,
        resolve_default_render_budget,
    )
    from openminion.modules.identity.runtime.service import IdentityCtl
    from openminion.modules.identity.runtime.renderer import normalize_purpose
    from openminion.modules.identity.storage.store import SQLiteIdentityStore
    from openminion.modules.identity.models import AgentProfile
    from openminion.modules.controlplane.commands.module import (
        CommandModule,
        CommandSpec,
        CommandSchema,
        AuthRequirement,
    )
    from openminion.modules.controlplane.contracts.models import (
        CommandResult,
        ParsedCommand,
        ResolvedContext,
    )
except ImportError as e:
    print(f"Import error in identity controlplane: {e}", file=sys.stderr)
    raise


logger = logging.getLogger(__name__)


def _identity_profile_template(agent_id: str) -> dict:
    return {
        "agent_id": agent_id,
        "display_name": agent_id,
        "profile_revision": 1,
        "role": {
            "mission": f"I am {agent_id}, a pragmatic AI assistant.",
            "responsibilities": [],
            "hard_constraints": [],
        },
        "personality": {"tone": "professional", "verbosity": "normal"},
        "risk": {"risk_level": "medium", "confirm_before": ["destructive_actions"]},
        "tool_posture": {"tool_use": "allowed"},
    }


def _deep_update(target: dict, update: dict) -> None:
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = value


def _load_identity_ctl_config() -> IdentityCtlConfig:
    try:
        return load_identity_config()
    except Exception as exc:  # noqa: BLE001
        logger.debug("identity controlplane config load failed reason=%s", exc)
        return IdentityCtlConfig()


def command_module() -> "IdentityCommandModule":
    """Factory function to create the identity command module."""
    from openminion.base.generated_paths import resolve_generated_state_path
    from openminion.modules.identity.constants import DEFAULT_IDENTITY_DB_FILENAME

    db_path = str(
        resolve_generated_state_path(DEFAULT_IDENTITY_DB_FILENAME, module="identity")
    )
    Path(db_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
    store = SQLiteIdentityStore(sqlite_path=db_path)
    identity_ctl = IdentityCtl(store=store)
    identity_cfg = _load_identity_ctl_config()

    return IdentityCommandModule(identity_ctl, identity_cfg=identity_cfg)


class IdentityCommandModule(CommandModule):
    """Command module for identity operations."""

    def __init__(
        self,
        identity_ctl: IdentityCtl,
        *,
        identity_cfg: IdentityCtlConfig | None = None,
    ):
        self._identity_ctl = identity_ctl
        self._identity_cfg = identity_cfg or IdentityCtlConfig()

    @property
    def name(self) -> str:
        """Module name."""
        return "identity"

    @property
    def version(self) -> str:
        """Module version."""
        return OPENMINION_VERSION

    @property
    def description(self) -> str:
        """Module description."""
        return "Identity profile management commands"

    def get_commands(self) -> List[CommandSpec]:
        """Return list of identity-related command specifications."""
        return [
            self._command_spec(
                "identity.list",
                "List all identity profiles",
                "/identity.list",
                self.handle_list,
            ),
            self._command_spec(
                "identity.show",
                "Show details of a specific identity profile",
                "/identity.show <agent_id>",
                self.handle_show,
                required_args=["agent_id"],
            ),
            self._command_spec(
                "identity.upsert",
                "Create or update an identity profile",
                "/identity.upsert <agent_id> [json_data]",
                self.handle_upsert,
                required_args=["agent_id"],
                optional_args=["json_data"],
            ),
            self._command_spec(
                "identity.delete",
                "Delete an identity profile",
                "/identity.delete <agent_id>",
                self.handle_delete,
                required_args=["agent_id"],
            ),
            self._command_spec(
                "identity.render",
                "Render identity snippet for a specific agent and purpose",
                "/identity.render <agent_id> <purpose>",
                self.handle_render,
                required_args=["agent_id", "purpose"],
            ),
            self._command_spec(
                "identity.set.tone",
                "Quickly set the tone for an agent",
                "/identity.set.tone <agent_id> <tone>",
                self.handle_set_tone,
                required_args=["agent_id", "tone"],
            ),
            self._command_spec(
                "identity.set.verbosity",
                "Quickly set the verbosity for an agent",
                "/identity.set.verbosity <agent_id> <verbosity>",
                self.handle_set_verbosity,
                required_args=["agent_id", "verbosity"],
            ),
            self._command_spec(
                "identity.set.mission",
                "Quickly set the mission statement for an agent",
                "/identity.set.mission <agent_id> <mission_text>",
                self.handle_set_mission,
                required_args=["agent_id", "mission_text"],
            ),
            self._command_spec(
                "identity.create",
                "Create identity profile interactively using wizard",
                "/identity.create",
                self.handle_create_interactive,
            ),
            self._command_spec(
                "identity.edit",
                "Edit identity profile interactively using wizard",
                "/identity.edit <agent_id>",
                self.handle_edit_interactive,
                required_args=["agent_id"],
            ),
        ]

    def _command_spec(
        self,
        name: str,
        description: str,
        usage: str,
        handler: object,
        *,
        required_args: list[str] | None = None,
        optional_args: list[str] | None = None,
    ) -> CommandSpec:
        return CommandSpec(
            name=name,
            schema=CommandSchema(
                name=name,
                description=description,
                usage=usage,
                required_args=required_args or [],
                optional_args=optional_args or [],
            ),
            handler=handler,
            auth_requirement=AuthRequirement.USER,
            module_name=self.name,
            version=self.version,
        )

    def _error_result(self, *, code: str, prefix: str, exc: Exception) -> CommandResult:
        message = str(exc)
        return CommandResult(
            ok=False,
            text=f"{prefix}: {message}",
            error={"code": code, "message": message},
        )

    def _get_profile_or_not_found(self, agent_id: str) -> AgentProfile | CommandResult:
        profile = self._identity_ctl.get_profile(agent_id)
        if profile:
            return profile
        return CommandResult(ok=False, text=f"No profile found for agent: {agent_id}")

    def _save_profile_update(
        self,
        agent_id: str,
        *,
        build_update: Callable[[AgentProfile], dict[str, object]],
        success_text: str,
        success_data: dict[str, object],
        error_code: str,
        error_prefix: str,
    ) -> CommandResult:
        try:
            profile = self._get_profile_or_not_found(agent_id)
            if isinstance(profile, CommandResult):
                return profile
            updated_profile = profile.model_copy(
                update={
                    "profile_revision": profile.profile_revision + 1,
                    **build_update(profile),
                }
            )
            self._identity_ctl.upsert_profile(updated_profile)
            return CommandResult(
                ok=True,
                text=success_text,
                data={
                    "agent_id": agent_id,
                    "profile_revision": updated_profile.profile_revision,
                    **success_data,
                },
            )
        except Exception as exc:  # noqa: BLE001
            return self._error_result(
                code=error_code,
                prefix=error_prefix,
                exc=exc,
            )

    def handle_list(
        self, command: ParsedCommand, ctx: ResolvedContext
    ) -> CommandResult:
        """Handle the identity list command."""
        try:
            profiles = self._identity_ctl.list_profiles()
            if not profiles:
                return CommandResult(
                    ok=True, text="No identity profiles found.", data={"profiles": []}
                )

            lines = ["Identity Profiles:"]
            for profile in profiles:
                lines.append(f"  - {profile.agent_id}")

            return CommandResult(
                ok=True,
                text="\n".join(lines),
                data={"profiles": [p.dict() for p in profiles]},
            )
        except Exception as exc:  # noqa: BLE001
            return self._error_result(
                code="IDENTITY_LIST_ERROR",
                prefix="Error listing identity profiles",
                exc=exc,
            )

    def handle_show(
        self, command: ParsedCommand, ctx: ResolvedContext
    ) -> CommandResult:
        """Handle the identity show command."""
        if not command.args:
            return CommandResult(ok=False, text="Usage: /identity.show <agent_id>")

        agent_id = command.args[0]

        try:
            profile = self._get_profile_or_not_found(agent_id)
            if isinstance(profile, CommandResult):
                return profile

            profile_dict = profile.dict()
            lines = [f"Identity Profile for: {profile_dict['agent_id']}"]
            lines.append(f"  Version: {profile_dict['profile_revision']}")
            lines.append(f"  Name: {profile_dict['display_name']}")
            lines.append(f"  Mission: {profile_dict['role']['mission']}")
            lines.append(
                f"  Role - Responsibilities: {len(profile_dict['role']['responsibilities'])} listed"
            )
            lines.append(
                f"  Role - Constraints: {len(profile_dict['role']['hard_constraints'])} listed"
            )
            lines.append(f"  Tone: {profile_dict['personality']['tone']}")
            lines.append(f"  Verbosity: {profile_dict['personality']['verbosity']}")
            lines.append(f"  Risk Level: {profile_dict['risk']['risk_level']}")
            lines.append(f"  Tool Posture: {profile_dict['tool_posture']['tool_use']}")

            return CommandResult(ok=True, text="\n".join(lines), data=profile_dict)
        except Exception as exc:  # noqa: BLE001
            return self._error_result(
                code="IDENTITY_SHOW_ERROR",
                prefix="Error retrieving profile",
                exc=exc,
            )

    def handle_upsert(
        self, command: ParsedCommand, ctx: ResolvedContext
    ) -> CommandResult:
        """Handle the identity upsert command."""
        if not command.args:
            return CommandResult(
                ok=False, text="Usage: /identity.upsert <agent_id> [json_data]"
            )
        agent_id = command.args[0]
        if len(command.args) == 1:
            return self._current_or_template_profile(agent_id)
        return self._apply_upsert_json(agent_id, " ".join(command.args[1:]))

    def _current_or_template_profile(self, agent_id: str) -> CommandResult:
        try:
            profile = self._identity_ctl.get_profile(agent_id)
            if profile:
                profile_dict = profile.dict()
                return CommandResult(
                    ok=True,
                    text=f"Current profile:\n```json\n{repr(profile_dict)}\n```\nEdit and provide JSON data in subsequent command.",
                    data=profile_dict,
                )
            template = _identity_profile_template(agent_id)
            return CommandResult(
                ok=True,
                text=f"No profile found for {agent_id}. Here's a template to fill:\n```json\n{repr(template)}\n```\nUse: /identity.upsert {agent_id} '<json_data>'",
                data=template,
            )
        except Exception as exc:  # noqa: BLE001
            return self._error_result(
                code="IDENTITY_UPSERT_FETCH_ERROR",
                prefix="Error getting current profile",
                exc=exc,
            )

    def _apply_upsert_json(self, agent_id: str, json_str: str) -> CommandResult:
        import json

        try:
            update_obj = json.loads(json_str)
            profile = self._identity_ctl.get_profile(agent_id)
            if profile:
                profile_dict = profile.dict()
                _deep_update(profile_dict, update_obj)
                updated_profile = AgentProfile.model_validate(profile_dict)
                self._identity_ctl.upsert_profile(updated_profile)
                return CommandResult(
                    ok=True,
                    text=f"Profile for {agent_id} updated successfully.",
                    data=updated_profile.dict(),
                )
            update_obj.setdefault("agent_id", agent_id)
            update_obj.setdefault("display_name", agent_id)
            new_profile = AgentProfile.model_validate(update_obj)
            self._identity_ctl.upsert_profile(new_profile)
            return CommandResult(
                ok=True,
                text=f"New profile for {agent_id} created successfully.",
                data=new_profile.dict(),
            )
        except json.JSONDecodeError as e:
            return CommandResult(
                ok=False,
                text=f"Invalid JSON format: {str(e)}",
                error={"code": "JSON_DECODE_ERROR", "message": str(e)},
            )
        except Exception as exc:  # noqa: BLE001
            return self._error_result(
                code="PROFILE_UPDATE_ERROR",
                prefix="Error updating profile",
                exc=exc,
            )

    def handle_delete(
        self, command: ParsedCommand, ctx: ResolvedContext
    ) -> CommandResult:
        """Handle the identity delete command."""
        if not command.args:
            return CommandResult(ok=False, text="Usage: /identity.delete <agent_id>")

        agent_id = command.args[0]

        try:
            profile = self._get_profile_or_not_found(agent_id)
            if isinstance(profile, CommandResult):
                return profile

            self._identity_ctl.delete_profile(agent_id)
            return CommandResult(
                ok=True,
                text=f"Profile for {agent_id} deleted successfully.",
                data={"agent_id": agent_id, "deleted": True},
            )
        except Exception as exc:  # noqa: BLE001
            return self._error_result(
                code="IDENTITY_DELETE_ERROR",
                prefix="Error deleting profile",
                exc=exc,
            )

    def handle_render(
        self, command: ParsedCommand, ctx: ResolvedContext
    ) -> CommandResult:
        """Handle the identity render command."""
        if len(command.args) < 2:
            return CommandResult(
                ok=False, text="Usage: /identity.render <agent_id> <purpose>"
            )

        agent_id = command.args[0]
        requested_purpose = command.args[1]

        try:
            purpose = normalize_purpose(requested_purpose)
            max_tokens = resolve_default_render_budget(
                purpose, identity_cfg=self._identity_cfg
            )

            snippet = self._identity_ctl.render(
                agent_id=agent_id, purpose=purpose, max_tokens=max_tokens
            )

            lines = [
                f"Identity Snippet for: {agent_id}",
                f"Purpose: {purpose}",
                f"Max Tokens: {max_tokens}",
                f"Actual Tokens used: {snippet.budget.used_tokens if snippet.budget else 'N/A'}",
                "=" * 40,
                snippet.text,
            ]

            data = {
                "agent_id": agent_id,
                "purpose": purpose,
                "requested_purpose": requested_purpose,
                "text": snippet.text,
                "snippet_render_version": getattr(snippet, "render_version", None),
            }
            if snippet.budget:
                data.update(
                    {
                        "used_tokens": snippet.budget.used_tokens,
                        "max_tokens": max_tokens,
                    }
                )
            return CommandResult(
                ok=True,
                text="\n".join(lines),
                data=data,
            )
        except Exception as exc:  # noqa: BLE001
            return self._error_result(
                code="IDENTITY_RENDER_ERROR",
                prefix="Error rendering identity",
                exc=exc,
            )

    def handle_set_tone(
        self, command: ParsedCommand, ctx: ResolvedContext
    ) -> CommandResult:
        """Handle the quick tone set command."""
        if len(command.args) < 2:
            return CommandResult(
                ok=False, text="Usage: /identity.set.tone <agent_id> <tone>"
            )

        agent_id = command.args[0]
        tone = command.args[1]
        normalized_tone = tone.lower()

        supported_tones = [
            "formal",
            "casual",
            "professional",
            "friendly",
            "humorous",
            "neutral",
            "authoritative",
        ]
        if normalized_tone not in supported_tones:
            return CommandResult(
                ok=False,
                text=f"Unsupported tone: {tone}. Supported tones: {', '.join(supported_tones)}",
            )

        return self._save_profile_update(
            agent_id,
            build_update=lambda profile: {
                "personality": profile.personality.model_copy(
                    update={"tone": normalized_tone}
                )
            },
            success_text=f"Tone for {agent_id} updated to '{tone}'",
            success_data={"tone": tone},
            error_code="TONE_UPDATE_ERROR",
            error_prefix="Error updating tone",
        )

    def handle_set_verbosity(
        self, command: ParsedCommand, ctx: ResolvedContext
    ) -> CommandResult:
        """Handle the quick verbosity set command."""
        if len(command.args) < 2:
            return CommandResult(
                ok=False, text="Usage: /identity.set.verbosity <agent_id> <verbosity>"
            )

        agent_id = command.args[0]
        verbosity = command.args[1]
        normalized_verbosity = verbosity.lower()

        supported_verbosities = ["terse", "normal", "verbose", "detailed"]
        if normalized_verbosity not in supported_verbosities:
            return CommandResult(
                ok=False,
                text=f"Unsupported verbosity: {verbosity}. Supported: {', '.join(supported_verbosities)}",
            )

        return self._save_profile_update(
            agent_id,
            build_update=lambda profile: {
                "personality": profile.personality.model_copy(
                    update={"verbosity": normalized_verbosity}
                )
            },
            success_text=f"Verbosity for {agent_id} updated to '{verbosity}'",
            success_data={"verbosity": verbosity},
            error_code="VERBOSITY_UPDATE_ERROR",
            error_prefix="Error updating verbosity",
        )

    def handle_set_mission(
        self, command: ParsedCommand, ctx: ResolvedContext
    ) -> CommandResult:
        """Handle the quick mission statement set command."""
        if len(command.args) < 2:
            return CommandResult(
                ok=False, text="Usage: /identity.set.mission <agent_id> <mission_text>"
            )

        agent_id = command.args[0]
        mission = " ".join(command.args[1:])

        if len(mission) > 500:
            return CommandResult(
                ok=False, text="Mission text is too long (max 500 characters)"
            )

        return self._save_profile_update(
            agent_id,
            build_update=lambda profile: {
                "role": profile.role.model_copy(update={"mission": mission})
            },
            success_text=f"Mission for {agent_id} updated successfully",
            success_data={"mission": mission},
            error_code="MISSION_UPDATE_ERROR",
            error_prefix="Error updating mission",
        )

    def handle_create_interactive(
        self, command: ParsedCommand, ctx: ResolvedContext
    ) -> CommandResult:
        """Handle the interactive identity create command."""
        try:
            ui = getattr(ctx, "ui", None)
            if not ui:
                return CommandResult(
                    ok=False,
                    text="Interactive wizard requires UI context. Use in a chat interface that supports wizards.",
                )

            wizard_session_id = getattr(ctx, "wizard_session_id", None)
            if wizard_session_id:
                return CommandResult(
                    ok=False,
                    text="Already in a wizard session. Complete or cancel that session before starting a new one.",
                )

            wizard_id = f"identity-create-{ctx.user_key}-{ctx.session_id}"
            return CommandResult(
                ok=True,
                text="Starting interactive identity creation. What should the agent ID be?",
                data={"wizard_id": wizard_id},
            )
        except Exception as exc:  # noqa: BLE001
            return self._error_result(
                code="INTERACTIVE_CREATE_START_ERROR",
                prefix="Error starting interactive creation",
                exc=exc,
            )

    def handle_edit_interactive(
        self, command: ParsedCommand, ctx: ResolvedContext
    ) -> CommandResult:
        """Handle the interactive identity edit command."""
        if not command.args:
            return CommandResult(ok=False, text="Usage: /identity.edit <agent_id>")

        agent_id = command.args[0]

        try:
            ui = getattr(ctx, "ui", None)
            if not ui:
                return CommandResult(
                    ok=False,
                    text="Interactive wizard requires UI context. Use in a chat interface that supports wizards.",
                )

            wizard_session_id = getattr(ctx, "wizard_session_id", None)
            if wizard_session_id:
                return CommandResult(
                    ok=False,
                    text="Already in a wizard session. Complete or cancel that session before starting a new one.",
                )

            profile = self._get_profile_or_not_found(agent_id)
            if isinstance(profile, CommandResult):
                return profile

            wizard_id = f"identity-edit-{ctx.user_key}-{ctx.session_id}-{agent_id}"
            return CommandResult(
                ok=True,
                text=f"Ready to edit profile for {agent_id}. The interactive wizard would start now in a full implementation.",
                data={"wizard_id": wizard_id},
            )
        except Exception as exc:  # noqa: BLE001
            return self._error_result(
                code="INTERACTIVE_EDIT_START_ERROR",
                prefix="Error starting interactive edit",
                exc=exc,
            )
