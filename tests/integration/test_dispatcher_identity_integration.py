from __future__ import annotations

import pytest

from openminion.modules.controlplane.contracts.models import (
    InboundMessage,
    ParsedCommand,
)
from openminion.modules.controlplane.constants import DEFAULT_MINIMAL_SCOPES
from openminion.modules.controlplane.runtime.identity import StoreBackedIdentityAPI
from openminion.modules.controlplane.runtime.security import ScopeAuthorizer
from openminion.modules.controlplane.storage.sqlite import SQLiteControlPlaneStore


@pytest.mark.parametrize(
    ("channel", "subject_id", "inbound"),
    [
        (
            "telegram",
            "7105273251",
            InboundMessage(
                channel="telegram",
                user_key="telegram:user:7105273251",
                chat_key="7105273251",
                user_id="7105273251",
                chat_id="7105273251",
                text="/status",
            ),
        ),
        (
            "slack",
            "slack:T1:channel:C1",
            InboundMessage(
                channel="slack",
                user_key="slack:T1:user:U1",
                chat_key="slack:T1:channel:C1",
                user_id="U1",
                chat_id="slack:T1:channel:C1",
                text="/status",
            ),
        ),
    ],
)
def test_scope_authorizer_consults_store_backed_identity_api_for_channels(
    tmp_path,
    channel: str,
    subject_id: str,
    inbound: InboundMessage,
) -> None:
    store = SQLiteControlPlaneStore(tmp_path / "cp.db")
    try:
        principal_id = store.upsert_principal(principal_id=f"principal:{channel}")
        identity = StoreBackedIdentityAPI(store)
        identity.bind(
            principal_id=principal_id,
            channel=channel,
            subject_id=subject_id,
            scopes=list(DEFAULT_MINIMAL_SCOPES),
            meta={"source": "integration-test"},
        )
        authorizer = ScopeAuthorizer(store=None, identity_api=identity)

        auth = authorizer.auth_for_inbound(inbound)
        allowed, reason = authorizer.command_allowed(
            ParsedCommand(canonical="status", original_text="/status", args=[]),
            auth,
        )

        assert auth.role == "paired"
        assert auth.principal_id == principal_id
        assert auth.metadata["principal_binding"]["channel"] == channel
        assert allowed is True
        assert reason == "ok"
    finally:
        store.close()


def test_scope_authorizer_fails_closed_for_missing_and_inactive_identity(
    tmp_path,
) -> None:
    store = SQLiteControlPlaneStore(tmp_path / "cp.db")
    try:
        identity = StoreBackedIdentityAPI(store)
        inactive_principal = store.upsert_principal(principal_id="principal:inactive")
        identity.bind(
            principal_id=inactive_principal,
            channel="telegram",
            subject_id="inactive-chat",
            status="inactive",
        )
        authorizer = ScopeAuthorizer(store=None, identity_api=identity)

        missing_auth = authorizer.auth_for_inbound(
            InboundMessage(
                channel="telegram",
                chat_id="missing-chat",
                chat_key="missing-chat",
                user_key="telegram:user:missing",
                text="/status",
            )
        )
        inactive_auth = authorizer.auth_for_inbound(
            InboundMessage(
                channel="telegram",
                chat_id="inactive-chat",
                chat_key="inactive-chat",
                user_key="telegram:user:inactive",
                text="/status",
            )
        )

        assert missing_auth.role == "unpaired"
        assert inactive_auth.role == "unpaired"
    finally:
        store.close()
