from __future__ import annotations

import os
import subprocess
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PREFLIGHT_SCRIPT = REPOSITORY_ROOT / ".github" / "scripts" / "nightly-preflight.sh"
SOURCE_SHA = "0123456789abcdef0123456789abcdef01234567"
OTHER_SHA = "89abcdef0123456789abcdef0123456789abcdef"


def _mock_gh(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    executable = bin_dir / "gh"
    executable.write_text(
        """#!/usr/bin/env bash
set -eu
case "$*" in
  *nightly.yml*) printf '%s\\n' "${MOCK_NIGHTLY_SHA:-}" ;;
  *ci.yml*) printf '%s\\n' "${MOCK_CI_SHA:-}" ;;
  *) printf 'unexpected gh invocation: %s\\n' "$*" >&2; exit 64 ;;
esac
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return bin_dir


def _run_preflight(
    tmp_path: Path,
    *,
    event: str,
    source_ref: str = "refs/heads/main",
    source_sha: str = SOURCE_SHA,
    nightly_sha: str = OTHER_SHA,
    ci_sha: str = SOURCE_SHA,
) -> tuple[subprocess.CompletedProcess[str], dict[str, str]]:
    output_path = tmp_path / "github-output"
    env = os.environ.copy()
    env.update(
        {
            "EVENT_NAME": event,
            "SOURCE_REF": source_ref,
            "SOURCE_SHA": source_sha,
            "REPOSITORY": "codingbaobao/instaloader-webui",
            "GITHUB_OUTPUT": str(output_path),
            "GH_TOKEN": "test-token",
            "MOCK_NIGHTLY_SHA": nightly_sha,
            "MOCK_CI_SHA": ci_sha,
        }
    )
    env["PATH"] = f"{_mock_gh(tmp_path)}{os.pathsep}{env['PATH']}"
    result = subprocess.run(
        ["bash", str(PREFLIGHT_SCRIPT)],
        cwd=REPOSITORY_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    outputs: dict[str, str] = {}
    if output_path.exists():
        for line in output_path.read_text(encoding="utf-8").splitlines():
            name, value = line.split("=", maxsplit=1)
            outputs[name] = value
    return result, outputs


def test_non_main_ref_is_rejected_before_publication(tmp_path: Path) -> None:
    result, outputs = _run_preflight(
        tmp_path,
        event="workflow_dispatch",
        source_ref="refs/heads/feature/test",
    )

    assert result.returncode != 0
    assert outputs == {}
    assert "only be published from main" in result.stderr


def test_scheduled_run_skips_the_last_successful_nightly(tmp_path: Path) -> None:
    result, outputs = _run_preflight(
        tmp_path,
        event="schedule",
        nightly_sha=SOURCE_SHA,
        ci_sha=OTHER_SHA,
    )

    assert result.returncode == 0
    assert outputs == {"source_sha": SOURCE_SHA, "publish": "false"}
    assert "No new commits" in result.stdout


def test_scheduled_run_requires_ci_for_the_exact_source_sha(tmp_path: Path) -> None:
    result, outputs = _run_preflight(
        tmp_path,
        event="schedule",
        nightly_sha=OTHER_SHA,
        ci_sha=OTHER_SHA,
    )

    assert result.returncode != 0
    assert outputs == {"source_sha": SOURCE_SHA}
    assert "does not have a successful CI run" in result.stderr


def test_scheduled_run_publishes_a_new_ci_approved_commit(tmp_path: Path) -> None:
    result, outputs = _run_preflight(
        tmp_path,
        event="schedule",
        nightly_sha=OTHER_SHA,
        ci_sha=SOURCE_SHA,
    )

    assert result.returncode == 0
    assert outputs == {"source_sha": SOURCE_SHA, "publish": "true"}


def test_manual_run_rebuilds_the_same_ci_approved_commit(tmp_path: Path) -> None:
    result, outputs = _run_preflight(
        tmp_path,
        event="workflow_dispatch",
        nightly_sha=SOURCE_SHA,
        ci_sha=SOURCE_SHA,
    )

    assert result.returncode == 0
    assert outputs == {"source_sha": SOURCE_SHA, "publish": "true"}
