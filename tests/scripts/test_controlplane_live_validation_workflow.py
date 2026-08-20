from pathlib import Path


WORKFLOW = (
    Path(__file__).resolve().parents[2]
    / ".github"
    / "workflows"
    / "controlplane-live-validation.yml"
)


def test_live_validation_workflow_is_scheduled_and_not_a_pr_gate() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in text
    assert 'cron: "0 2 * * *"' in text
    assert "pull_request:" not in text
    assert "push:" not in text


def test_live_validation_workflow_covers_each_external_surface() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "-m telegram_live" in text
    assert "-m slack_live" in text
    assert "-m postgres" in text
    assert "image: postgres:16" in text
    assert "TELEGRAM_BOT_TOKEN" in text
    assert "SLACK_BOT_TOKEN" in text
    assert "OPENMINION_TEST_POSTGRES_URL" in text
