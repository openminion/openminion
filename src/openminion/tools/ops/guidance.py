"""Built-in guidance for safe ops tool use."""

OPS_GUIDANCE_ID = "ops.safety.v1"

OPS_TOOL_FAMILY_GUIDANCE = (
    "Use ops tools evidence-first. Prefer read-only observations before any "
    "change, do not claim remediation success without observed evidence, ask "
    "before write or destructive operations, and summarize target, evidence, "
    "uncertainty, and next steps."
)
