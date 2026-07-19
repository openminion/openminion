from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from typing import Any

from openminion.modules.controlplane.runtime.audit import emit_audit_event

from .store import ControlPlanePairingStore


@dataclass
class PairingMigrationJob:
    legacy_store: Any
    new_store: ControlPlanePairingStore
    audit_logger: object | None = None
    logger: logging.Logger | None = None
    channel: str = "telegram"

    def run_once(self) -> dict[str, int]:
        log = self.logger or logging.getLogger(__name__)
        if self.new_store.has_channel_data(channel=self.channel):
            return {"tokens_copied": 0, "attempts_copied": 0, "skipped": 1}
        if not hasattr(self.legacy_store, "iter_pair_tokens"):
            return {"tokens_copied": 0, "attempts_copied": 0, "skipped": 0}

        try:
            token_rows = list(self.legacy_store.iter_pair_tokens())
            attempt_rows = list(self.legacy_store.iter_pair_attempts())
            tokens_copied = self.new_store.bulk_insert_tokens(token_rows)
            attempts_copied = self.new_store.bulk_insert_attempts(attempt_rows)
        except (
            AttributeError,
            TypeError,
            ValueError,
            RuntimeError,
            sqlite3.Error,
        ) as exc:
            log.warning("pairing migration failed channel=%s: %s", self.channel, exc)
            raise

        if tokens_copied or attempts_copied:
            emit_audit_event(
                self.audit_logger,
                "cp.pairing.migration.completed",
                channel=self.channel,
                tokens_copied=tokens_copied,
                attempts_copied=attempts_copied,
            )
        return {
            "tokens_copied": tokens_copied,
            "attempts_copied": attempts_copied,
            "skipped": 0,
        }
