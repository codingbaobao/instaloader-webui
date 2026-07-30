# GitHub CI and Docker Release Automation Design

## Goal

Prepare Instaloader WebUI for public GitHub hosting with an MIT license,
continuous validation, continuous secret scanning, and an approval-gated
Semantic Versioning workflow that publishes multi-platform images to Docker
Hub.

All repository changes are prepared and verified locally. Nothing is pushed to
GitHub as part of this work.

## Repository Baseline

- GitHub repository: `codingbaobao/instaloader-webui`
- Docker Hub image: `codingbaobao/instaloader-webui`
- Default branch: `main`
- Current application version: `0.1.0`
- Release baseline commit:
  `cfa23307fb2489a87cd603ec40c853fa122248a8`
- Backend runtime: Python 3.12
- Frontend runtime: Node.js 22
- Container platforms: `linux/amd64` and `linux/arm64`

## Licensing

Add the standard MIT license as `LICENSE` with:

```text
Copyright (c) 2026 codingbaobao
```

The README will identify the project as MIT licensed. Instaloader 4.15.2 is
also MIT licensed, but remains a separately maintained dependency.

## Continuous Integration

Create `.github/workflows/ci.yml`, triggered for pull requests and pushes to
`main`. Give the workflow only `contents: read` permission. Jobs may run in
parallel and must have explicit timeouts.

### Secret scan

- Check out complete Git history with `fetch-depth: 0`.
- Run Gitleaks against the repository and its history.
- Do not upload a secret-containing report as an Actions artifact.
- The scan requires no repository secret.
- Run the same full-history scan locally before completion.

### Backend validation

- Use CPython 3.12.
- Install the backend with its `test` extra.
- Run Ruff against `src`, `tests`, and `migrations`.
- Run Mypy against `src`.
- Run the complete backend Pytest suite with its configured 80% coverage
  threshold.

### Frontend validation

- Use Node.js 22 with npm dependency caching keyed by
  `frontend/package-lock.json`.
- Run `npm ci`.
- Run the Vitest suite with coverage.
- Run ESLint.
- Run the TypeScript and Vite production build.

### Container validation

- Build the application image from `docker/Dockerfile`.
- Validate the resolved Compose configuration.
- Run the existing Compose container smoke tests against the locally built
  image, never the mutable Docker Hub `latest` image.
- Preserve the existing non-root, read-only filesystem, dropped capability,
  persistence, restart, and health-check assertions.

All external GitHub Actions are pinned to immutable full commit SHAs with a
comment identifying the corresponding release version.

## Semantic Versioning and Release Approval

Use Release Please in manifest mode. Add:

- `release-please-config.json`
- `.release-please-manifest.json`
- Release Please configuration in `.github/workflows/release.yml`

The repository is one releasable component, regardless of whether a commit
changes the backend, frontend, Docker, or documentation.

Release Please uses the default Semantic Versioning strategy:

- `fix:` produces a patch bump.
- `feat:` produces a minor bump.
- A `!` after the type or a `BREAKING CHANGE:` footer produces a major bump.
- Non-releasable types such as `chore:` and `ci:` do not cause a release on
  their own.

The release PR is maintained in place. Additional qualifying commits merged
into `main` update the existing release PR, its target version, and its
CHANGELOG. Merging the release PR creates the version tag and GitHub Release.

Squash merge is recommended so the final commit title on `main` is the
intentional Conventional Commit entry used in release notes.

### Version files

Release Please keeps these values synchronized:

- `.release-please-manifest.json`
- `backend/pyproject.toml` at `project.version`
- `frontend/package.json` at `version`
- `frontend/package-lock.json` at its root package version fields

Release Please creates and subsequently maintains `CHANGELOG.md`.

The Dockerfile must not duplicate the project version. It installs the
application wheel produced by the build stage without an exact version
literal.

### Initial release

Treat the existing `0.1.0` source state as the release baseline. Configure
Release Please with the baseline commit above so historical feature commits are
not counted again.

