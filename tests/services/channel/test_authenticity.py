import hmac
from hashlib import sha256
import os
import unittest
from unittest.mock import patch

from openminion.base.config import ChannelAuthenticityConfig
from openminion.services.channel.authenticity import (
    MODE_REQUIRE,
    MODE_WARN,
    ChannelAuthenticityEvidence,
    build_channel_authenticity_policy,
    evaluate_inbound_authenticity,
)


class ChannelAuthenticityTests(unittest.TestCase):
    def test_trusted_channel_is_allowed_without_signature(self) -> None:
        policy = build_channel_authenticity_policy(ChannelAuthenticityConfig())
        decision = evaluate_inbound_authenticity(
            policy=policy,
            evidence=ChannelAuthenticityEvidence(
                channel="console",
                target="user",
                body="hello",
                metadata={},
            ),
        )
        self.assertTrue(decision.allowed)
        self.assertTrue(decision.verified)
        self.assertEqual(decision.reason_code, "trusted_channel")

    def test_require_mode_denies_missing_signature(self) -> None:
        policy = build_channel_authenticity_policy(
            ChannelAuthenticityConfig(
                mode=MODE_REQUIRE,
                trusted_channels=["console"],
                required_channels=["telegram"],
                secret_env_by_channel={"telegram": "TEST_TELEGRAM_SECRET"},
            )
        )
        decision = evaluate_inbound_authenticity(
            policy=policy,
            evidence=ChannelAuthenticityEvidence(
                channel="telegram",
                target="group-1",
                body="hello",
                metadata={},
            ),
        )
        self.assertFalse(decision.allowed)
        self.assertFalse(decision.verified)
        self.assertEqual(decision.reason_code, "missing_signature")

    def test_warn_mode_allows_missing_signature_with_warning(self) -> None:
        policy = build_channel_authenticity_policy(
            ChannelAuthenticityConfig(
                mode=MODE_WARN,
                trusted_channels=["console"],
                required_channels=[],
            )
        )
        decision = evaluate_inbound_authenticity(
            policy=policy,
            evidence=ChannelAuthenticityEvidence(
                channel="telegram",
                target="group-1",
                body="hello",
                metadata={},
            ),
        )
        self.assertTrue(decision.allowed)
        self.assertFalse(decision.verified)
        self.assertTrue(decision.warning)
        self.assertEqual(decision.reason_code, "warn_missing_signature")

    @patch.dict(os.environ, {"TEST_TELEGRAM_SECRET": "secret-123"}, clear=False)
    def test_require_mode_allows_valid_hmac_signature(self) -> None:
        body = "hello"
        signature = hmac.new(b"secret-123", body.encode("utf-8"), sha256).hexdigest()
        policy = build_channel_authenticity_policy(
            ChannelAuthenticityConfig(
                mode=MODE_REQUIRE,
                trusted_channels=["console"],
                required_channels=["telegram"],
                secret_env_by_channel={"telegram": "TEST_TELEGRAM_SECRET"},
                max_age_seconds=300,
                allowed_algorithms=["hmac-sha256"],
            )
        )
        decision = evaluate_inbound_authenticity(
            policy=policy,
            evidence=ChannelAuthenticityEvidence(
                channel="telegram",
                target="group-1",
                body=body,
                metadata={
                    "auth_signature": signature,
                    "auth_signature_alg": "hmac-sha256",
                    "auth_signature_ts": "1700000000",
                },
            ),
            now_epoch_seconds=1700000001,
        )
        self.assertTrue(decision.allowed)
        self.assertTrue(decision.verified)
        self.assertEqual(decision.reason_code, "verified_signature")
