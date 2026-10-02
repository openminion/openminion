"""Release workflow event and publication boundaries stay fail-closed."""

from pathlib import Path

import pytest
from scripts.ci.release_event import release_target, source_version


ROOT = Path(__file__).resolve().parents[2]
RELEASE_WORKFLOW = (ROOT / ".github/workflows/release.yml").read_text()
RELEASE_EVENTS = RELEASE_WORKFLOW.split("concurrency:", 1)[0]


def test_release_workflow_runs_only_for_publication_events():
    assert 'tags: ["v*"]' in RELEASE_EVENTS
    assert "workflow_dispatch:" in RELEASE_EVENTS
    assert "pull_request:" not in RELEASE_EVENTS
    assert "branches: [main]" not in RELEASE_EVENTS
    assert "- testpypi" in RELEASE_EVENTS
    assert "- pypi" not in RELEASE_EVENTS
    assert "cancel-in-progress: false" in RELEASE_WORKFLOW


def test_manual_publication_is_testpypi_only_and_requires_main():
    assert "python -m scripts.ci.release_event" in RELEASE_WORKFLOW
    assert "requested-target" in RELEASE_WORKFLOW
    assert "github.event.inputs.target == 'pypi'" not in RELEASE_WORKFLOW
    assert (
        release_target(
            event_name="workflow_dispatch",
            ref="refs/heads/main",
            requested_target="testpypi",
            repository=ROOT,
        )
        == "testpypi"
    )
    for ref in ("refs/heads/dev", "refs/tags/v0.0.29"):
        with pytest.raises(ValueError, match="dispatched from main"):
            release_target(
                event_name="workflow_dispatch",
                ref=ref,
                requested_target="testpypi",
                repository=ROOT,
            )


def test_production_publication_is_final_tag_only():
    publish_pypi = RELEASE_WORKFLOW.split("  publish-pypi:", 1)[1]
    version = source_version(ROOT)
    current = release_target(
        event_name="push",
        ref=f"refs/tags/v{version}",
        requested_target="",
        repository=ROOT,
    )
    assert current == "pypi"
    assert "needs.validate-release-request.outputs.target == 'pypi'" in publish_pypi
    assert "workflow_dispatch" not in publish_pypi


@pytest.mark.parametrize("tag", ["0.0.29rc1", "0.0.29alpha1", "0.0.29beta1"])
def test_supported_prerelease_tags_route_only_to_testpypi(tmp_path, tag):
    version_owner = tmp_path / "src/openminion/base"
    version_owner.mkdir(parents=True)
    (version_owner / "version.py").write_text(f'OPENMINION_VERSION = "{tag}"\n')
    assert (
        release_target(
            event_name="push",
            ref=f"refs/tags/v{tag}",
            requested_target="",
            repository=tmp_path,
        )
        == "testpypi"
    )


@pytest.mark.parametrize("tag", ["0.0.29a1", "0.0.29b1", "0.0.29.dev1"])
def test_unsupported_prerelease_spellings_fail_closed(tmp_path, tag):
    version_owner = tmp_path / "src/openminion/base"
    version_owner.mkdir(parents=True)
    (version_owner / "version.py").write_text(f'OPENMINION_VERSION = "{tag}"\n')
    with pytest.raises(ValueError):
        release_target(
            event_name="push",
            ref=f"refs/tags/v{tag}",
            requested_target="",
            repository=tmp_path,
        )


def test_tag_version_must_match_source_version():
    with pytest.raises(ValueError, match="does not match source version"):
        release_target(
            event_name="push",
            ref="refs/tags/v9.9.9",
            requested_target="",
            repository=ROOT,
        )


def test_both_indexes_keep_full_artifact_verification():
    assert "make workflow-check" in RELEASE_WORKFLOW
    assert "make format-check" in RELEASE_WORKFLOW
    assert "make lint" in RELEASE_WORKFLOW
    assert "make test-ci" in RELEASE_WORKFLOW
    assert "Verify wheel install and bootstrap smoke" in RELEASE_WORKFLOW
    assert "Verify sdist install and bootstrap smoke" in RELEASE_WORKFLOW
    assert (
        RELEASE_WORKFLOW.count(
            "needs: [validate-release-request, build, verify-built-artifacts]"
        )
        == 2
    )


def test_final_tag_still_drives_source_and_binary_observers():
    for workflow_name in (
        "runtime-manifests.yml",
        "runtime-candidate-request.yml",
    ):
        workflow = (ROOT / ".github/workflows" / workflow_name).read_text()
        assert "workflows: [Release]" in workflow
        assert "github.event.workflow_run.conclusion == 'success'" in workflow
        assert "github.event.workflow_run.event == 'push'" in workflow
        assert "startsWith(github.event.workflow_run.head_branch, 'v')" in workflow
        for marker in ("alpha", "beta", "rc"):
            assert (
                f"!contains(github.event.workflow_run.head_branch, '{marker}')"
                in workflow
            )
