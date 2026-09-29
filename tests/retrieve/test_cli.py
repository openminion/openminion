from __future__ import annotations

from argparse import ArgumentParser
from types import SimpleNamespace
from unittest.mock import Mock

from openminion.modules.retrieve import cli


def test_ingest_text_forwards_explicit_scope_key(capsys) -> None:
    parser = ArgumentParser()
    service = Mock()
    service.ingest_source.return_value = SimpleNamespace(
        model_dump=lambda **_kwargs: {"doc_id": "doc-1"}
    )
    args = cli._build_parser().parse_args(
        [
            "ingest-text",
            "--source-type",
            "doc",
            "--source-ref",
            "doc://alpha",
            "--text",
            "alpha notes",
            "--scope",
            "project",
            "--scope-key",
            "project:alpha",
        ]
    )

    assert cli._run_service_command(parser=parser, service=service, args=args) == 0
    assert service.ingest_source.call_args.kwargs["scope_key"] == "project:alpha"
    assert '"doc_id": "doc-1"' in capsys.readouterr().out
