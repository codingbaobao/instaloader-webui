# Nightly Docker Image Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish a CI-gated, multi-platform `nightly` Docker image from `main` on a daily schedule or manual request.

**Architecture:** A dedicated workflow separates nightly credentials and triggers from stable releases. A small Bash preflight script queries GitHub workflow history, and Python behavior tests mock those API responses before the workflow is allowed to publish.

**Tech Stack:** GitHub Actions YAML, Bash, GitHub CLI, Docker Buildx, Pytest

## Global Constraints

- Only `refs/heads/main` may publish.
- Scheduled execution is `0 18 * * *`, equivalent to 02:00 Asia/Taipei.
- Scheduled runs skip an already-published commit; manual runs rebuild it.
- The exact source SHA must have a successful `ci.yml` push run on `main`.
- Publish only `z21012101/instaloader-webui:nightly` for `linux/amd64` and `linux/arm64`.
- Stable release tags and `.github/workflows/release.yml` remain unchanged.
- External actions use immutable full commit SHAs.

---

### Task 1: Preflight Eligibility Script

**Files:**
- Create: `.github/scripts/nightly-preflight.sh`
- Test: `tests/automation/test_nightly_preflight.py`

**Interfaces:**
- Consumes: `EVENT_NAME`, `SOURCE_REF`, `SOURCE_SHA`, `REPOSITORY`, `GITHUB_OUTPUT`, and `GH_TOKEN` environment variables.
- Produces: `publish=true|false` and `source_sha=<40-character SHA>` in `GITHUB_OUTPUT`.

- [ ] **Step 1: Write failing behavior tests**

Create a temporary mock `gh` executable and assert these cases:

```python
def test_scheduled_run_skips_the_last_successful_nightly(...):
    result, outputs = run_preflight(
        event="schedule",
        source_sha=SHA,
        nightly_sha=SHA,
        ci_sha="different",
    )
    assert result.returncode == 0
    assert outputs["publish"] == "false"


def test_manual_run_rebuilds_when_ci_matches(...):
    result, outputs = run_preflight(
        event="workflow_dispatch",
        source_sha=SHA,
        nightly_sha=SHA,
        ci_sha=SHA,
    )
    assert result.returncode == 0
    assert outputs == {"source_sha": SHA, "publish": "true"}
```

Also assert non-`main` rejection and CI mismatch failure.

- [ ] **Step 2: Run tests and confirm the script is missing**

Run:

```bash
.venv/bin/python -m pytest --no-cov tests/automation/test_nightly_preflight.py -q
```

Expected: FAIL because `.github/scripts/nightly-preflight.sh` does not exist.

- [ ] **Step 3: Implement the preflight script**

Use strict Bash and GitHub workflow-run endpoints:

```bash
#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${SOURCE_REF}" != "refs/heads/main" ]]; then
  echo "::error::Nightly images may only be published from main."
  exit 1
fi

printf 'source_sha=%s\n' "${SOURCE_SHA}" >> "${GITHUB_OUTPUT}"

if [[ "${EVENT_NAME}" == "schedule" ]]; then
  previous_sha="$(gh api \
    "/repos/${REPOSITORY}/actions/workflows/nightly.yml/runs?branch=main&status=success&per_page=1" \
    --jq '.workflow_runs[0].head_sha // ""')"
  if [[ "${previous_sha}" == "${SOURCE_SHA}" ]]; then
    printf 'publish=false\n' >> "${GITHUB_OUTPUT}"
    exit 0
  fi
fi

ci_sha="$(gh api \
  "/repos/${REPOSITORY}/actions/workflows/ci.yml/runs?branch=main&event=push&status=success&per_page=1" \
  --jq '.workflow_runs[0].head_sha // ""')"
[[ "${ci_sha}" == "${SOURCE_SHA}" ]] || exit 1
printf 'publish=true\n' >> "${GITHUB_OUTPUT}"
```

- [ ] **Step 4: Run focused tests**

Run the same Pytest command. Expected: all preflight behavior tests pass.

- [ ] **Step 5: Commit the tested preflight unit**

```bash
git add .github/scripts/nightly-preflight.sh tests/automation/test_nightly_preflight.py
git commit -m "ci: add tested nightly eligibility checks"
```

### Task 2: Nightly Publication Workflow

**Files:**
- Create: `.github/workflows/nightly.yml`

**Interfaces:**
- Consumes: preflight outputs `publish` and `source_sha`; repository variable `DOCKERHUB_USERNAME`; repository secret `DOCKERHUB_TOKEN`.
- Produces: `${DOCKERHUB_USERNAME}/instaloader-webui:nightly` with OCI metadata, SBOM, and provenance.

- [ ] **Step 1: Implement the dedicated workflow**

The workflow shape is:

```yaml
name: Nightly
on:
  schedule:
    - cron: "0 18 * * *"
  workflow_dispatch:
permissions:
  contents: read
  actions: read
concurrency:
  group: nightly-${{ github.repository }}
  cancel-in-progress: false
```

Create a preflight job that exposes the script outputs, then a conditional
publication job using the same pinned Docker actions and build settings as
`release.yml`. Pass `GH_TOKEN: ${{ github.token }}` only to the preflight step.

- [ ] **Step 2: Run preflight behavior tests**

```bash
.venv/bin/python -m pytest --no-cov tests/automation -q
```

Expected: all automation tests pass.

- [ ] **Step 3: Lint the workflow contract**

```bash
actionlint .github/workflows/nightly.yml
```

Expected: no diagnostics.

- [ ] **Step 4: Verify immutable action pins and the single tag policy**

```bash
if rg 'uses:' .github/workflows/nightly.yml | rg -v '@[0-9a-f]{40}( |$)'; then exit 1; fi
if rg 'type=(semver|raw),.*(latest|version)' .github/workflows/nightly.yml; then exit 1; fi
```

Expected: both checks exit successfully without output.

- [ ] **Step 5: Commit the workflow**

```bash
git add .github/workflows/nightly.yml
git commit -m "ci: publish nightly Docker image from main"
```

### Task 3: Operator Documentation and Final Verification

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: the final workflow behavior and `nightly` tag.
- Produces: deployment instructions for maintainers and testers.

- [ ] **Step 1: Document nightly behavior**

Add the schedule, manual trigger, unchanged-commit skip, CI gate, platforms,
mutable tag policy, and this deployment override:

```text
IW_IMAGE=z21012101/instaloader-webui:nightly
```

- [ ] **Step 2: Run repository verification**

```bash
.venv/bin/python -m pytest --no-cov tests/automation backend/tests -q
npx -y -p node@22 node node_modules/vitest/vitest.mjs run
npx -y -p node@22 node node_modules/eslint/bin/eslint.js .
npx -y -p node@22 node node_modules/vite/bin/vite.js build
actionlint .github/workflows/ci.yml .github/workflows/release.yml .github/workflows/nightly.yml
git diff --check
```

Expected: every command succeeds. Container smoke tests remain the responsibility
of GitHub CI when the local Docker engine is unavailable.

- [ ] **Step 3: Review the branch diff and commit documentation**

```bash
git diff origin/main...HEAD
git status --short
git add README.md
git commit -m "docs: describe nightly image deployment"
```

- [ ] **Step 4: Push and open the pull request**

```bash
git push -u origin codex/nightly-image
gh pr create --base main --head codex/nightly-image \
  --title "ci: publish nightly Docker image" \
  --body-file /tmp/nightly-pr-body.md
```

Expected: GitHub returns the new pull request URL.
