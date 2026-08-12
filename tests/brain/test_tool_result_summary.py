from openminion.modules.brain.adapters.tool.results import _derive_toolspec_summary


def test_failed_tool_prefers_diagnostic_summary() -> None:
    payload = {
        "status": "error",
        "summary": (
            "Command exited with code 255.\n\nstderr:\n"
            "deploy@example: Permission denied (publickey,password)."
        ),
        "error": {
            "code": "EXEC_ERROR",
            "message": "command exited with code 255",
        },
    }

    summary = _derive_toolspec_summary(
        payload,
        status="error",
        tool_name="exec.run",
    )

    assert "Permission denied" in summary


def test_failed_tool_uses_error_when_summary_is_absent() -> None:
    payload = {
        "status": "error",
        "error": {
            "code": "EXEC_ERROR",
            "message": "command exited with code 255",
        },
    }

    summary = _derive_toolspec_summary(
        payload,
        status="error",
        tool_name="exec.run",
    )

    assert summary == "command exited with code 255"
