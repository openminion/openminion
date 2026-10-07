from __future__ import annotations

from copy import deepcopy
import json
import re

import pytest

from tests.e2e.runners.run_commerce_local import SCENARIO_IDS, _run

pytestmark = pytest.mark.e2e

_HEX_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_MUTATION_SCENARIOS = {
    "prepare_denied_then_approved",
    "place_separate_approval",
    "crash_recovery_without_resubmit",
    "approved_cancellation",
    "partial_return_and_refund",
}
_SENSITIVE_MARKERS = (
    "fixture-provider-secret",
    "fixture-payment-token",
    "fixture buyer",
    "home ending 42",
    "access_token=",
    "bearer ",
)


def validate_commerce_evidence(payload: object) -> None:
    if not isinstance(payload, dict):
        raise ValueError("commerce evidence must be a structured object")
    if payload.get("schema_version") != "commerce-local-evidence-v1":
        raise ValueError("commerce evidence schema is missing")
    if not _HEX_COMMIT.fullmatch(str(payload.get("source_commit", ""))):
        raise ValueError("source commit is missing")
    if payload.get("subject_id") != "local":
        raise ValueError("trusted subject identity is missing")
    inventories = payload.get("inventories")
    if not isinstance(inventories, dict) or {
        key: len(value) if isinstance(value, list) else -1
        for key, value in inventories.items()
    } != {"0": 0, "2": 2, "3": 3, "5": 5}:
        raise ValueError("commerce inventory phases are wrong")

    scenarios = payload.get("scenarios")
    if (
        not isinstance(scenarios, list)
        or tuple(
            item.get("scenario_id") for item in scenarios if isinstance(item, dict)
        )
        != SCENARIO_IDS
    ):
        raise ValueError("complete commerce scenario matrix is missing")
    if payload.get("scenario_count") != len(SCENARIO_IDS):
        raise ValueError("scenario count does not match the matrix")
    coverage = payload.get("scenario_coverage")
    if not isinstance(coverage, dict) or set(coverage) != set(SCENARIO_IDS):
        raise ValueError("scenario coverage mapping is missing")
    case_ids = [
        case_id
        for scenario_id in SCENARIO_IDS
        for case_id in coverage.get(scenario_id, ())
    ]
    expected_case_ids = {f"COLR-S{index:02d}" for index in range(1, 21)}
    if len(case_ids) != 20 or set(case_ids) != expected_case_ids:
        raise ValueError("scenario coverage must map COLR-S01 through COLR-S20 once")
    for scenario in scenarios:
        if not isinstance(scenario, dict) or scenario.get("status") != "passed":
            raise ValueError("prose-only or failed scenario evidence is not accepted")
        if scenario.get("subject_id") != "local":
            raise ValueError("scenario subject identity is missing")
        scenario_id = str(scenario["scenario_id"])
        if scenario_id in _MUTATION_SCENARIOS:
            policy = scenario.get("policy")
            if not isinstance(policy, dict) or any(
                not str(policy.get(key, ""))
                for key in (
                    "approval_id",
                    "grant_id",
                    "invocation_id",
                    "invocation_hash",
                )
            ):
                raise ValueError("mutation policy correlation is missing")
            if (
                not str(scenario.get("attempt_id", ""))
                or scenario.get("attempt_id") == "none"
            ):
                raise ValueError("mutation attempt identity is missing")
            if scenario_id != "prepare_denied_then_approved" and (
                not str(scenario.get("order_id", ""))
                or scenario.get("order_id") == "none"
            ):
                raise ValueError("mutation order identity is missing")
            if int(scenario.get("fixture_ledger_after", -1)) <= int(
                scenario.get("fixture_ledger_before", -1)
            ):
                raise ValueError("mutation lacks fixture truth")

    ledger = payload.get("fixture_ledger")
    if not isinstance(ledger, list) or not ledger:
        raise ValueError("fixture ledger truth is missing")
    identities = [
        (
            item.get("fixture_id"),
            item.get("operation"),
            item.get("idempotency_key"),
        )
        for item in ledger
        if isinstance(item, dict)
    ]
    if len(identities) != len(ledger) or len(set(identities)) != len(identities):
        raise ValueError("duplicate provider call found")
    ledger_keys = {item[2] for item in identities}
    attempt_records = payload.get("attempt_records")
    if not isinstance(attempt_records, list):
        raise ValueError("attempt records are missing")
    attempts = {
        str(item.get("attempt_id")): item
        for item in attempt_records
        if isinstance(item, dict)
    }
    for scenario in scenarios:
        if scenario["scenario_id"] not in _MUTATION_SCENARIOS:
            continue
        attempt = attempts.get(str(scenario.get("attempt_id")))
        if (
            attempt is None
            or attempt.get("idempotency_key") != scenario.get("idempotency_key")
            or scenario.get("idempotency_key") not in ledger_keys
        ):
            raise ValueError("mutation identities do not join to durable fixture truth")
        policy = scenario["policy"]
        if attempt.get("kind") != "preparation" and (
            attempt.get("authorization_hash") != policy.get("invocation_hash")
        ):
            raise ValueError("authorization does not join to the durable attempt")

    recovery = next(
        item
        for item in scenarios
        if item["scenario_id"] == "crash_recovery_without_resubmit"
    )
    if recovery.get("facts", {}).get("provider_place_calls") != 1:
        raise ValueError("recovery resubmitted the provider request")
    cursor = payload.get("notification_cursor")
    if not isinstance(cursor, dict) or cursor.get("notifications") != 2:
        raise ValueError("notification cursor evidence is missing")
    if cursor.get("before") == cursor.get("after"):
        raise ValueError("notification cursor did not advance")
    tracking = next(
        item
        for item in scenarios
        if item["scenario_id"] == "tracking_cursor_deduplication"
    )
    if tracking.get("facts", {}).get("cursor_after") != cursor.get("after"):
        raise ValueError("notification cursor does not join to tracking truth")
    if tracking.get("facts", {}).get("delivery_requests") != [True, False, True]:
        raise ValueError("notification deduplication evidence is missing")
    partial = next(
        item for item in scenarios if item["scenario_id"] == "partial_return_and_refund"
    )
    if partial.get("facts", {}).get("refund_states") != ["pending", "completed"]:
        raise ValueError("refund lifecycle evidence is incomplete")
    if partial.get("facts", {}).get("pending_collision_rejected") is not True:
        raise ValueError("pending action collision evidence is missing")
    access = next(
        item
        for item in scenarios
        if item["scenario_id"] == "cross_subject_and_channel_rejection"
    )
    access_facts = access.get("facts", {})
    if (
        access_facts.get("cross_subject_code") != "SUBJECT_UNAVAILABLE"
        or access_facts.get("channel_ingress_code") != "POLICY_MODE_UNSUPPORTED"
        or access_facts.get("provider_calls_unchanged") is not True
    ):
        raise ValueError("subject and channel boundary evidence is missing")
    credential_events = payload.get("credential_access_events")
    if not isinstance(credential_events, list) or {
        event.get("credential_id")
        for event in credential_events
        if isinstance(event, dict)
    } != {"provider-secret", "buyer-profile", "payment-token"}:
        raise ValueError("credential access evidence is missing")
    if any(
        event.get("access_site") != "tools.commerce.place_order"
        or event.get("decision") != "allowed"
        for event in credential_events
        if isinstance(event, dict)
    ):
        raise ValueError("credential access evidence is malformed")
    boundary = next(
        item
        for item in scenarios
        if item["scenario_id"] == "copied_preparation_and_merchant_mismatch"
    )
    if boundary.get("facts", {}).get("copied_rejected") is not True or (
        boundary.get("facts", {}).get("merchant_mismatch_rejected") is not True
    ):
        raise ValueError("merchant and copied-preparation rejection are missing")
    label = next(
        item
        for item in scenarios
        if item["scenario_id"] == "return_label_and_signed_link_redaction"
    )
    if label.get("facts", {}).get("artifact_fields") != []:
        raise ValueError("handoff leaked label or artifact fields")
    encoded = json.dumps(payload, sort_keys=True).casefold()
    if any(marker in encoded for marker in _SENSITIVE_MARKERS):
        raise ValueError("commerce evidence contains a sensitive marker")


