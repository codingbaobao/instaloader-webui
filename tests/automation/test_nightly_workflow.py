from __future__ import annotations

import re
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPOSITORY_ROOT / ".github" / "workflows" / "nightly.yml"


def _workflow() -> str:
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def test_nightly_has_the_approved_triggers_and_permissions() -> None:
    workflow = _workflow()

    assert 'cron: "0 18 * * *"' in workflow
    assert "  workflow_dispatch:" in workflow
    assert "permissions:\n  contents: read\n  actions: read" in workflow


def test_nightly_queues_all_concurrent_publications() -> None:
    workflow = _workflow()

    assert "group: nightly-${{ github.repository }}" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "queue: max" in workflow


def test_nightly_checks_and_builds_the_exact_triggered_sha() -> None:
    workflow = _workflow()

    assert "run: bash .github/scripts/nightly-preflight.sh" in workflow
    assert "ref: ${{ github.sha }}" in workflow
    assert "ref: ${{ needs.preflight.outputs.source_sha }}" in workflow


def test_nightly_uses_only_immutable_action_revisions() -> None:
    action_revisions = re.findall(r"^\s+uses: [^\s@]+@([^\s]+)", _workflow(), re.MULTILINE)

    assert action_revisions
    assert all(re.fullmatch(r"[0-9a-f]{40}", revision) for revision in action_revisions)


def test_nightly_publishes_only_the_multi_platform_nightly_tag() -> None:
    workflow = _workflow()

    assert "platforms: linux/amd64,linux/arm64" in workflow
    assert workflow.count("type=raw,value=nightly") == 1
    assert "type=semver" not in workflow
    assert "value=latest" not in workflow
    assert "push: true" in workflow
    assert "sbom: true" in workflow
    assert "provenance: mode=max" in workflow
