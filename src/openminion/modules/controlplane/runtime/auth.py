from dataclasses import dataclass

_ADMIN_COMMANDS: frozenset[str] = frozenset(
    {
        "artifact.purge",
        "memory.promote",
        "config.set",
        "approve",
        "deny",
    }
)


@dataclass
class AuthEvaluator:
    admin_user_keys: list[str]

    def role_for(self, user_key: str) -> str:
        return "admin" if user_key in self.admin_user_keys else "user"

    def is_admin(self, user_key: str) -> bool:
        return self.role_for(user_key) == "admin"

    def is_admin_command(self, command_canonical: str) -> bool:
        return command_canonical in _ADMIN_COMMANDS

    def check(self, user_key: str, command_canonical: str) -> tuple[bool, str]:
        if self.is_admin_command(command_canonical):
            if not self.is_admin(user_key):
                return False, f"command '{command_canonical}' requires admin role"
        return True, "ok"
