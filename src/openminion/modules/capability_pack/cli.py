from __future__ import annotations

import json

import typer

from .api import activate_registered_pack, inspect_pack, list_packs, smoke_pack
from .evaluation import CapabilityScenario
from .fixtures import business_support_manifest
from .registry import CapabilityPackRegistry

app = typer.Typer(add_completion=False, no_args_is_help=True)


def _registry() -> CapabilityPackRegistry:
    registry = CapabilityPackRegistry()
    registry.register(business_support_manifest())
    return registry


@app.command("list")
def list_command() -> None:
    typer.echo(
        json.dumps(
            [item.model_dump(mode="json") for item in list_packs(_registry())],
            sort_keys=True,
        )
    )


@app.command("inspect")
def inspect_command(pack_id: str) -> None:
    typer.echo(inspect_pack(_registry(), pack_id).model_dump_json(by_alias=True))


@app.command("activate")
def activate_command(pack_id: str, session_id: str = "cli") -> None:
    manifest = inspect_pack(_registry(), pack_id)
    active = activate_registered_pack(
        _registry(),
        pack_id=pack_id,
        session_id=session_id,
        available_tools=(
            *(tool.tool_id for tool in manifest.tools),
            *manifest.baseline_tools,
        ),
        available_skills=(skill.skill_id for skill in manifest.skills),
    )
    typer.echo(active.model_dump_json())


@app.command("smoke")
def smoke_command(pack_id: str) -> None:
    manifest = inspect_pack(_registry(), pack_id)
    results = smoke_pack(
        manifest,
        (
            CapabilityScenario(
                scenario_id="read-allowed",
                verb="read",
                capability_scope="support.customer.read",
                expected_decision="allow",
            ),
            CapabilityScenario(
                scenario_id="send-requires-approval",
                verb="external_send",
                capability_scope="support.reply.send",
                expected_decision="ask",
            ),
            CapabilityScenario(
                scenario_id="money-movement-requires-approval",
                verb="money_movement",
                capability_scope="support.refund.execute",
                expected_decision="ask",
            ),
        ),
    )
    typer.echo(json.dumps([item.model_dump(mode="json") for item in results]))


if __name__ == "__main__":
    app()
