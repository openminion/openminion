# ruff: noqa: F401
import hashlib
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, Optional, Protocol, TYPE_CHECKING, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

if TYPE_CHECKING:  # pragma: no cover - typing-only imports
    from ..runtime.plugin import ToolRuntime

PolicyAction = Literal["allow", "deny", "require_confirm"]
RiskClass = Literal[
    "read", "write", "exec", "state_change", "destructive", "financial", "security"
]
RiskReversibility = Literal[
    "reversible", "partially_reversible", "irreversible", "unknown"
]
