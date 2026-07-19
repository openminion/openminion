"""Lifecycle operator prompt strings."""

PINCHTAB_AUTOSTART_PROMPT = (
    "OpenMinion can start the PinchTab browser service locally when needed.\n"
    "This launches a background process on your machine.\n"
    "Allow auto-start for PinchTab? [y/N]: "
)

SIDECAR_POLICY_PROMPT_TEMPLATE = (
    "Sidecar action requires approval.\nAllow {verb} for sidecar '{name}'? [y/N]: "
)


def build_sidecar_policy_prompt(*, verb: str, name: str) -> str:
    """Render the sidecar approval prompt for an operator."""

    return SIDECAR_POLICY_PROMPT_TEMPLATE.format(verb=verb, name=name)


__all__ = [
    "PINCHTAB_AUTOSTART_PROMPT",
    "SIDECAR_POLICY_PROMPT_TEMPLATE",
    "build_sidecar_policy_prompt",
]
