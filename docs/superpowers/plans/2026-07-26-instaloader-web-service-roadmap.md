# Instaloader Personal Web Service Roadmap

> **Status — superseded for schema work (2026-08-01):** This is a historical
> plan. Do not execute its Alembic, migration, database-upgrade/downgrade, or
> migration-startup instructions. Current pre-1.0 builds support only fresh
> SQLite databases; for a schema-marker change, follow the
> [current policy](../../../README.md#pre-10-database-schema-policy).

> **For agentic workers:** Execute each phase from its own detailed implementation plan. Do not begin a later phase until the preceding phase passes its review and verification gate.

**Goal:** Deliver the approved personal Instagram archive WebUI as four independently testable increments.

**Architecture:** One multi-stage image runs as separate `web` and `worker` services with a shared `/data` volume. FastAPI owns HTTP APIs and the compiled React UI; the worker owns Instaloader activity, scheduling, downloads, and bulk filesystem work.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, SQLAlchemy, Alembic, SQLite WAL, Argon2id, React, TypeScript, Vite, Vitest, Playwright, pytest, Docker Compose.

## Global Constraints

- Treat `C:\Users\z2101\instaloader\instaloader-webui` as the project root.
- Do not modify any file under the outer repository's `instaloader/` package.
- Keep all persistent runtime state beneath `/data`.
- Ship HTTP only; do not add TLS, Nginx, Caddy, Traefik, or tunnel services.
- Support exactly one WebUI administrator and one active Instagram session.
- Use tests before production implementation and maintain at least 80% coverage.
- Use immutable domain values and schema validation at every external boundary.
- Never persist or log plaintext passwords, 2FA codes, cookie imports, session cookies, or authorization headers.
- Use a single worker process and a persistent SQLite-backed job queue.
- Posts and Reels default to enabled; Tagged and Stories default to disabled.
- Use one global synchronization interval, defaulting to six hours.

---

## Phase 1: Application Foundation and Administrator Authentication

Deliver a runnable FastAPI and React application with:

- Configuration validation.
- Consistent API envelopes.
- SQLite WAL and Alembic migrations.
- Administrator bootstrap from environment or Docker secret.
- Argon2id password hashing.
- Opaque server-side browser sessions.
- CSRF protection and login throttling.
- First-login forced password change.
- Responsive authenticated application shell.
- Multi-stage Docker image and `web` service smoke test.

Detailed plan:

`docs/superpowers/plans/2026-07-26-phase-1-foundation-auth.md`

Exit gate:

- Backend, frontend, integration, coverage, and Docker smoke tests pass.
- No Instagram credential or network code exists yet.
- An administrator can bootstrap, log in, change the initial password, reload the page, and log out.

## Phase 2: Instagram Session, Persistent Jobs, and Download Worker

Deliver:

- AES-GCM encrypted Instagram session storage.
- Username/password and 2FA state machine.
- Validated Cookie JSON, Netscape `cookies.txt`, and raw `Cookie` header import.
- Instaloader public-API adapter and fixture-backed contract tests.
- Persistent SQLite job queue and restart recovery.
- Smart input parsing and preview for username, Profile URL, Post URL, Reel URL, and TV URL.
- Individual post and reel downloads through staging and atomic finalization.
- Profile tracking and global incremental synchronization.
- Global Posts, Reels, Tagged, and Stories settings.

Exit gate:

- Worker and Web API coordinate through SQLite without Redis.
- A fixture-backed individual Reel job survives worker restart and finalizes exactly once.
- No untrusted pickle is accepted.
- Session expiry, private profile, checkpoint, rate limit, and disk errors become stable user-facing error codes.

## Phase 3: Media Library API and Approved React Experience

Deliver:

- Profile, media, asset, relation, story-group, and activity APIs.
- Cursor pagination and deterministic ordering.
- Authenticated media streaming with byte ranges.
- Hybrid home page with Profile shortcuts and recent content.
- Instagram-style Profile page with Posts, Reels, Tagged, and Story archive navigation.
- Smart-add bottom sheet with normalized preview.
- Image, video, and carousel viewer with caption and source metadata.
- Activity page with persistent job progress.
- Global synchronization settings UI.
- Mobile bottom navigation and desktop sidebar.

Exit gate:

- Approved mobile and desktop flows pass Playwright tests.
- A media item shared through owned, tagged, and manual relations uses one physical asset set.
- Direct unauthenticated access to media is rejected.

## Phase 4: Deletion, Hardening, and Release Verification

Deliver:

- Media `ignored` tombstones and complete-removal semantics.
- Profile archive and persistent bulk deletion.
- Restart-safe, idempotent deletion progress.
- Shared-asset reference protection.
- Retry UI for deletion and worker failures.
- Password reset management CLI and browser-session revocation.
- Allowed-host, trusted-proxy, secure-cookie, CSP, and redaction hardening.
- Storage diagnostics and audit events.
- Full Docker Compose with `web` and `worker`.
- End-to-end, security, container restart, and coverage verification.
- Deployment and operations documentation emphasizing the HTTP-only boundary.

Exit gate:

- Every acceptance criterion in the approved design spec has a passing automated or documented manual verification.
- Test coverage is at least 80%.
- Both containers run as non-root and preserve all state through `/data`.
- The release contains no Node build cache, development dependency, secret, unsafe source map, or pickle-loading endpoint.
