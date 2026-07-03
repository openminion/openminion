"""Small time helpers shared by goal runtime owners."""

from __future__ import annotations

import time


def goal_now_ms() -> int:
    return int(time.time() * 1000)


__all__ = ["goal_now_ms"]
