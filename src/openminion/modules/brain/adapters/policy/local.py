from openminion.modules.brain.constants import BRAIN_COMMAND_KIND_TOOL
from openminion.modules.brain.interfaces import BRAIN_ADAPTER_INTERFACE_VERSION
from openminion.modules.brain.schemas import Command, PolicyDecision, WorkingState


class LocalPolicyAdapter:
    contract_version = BRAIN_ADAPTER_INTERFACE_VERSION

    def evaluate(
        self,
        *,
        command: Command,
        working_state: WorkingState,
        session_context: dict[str, object],
    ) -> PolicyDecision:
        del working_state, session_context
        if command.risk_level == "high":
            return PolicyDecision(
                outcome="REQUIRE_CONFIRMATION",
                explanation="High-risk command requires user confirmation.",
            )
        if command.kind == BRAIN_COMMAND_KIND_TOOL and getattr(
            command, "tool_name", ""
        ) in {"rm", "shutdown"}:
            return PolicyDecision(
                outcome="DENY", explanation="Dangerous tool is denied by local policy."
            )
        if command.kind == BRAIN_COMMAND_KIND_TOOL and getattr(command, "cwd", None):
            cwd = str(command.cwd)
            if ".." in cwd:
                patched = command.model_copy(update={"cwd": cwd.replace("..", "")})
                return PolicyDecision(
                    outcome="MODIFY",
                    explanation="Path traversal clamped in cwd.",
                    patched_command=patched,
                )
        return PolicyDecision(outcome="ALLOW", explanation="Allowed by local policy.")
