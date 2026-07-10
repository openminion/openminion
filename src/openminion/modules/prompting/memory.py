"""Shared memory and session-summary prompt fragments."""

from .context_blocks import CURRENT_SESSION_SUMMARY_HEADER, PRIOR_SESSION_SUMMARY_HEADER

CURRENT_SESSION_CALLBACK_CONTEXT_LABEL = "Current session callback context:"
PRIOR_SESSION_CONTEXT_LABEL = "Most relevant prior session:"

__all__ = [
    "CURRENT_SESSION_CALLBACK_CONTEXT_LABEL",
    "CURRENT_SESSION_SUMMARY_HEADER",
    "PRIOR_SESSION_CONTEXT_LABEL",
    "PRIOR_SESSION_SUMMARY_HEADER",
]
