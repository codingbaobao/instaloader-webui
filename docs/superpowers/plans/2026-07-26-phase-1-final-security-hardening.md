# Phase 1 Final Security Hardening Implementation Plan

> **Status — superseded for schema work (2026-08-01):** This is a historical
> plan. Do not execute its Alembic, migration, database-upgrade/downgrade, or
> migration-startup instructions. The current pre-1.0 runtime supports only
> the exact fresh schema and does not migrate older databases.

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` or `superpowers:executing-plans`
> to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Close the final public-release authentication, response, dependency,
and container hardening findings without modifying upstream Instaloader.

**Architecture:** Requests pass through pure ASGI body-limit and security-header
middleware before FastAPI parsing. Login admission uses one SQLite-serialized,
all-or-nothing reservation carrying independent account, canonical-IP, and
global HMAC scopes. A provider-owned semaphore bounds Argon2 work, while
Compose supplies trusted-proxy and least-privilege runtime controls.

**Tech Stack:** FastAPI, Pydantic, SQLAlchemy, Alembic, SQLite, Argon2,
HTTPX/pytest, Docker Compose, React/Vite.

## Global Constraints

- Request body maximum: 16,384 bytes.
- Username maximum: 64 UTF-8 bytes; password maximum: 1,024 UTF-8 bytes.
- Account/IP limits: 5 per 15 minutes; global limit: 20 per 15 minutes.
- Argon2 concurrency: 2; throttle storage weighted hard cap: 1,024 rows.
- A successful login clears account failures only. Completed IP/global history
  intentionally remains. Exact account+IP in-flight failures become stale,
  while concurrent successful reservations remain valid.
- No HSTS in the app; the external TLS terminator owns HSTS.
- No upstream `instaloader/` edits, no Phase 2 work, no push.

---

### Task 1: Request and response boundaries

**Files:**

- Create: `backend/src/instaloader_webui/api/middleware.py`
- Modify: `backend/src/instaloader_webui/main.py`
- Modify: `backend/src/instaloader_webui/api/routes/auth.py`
- Test: `backend/tests/integration/test_auth_api.py`
- Test: `backend/tests/integration/test_health.py`
- Test: `backend/tests/integration/test_spa.py`

- [ ] Write failing tests for oversized declared and streamed bodies.
- [ ] Verify both fail because requests reach FastAPI.
- [ ] Implement pure ASGI prebuffer/replay with stable `413 request_too_large`.
- [ ] Write failing tests for safe 500 envelopes, all security headers,
      auth `no-store`, and stale-cookie deletion.
- [ ] Implement safe contextual exception logging and response hardening.
- [ ] Run the focused tests under `-W error`.

### Task 2: Credential and Argon2 resource bounds

**Files:**

- Modify: `backend/src/instaloader_webui/config.py`
- Modify: `backend/src/instaloader_webui/api/routes/auth.py`
- Modify: `backend/src/instaloader_webui/auth/passwords.py`
- Modify: `backend/src/instaloader_webui/services/auth_service.py`
- Test: `backend/tests/unit/test_passwords.py`
- Test: `backend/tests/integration/test_auth_api.py`

- [ ] Write failing UTF-8 byte-limit and malformed-hash regressions.
- [ ] Write a deterministic failing concurrency test proving more than two
      Argon2 operations currently overlap.
- [ ] Implement explicit byte validators, fixed dummy verification, and a
      provider-owned two-slot semaphore.
- [ ] Add safe overload mapping and admission cancellation.
- [ ] Run focused tests under `-W error`.

### Task 3: Multi-scope persistent throttle

**Files:**

- Create: `backend/migrations/versions/0005_multi_scope_login_reservations.py`
- Modify: `backend/src/instaloader_webui/db/models.py`
- Modify: `backend/src/instaloader_webui/db/repositories.py`
- Modify: `backend/src/instaloader_webui/auth/throttle.py`
- Test: `backend/tests/unit/test_login_throttle.py`
- Test: `backend/tests/unit/test_auth_service.py`
- Test: `backend/tests/integration/test_database.py`

- [ ] Write failing account-spray, IP-spray, distributed-global,
      canonicalization, pruning, capacity, and no-leak tests.
- [ ] Verify current combined-bucket behavior fails those regressions.
- [ ] Add the multi-scope transient reservation migration.
- [ ] Implement one-transaction prune/check/cap/reserve and three-scope
      failure completion.
- [ ] Preserve late-failure invalidation and concurrent-success regressions.
- [ ] Run focused concurrency and migration tests under `-W error`.

### Task 4: Container, proxy, and dependency hardening

**Files:**

- Modify: `backend/pyproject.toml`
- Modify: `docker/Dockerfile`
- Modify: `compose.yaml`
- Modify: `.env.example`
- Modify: `README.md`
- Test: `tests/container/test_compose_smoke.py`

- [ ] Write failing compose/smoke assertions for trusted proxies, umask,
      read-only rootfs, tmpfs, dropped capabilities, no-new-privileges, and
      SQLite file modes.
- [ ] Implement the minimal runtime and documentation changes.
- [ ] Upgrade pytest to `>=9.0.3,<10` with compatible pytest-cov.
- [ ] Run Compose config and all real container smoke tests.

### Task 5: Release verification and handoff

- [ ] Run full backend pytest under `-W error` with coverage at least 80%.
- [ ] Run Ruff and mypy.
- [ ] Run frontend tests, lint, build, and high-severity audit.
- [ ] Run project/runtime Python audits and real container smoke tests.
- [ ] Verify no secrets/log leakage, unrelated Docker resources, upstream
      Instaloader changes, or unexpected worktree edits.
- [ ] Append exact evidence and deviations to
      `.superpowers/sdd/2026-07-26-phase-1-foundation-auth/final-fix-1-report.md`.
- [ ] Complete code and security reviews, then create cohesive conventional
      commits without pushing.
