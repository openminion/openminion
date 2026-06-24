"""Compatibility re-export shim for durable-memory trust primitives."""

from sophiagraph.trust import types as _sophiagraph_trust_types

from openminion.modules.memory.errors import (
    InvalidArgumentError as _MemoryInvalidArgumentError,
)

from sophiagraph.trust.types import *  # noqa: F401,F403

# Keep the sophiagraph-owned trust contracts as the public owner while aligning
# validation errors with the local memory error taxonomy expected by callers.
_sophiagraph_trust_types.InvalidArgumentError = _MemoryInvalidArgumentError