@pytest.fixture(scope="module")
def evidence() -> dict[str, object]:
    return _run()


def test_local_commerce_evidence_is_structured_and_joined(evidence) -> None:
    validate_commerce_evidence(evidence)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: "all scenarios passed",
        lambda value: {**value, "subject_id": ""},
        lambda value: {
            **value,
            "scenarios": [
                value["scenarios"][0],
                {**value["scenarios"][1], "policy": {}},
                *value["scenarios"][2:],
            ],
        },
        lambda value: {
            **value,
            "scenarios": [
                *value["scenarios"][:1],
                {**value["scenarios"][1], "attempt_id": "none"},
                *value["scenarios"][2:],
            ],
        },
        lambda value: {
            **value,
            "fixture_ledger": [
                *value["fixture_ledger"],
                deepcopy(value["fixture_ledger"][0]),
            ],
        },
        lambda value: {
            **value,
            "attempt_records": [
                {**value["attempt_records"][0], "idempotency_key": "wrong"},
                *value["attempt_records"][1:],
            ],
        },
        lambda value: {
            **value,
            "notification_cursor": {
                **value["notification_cursor"],
                "after": value["notification_cursor"]["before"],
            },
        },
        lambda value: {
            **value,
            "scenarios": [
                *value["scenarios"][:5],
                {
                    **value["scenarios"][5],
                    "facts": {"provider_place_calls": 2},
                },
                *value["scenarios"][6:],
            ],
        },
        lambda value: {
            **value,
            "scenario_coverage": {
                key: covered
                for key, covered in value["scenario_coverage"].items()
                if key != SCENARIO_IDS[0]
            },
        },
        lambda value: {
            **value,
            "scenario_coverage": {
                **value["scenario_coverage"],
                SCENARIO_IDS[0]: (
                    *value["scenario_coverage"][SCENARIO_IDS[0]],
                    "COLR-S02",
                ),
            },
        },
        lambda value: {**value, "credential_access_events": []},
    ],
)
def test_validator_rejects_unproved_or_unsafe_evidence(evidence, mutate) -> None:
    with pytest.raises(ValueError):
        validate_commerce_evidence(mutate(deepcopy(evidence)))
