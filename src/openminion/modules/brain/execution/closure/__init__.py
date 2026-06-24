"""Closure helpers for brain execution."""

from .base import *  # noqa: F403
from ..runtime.closure.evaluator import (
    _active_plan_at_closure as _runtime_active_plan_at_closure,
)


def _active_plan_at_closure(runner, state):
    return _runtime_active_plan_at_closure(runner, state=state)
