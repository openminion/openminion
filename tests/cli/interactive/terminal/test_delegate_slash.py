from __future__ import annotations

from rich.console import Console

from openminion.cli.interactive.terminal.shell.delegation import handle_slash_delegate


def test_terminal_slash_delegate_forwards_approval_callback() -> None:
    calls: list[dict[str, object]] = []
    approval_callback = object()

    class _Runtime:
        def delegate_task(self, **kwargs: object) -> dict[str, object]:
            calls.append(dict(kwargs))
            return {
                "ok": True,
                "mode": kwargs.get("mode"),
                "status": "success",
                "agent_id": kwargs.get("target_agent_id"),
                "content": "delegated",
            }

    console = Console(record=True, force_terminal=False)
    handle_slash_delegate(
        "/delegate worker write file",
        runtime=_Runtime(),
        console=console,
        approval_callback=approval_callback,  # type: ignore[arg-type]
    )

    assert len(calls) == 1
    assert calls[0]["approval_callback"] is approval_callback
    assert calls[0]["target_agent_id"] == "worker"
    assert calls[0]["instruction"] == "write file"
    assert "Delegation:" in console.export_text()
