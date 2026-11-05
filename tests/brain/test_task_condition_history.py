"""Self-contained reference context for condition amendments and reaffirmations."""

import pytest

from openminion.modules.brain.loop.context.task_context import (
    input_digest,
    updated_task_context,
)
from tests.brain.test_resolved_task_context import delta


def test_anaphoric_reaffirmation_keeps_its_rule_after_twenty_more_updates():
    from openminion.modules.brain.loop.context.task_context import task_context_message

    first = "Process BATCH-ALPHA, excluding archived rows. Write JSON."
    task = updated_task_context(
        None,
        delta(
            0,
            first,
            operation="start",
            objective="Process BATCH-ALPHA",
            conditions=[
                ("exclude_archived", "excluding archived rows"),
                ("format", "Write JSON"),
            ],
        ),
        user_input=first,
    )
    latest = "Use BATCH-BETA. Keep the exclusion rule."
    task = updated_task_context(
        task,
        delta(
            1,
            latest,
            objective="Process BATCH-BETA",
            conditions=[("exclude_archived", "Keep the exclusion rule")],
        ),
        user_input=latest,
    )
    for i in range(20):
        text = f"field_{i}=VALUE_{i}"
        task = updated_task_context(
            task,
            delta(task.revision, text, conditions=[(f"field_{i}", text)]),
            user_input=text,
        )
    evidence = task.condition_history["exclude_archived"]
    assert len(evidence) == 1 and evidence[0].quote == "excluding archived rows"
    assert evidence[0].source == input_digest(first)
    text = task_context_message(task).content
    assert "excluding archived rows" in text and "Keep the exclusion rule" in text
    assert "latest explicit conditions supersede prior values" in text


def test_condition_history_keeps_answer_context_and_follows_its_owner():
    first = "Draw a rectangle."
    task = updated_task_context(
        None, delta(0, first, operation="start", objective=first), user_input=first
    )
    update = delta(1, "yes", conditions=[("width", "yes")])
    update["set_conditions"][0]["question_quote"] = "Use width 120?"
    task = updated_task_context(
        task, update, user_input="yes", clarification_question="Use width 120?"
    )
    latest = "Keep the agreed width."
    task = updated_task_context(
        task, delta(2, latest, conditions=[("width", latest)]), user_input=latest
    )
    assert task.condition_history["width"][0].question_quote == "Use width 120?"
    removed = updated_task_context(
        task, delta(3, "Remove width", remove=["width"]), user_input="Remove width"
    )
    assert not removed.condition_history and not removed.condition_questions
    replaced = updated_task_context(
        task,
        delta(3, "New task", operation="replace", objective="New task"),
        user_input="New task",
    )
    assert not replaced.condition_history and not replaced.conditions


def test_condition_history_overflow_rejects_without_mutating_prior_state():
    first = "x" * 1000
    task = updated_task_context(
        None,
        delta(
            0,
            first,
            operation="start",
            objective="Compare",
            conditions=[("condition", first)],
        ),
        user_input=first,
    )
    for i in range(20):
        text = str(i) + "x" * 999
        before = task.model_dump()
        try:
            task = updated_task_context(
                task,
                delta(task.revision, text, conditions=[("condition", text)]),
                user_input=text,
            )
        except ValueError:
            assert task.model_dump() == before
            assert task.condition_history
            return
    pytest.fail("Condition history exceeded bounded capacity without rejection")
