# Nightly Docker Image Design

## Goal

Publish a tested snapshot of `main` to Docker Hub every night so maintainers can
deploy current development code without creating a stable release.

## Workflow Boundary

Add a dedicated `.github/workflows/nightly.yml`. Keep `ci.yml` and
`release.yml` unchanged so nightly publication cannot affect Release Please or
the stable release path.

The nightly workflow has only `contents: read` and `actions: read` repository
permissions. Docker Hub credentials remain repository-level configuration and
are used only by the publication job after all preflight checks pass.

## Triggers and Source

- Run on `workflow_dispatch` for an explicit maintainer rebuild.
- Run daily at `0 18 * * *`, which is 02:00 the next day in Asia/Taipei.
- Reject any run whose triggering ref is not exactly `refs/heads/main`.
- Build the immutable `github.sha` captured when the run was triggered. Do not
  follow a moving branch after the run enters the queue.
- Serialize workflow runs with one `queue: max` concurrency group so schedule
  and manual requests wait instead of replacing a pending publication.

## Preflight Rules

A focused `.github/scripts/nightly-preflight.sh` script owns eligibility
checks. Keeping this logic outside YAML makes the behavior locally testable.

For a scheduled run:

1. Read the most recent successful `nightly.yml` workflow run on `main`.
2. If its head SHA equals the current source SHA, emit `publish=false` and
   finish successfully without logging in to Docker Hub.
3. Otherwise, continue to the CI gate.

A manual run deliberately bypasses only the unchanged-commit check, allowing a
maintainer to rebuild the same commit.

Before every actual publication, read the most recent successful `ci.yml` push
run on `main`. Its head SHA must exactly equal the nightly source SHA. A missing,
pending, failed, cancelled, or older CI result fails preflight and prevents any
registry operation. A later schedule or manual dispatch can retry the commit.

The preflight script uses the GitHub CLI already installed on GitHub-hosted
Ubuntu runners and authenticates it with the run's `GITHUB_TOKEN`. It writes
`publish` and `source_sha` outputs through `GITHUB_OUTPUT`.

## Image Publication

When `publish=true`, the publication job:

1. Checks out the exact preflight `source_sha`.
2. Logs in with `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN`.
3. Configures QEMU and Docker Buildx.
4. Uses the same Dockerfile and immutable action revisions as the stable
   release workflow.
5. Builds and pushes `linux/amd64` and `linux/arm64`.
6. Pushes only `${DOCKERHUB_USERNAME}/instaloader-webui:nightly`.
7. Publishes OCI labels, an SBOM, and maximum BuildKit provenance.
8. Reuses GitHub Actions layer caching.

The mutable `nightly` tag always represents the latest successfully published
snapshot. No dated or commit-specific nightly tags are retained. The OCI
revision label records the exact source commit. Stable `latest`, SemVer, and
major/minor tags remain exclusively owned by `release.yml`.

## Failure Handling

- A non-`main` manual dispatch fails before registry login.
- A GitHub API or CI-gate failure fails preflight and leaves Docker Hub
  unchanged.
- A scheduled run with no new commit succeeds with the publication job skipped.
- A multi-platform build or push failure fails the workflow and does not count
  as a successful nightly; the next run retries because its SHA differs from
  the last successful workflow run.
- Concurrent schedule and manual events are serialized so they cannot race to
  update `nightly`.

## Verification and Documentation

Add contract and behavior tests that cover:

- rejecting non-`main` refs;
- scheduled unchanged-commit skipping;
- manual same-commit rebuilding;
- exact-SHA CI gating;
- trigger time, permissions, concurrency, platforms, immutable action pins,
  metadata, and the single `nightly` tag.

Run the new tests, `actionlint`, repository test suites that do not require an
unavailable local Docker engine, `git diff --check`, and a final status review.
Document the schedule, manual rebuild behavior, CI gate, image tag, supported
platforms, and deployment override in `README.md`.