After the repository is first pushed, the maintainer may publish a one-time
GitHub Release tagged `v0.1.0`. That release publishes the initial Docker image.
All later version bumps and GitHub Releases are created by merging Release
Please PRs.

## Release Workflow

Create `.github/workflows/release.yml` with two independently permissioned jobs.

### Release Please job

On a push to `main`:

- Run Release Please in manifest mode.
- Use `RELEASE_PLEASE_TOKEN`, not the default `GITHUB_TOKEN`, so the generated
  release PR triggers the normal CI workflow.
- Grant only the permissions Release Please needs: repository contents,
  pull requests, and issues read/write.

The fine-grained token must be scoped to this repository, have an expiration
date, and be stored as the GitHub Actions secret `RELEASE_PLEASE_TOKEN`.

### Docker publication job

On a published GitHub Release:

- Reject tags that do not exactly match stable `vMAJOR.MINOR.PATCH` syntax.
- Check out the tagged source.
- Log in using repository variable `DOCKERHUB_USERNAME` and repository secret
  `DOCKERHUB_TOKEN`.
- Build `linux/amd64` and `linux/arm64` using Docker Buildx and QEMU.
- Push the exact version tag, the `MAJOR.MINOR` tag, and `latest`.
- Add OCI source, revision, version, title, description, and license labels.
- Publish an SBOM and BuildKit provenance attestation with the image.
- Use GitHub Actions cache storage for Docker layers.
- Never log credential values.

Pull requests and ordinary branch pushes never receive Docker Hub credentials
and never push an image.

## Docker Compose

The base `compose.yaml` becomes the deployment definition and defaults to:

```yaml
${IW_IMAGE:-codingbaobao/instaloader-webui:latest}
```

Both `web` and `worker` continue to run the exact same image. Operators can pin
a release by setting, for example:

```text
IW_IMAGE=codingbaobao/instaloader-webui:0.1.0
```

Remove source build instructions from the base Compose file. Add
`compose.build.yaml` as an explicit local-development and test override that
restores the current Docker build configuration and uses
`instaloader-webui:local`.

Production deployment uses:

```text
docker compose pull
docker compose up -d
```

Local source builds use both Compose files and `--build`.

## Documentation

Update `README.md` to cover:

- Docker Hub deployment and release pinning
- local source builds through the override file
- MIT licensing
- Conventional Commit examples
- Release PR lifecycle
- required GitHub variable and secrets
- the one-time `v0.1.0` bootstrap release
- recommended GitHub repository settings:
  - allow Actions to create pull requests
  - require CI checks before merging
  - use squash merge
  - enable GitHub secret scanning and push protection

No real credential or placeholder credential value is committed.

## Failure Handling and Security Boundaries

- A failed CI job blocks merge when the documented branch rule is enabled.
- Release Please never publishes Docker directly; publication begins only
  after a GitHub Release is published.
- A failed multi-platform build or registry push fails the release workflow
  without changing source code or version files.
- Docker Hub credentials are unavailable to forked pull requests.
- Release Please and Docker Hub tokens are separate and independently
  revocable.
- The Compose test path explicitly selects the local image to avoid validating
  an unrelated mutable registry image.

## Verification

Before completion, run and inspect:

1. A Gitleaks scan over the complete Git history and working tree.
2. Repository automation contract tests written before the new configuration.
3. Backend Pytest, Ruff, and Mypy.
4. Frontend Vitest, ESLint, and production build.
5. Docker image build and Compose container smoke tests.
6. `docker compose config` for deployment and local-build configurations.
7. `actionlint` against both workflows.
8. JSON parsing/schema-oriented checks for Release Please configuration.
9. `git diff --check`.
10. `git status` to confirm no generated credentials, data, or build outputs
    are staged.

The final handoff lists the GitHub secrets, variable, and repository settings
the maintainer must configure after pushing. The work remains local and no
GitHub or Docker Hub publication occurs.
