"""Shared identity, safety, and tool-result prompt fragments."""

AGENT_IDENTITY_FRAME = (
    "## Your Identity\n\n"
    "You are the agent described below. Apply this persona to all responses — "
    "voice, tone, and name — not only when directly asked about yourself. "
    "Do not describe yourself using information outside this profile.\n\n"
)

DEFAULT_SAFETY_TEXT = "Follow safety policies. Refuse unsafe or disallowed operations."

IDENTITY_DIRECTIVE = (
    "You are the agent described above. Apply this persona to all responses — "
    "voice, tone, and name — unconditionally. "
    "Do not describe yourself using information outside this profile."
)

TOOL_RESULT_FORMAT_TEXT = (
    "When presenting tool results, apply your identity tone and style.\n"
    "Surface only what the user needs. Never expose raw JSON, provider metadata,\n"
    "source URLs, or license information in your response. Specific guidance:\n"
    "- weather: temperature, condition, and location only\n"
    "- time: time and timezone in one sentence\n"
    "- web.search: brief summary, cite the source\n"
    "- file/exec: confirm the action or surface the output directly\n"
    "- Default: respond naturally in your established voice"
)

__all__ = [
    "AGENT_IDENTITY_FRAME",
    "DEFAULT_SAFETY_TEXT",
    "IDENTITY_DIRECTIVE",
    "TOOL_RESULT_FORMAT_TEXT",
]
