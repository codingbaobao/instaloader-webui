# Instaloader PyPI Dependency and Worker Lifecycle Design

## Goal

Replace the Docker build's sibling-source Instaloader dependency with an exact
PyPI dependency and reuse Instaloader clients for the lifetime of the persistent
worker process.

## Dependency and Build

- Pin `instaloader==4.15.3` in `backend/pyproject.toml`.
- Let the backend wheel build resolve the pinned package from PyPI into the
  existing wheel directory.
- Remove the Docker stage that copies and builds the sibling `instaloader/`
  source tree.
- Change the Compose build context from the parent directory to the WebUI
  repository and update Docker `COPY` paths accordingly.
- Keep the runtime installation offline with `--no-index --find-links=/wheels`.

This makes the WebUI repository independently buildable while preserving an
exact Instaloader version in application metadata.

## Worker Runtime

Add a focused `WorkerInstaloaderRuntime` component owned by the persistent
worker. It caches:

- one anonymous `Instaloader` client; and
- at most one authenticated `Instaloader` client for the current encrypted
  session revision.

Each acquisition reloads the encrypted session metadata. If no session exists,
the runtime returns the cached anonymous client. If the session's
`imported_at` revision differs from the cached authenticated revision, the
runtime closes the stale authenticated client, creates a new client, loads the
validated cookies, and caches it. Removing a session switches future work back
to the anonymous client without mutating private Instaloader cookie state.

Before returning a client, the runtime updates its output directory for the
current job. The worker remains single-process and executes one job at a time,
so the reusable client does not require concurrent leases or locking.

## Integration Boundaries

- `worker.py` creates one runtime and closes it in `finally`.
- `JobRunner` injects the runtime into each job-scoped
  `PublicInstaloaderAdapter`.
- `PublicInstaloaderAdapter` asks the runtime for a configured client instead
  of constructing a new `Instaloader` for every operation.
- Cookie import validation remains isolated in `InstagramSessionService` with
  its existing short-lived client. An unvalidated candidate must never enter
  the worker cache.

## Error Handling

- An unreadable encrypted session continues to produce the existing safe
  application error.
- A failed authenticated-client replacement does not overwrite the previously
  cached client revision.
- Replaced authenticated clients are explicitly closed.
- Worker shutdown closes both cached clients exactly once.
- Rate-limit history is intentionally process-local and is not persisted
  across worker or container restarts.

## Verification

This POC does not add or run unit tests, smoke tests, or coverage. Verification
consists of:

- Python syntax compilation for modified backend modules;
- package metadata inspection confirming the exact Instaloader pin;
- a Docker image build proving the repository-only build context and PyPI wheel
  resolution;
- `git diff --check`; and
- independent code review.

