# Global Instagram Cooldown Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the per-profile 25-item cap with a five-minute time slice and prevent rate-limit cascades with a persistent account/IP-wide cooldown.

**Architecture:** `ProfileSyncCoordinator` will use injected monotonic time and pacing callbacks, keeping Stories exempt while time-slicing Posts/Reels. A schema-free `InstagramCooldownStore` will atomically persist cooldown state under `/data/state`; `JobRunner` will activate/reset it, and `JobRepository.claim_next()` will skip Instagram jobs while allowing local deletion work.

**Tech Stack:** Python 3.12, Instaloader 4.15.3, SQLAlchemy, SQLite, Pytest.

## Global Constraints

- Preserve the exact existing SQLite schema.
- Stories remain first and are exempt from the five-minute long-lived-media slice.
- Normal Instaloader query pacing remains enabled.
- Newly saved Posts/Reels receive one-to-three seconds of jitter; existing media do not.
- Cooldown state contains no Cookie values or Instagram response bodies.
- Instagram job types are `profile_sync`, `single_media`, and `followee_discovery`.
- Local `delete_media` and `delete_profile` jobs remain claimable during cooldown.

---

### Task 1: Time-Slice Profile Backfill

**Files:**
- Modify: `backend/src/instaloader_webui/instagram/profile_sync.py`
- Modify: `backend/src/instaloader_webui/instagram/public_adapter.py`
- Modify: `backend/tests/unit/test_profile_sync_coordinator.py`
- Modify: `backend/tests/unit/test_job_runner_media.py`

**Interfaces:**
- Consumes: `monotonic: Callable[[], float]`, `pause_between_new_media: Callable[[], None]`.
- Produces: `ProfileSyncResult.backfill_pending=True` when five minutes elapse, independent of item count.

- [ ] Add failing coordinator tests proving more than 25 fast saves complete, elapsed time pauses before the next long-lived item, Stories do not consume the slice, and pacing occurs only between newly saved long-lived items.
- [ ] Run the focused tests and confirm they fail because the current coordinator still counts 25 saves.
- [ ] Replace `_NEW_LONG_LIVED_MEDIA_LIMIT` with an injected monotonic time slice and pacing callback; update the pending status text.
- [ ] Wire production callbacks to `time.monotonic()` and `time.sleep(random.uniform(1, 3))` in the public adapter.
- [ ] Update the mixed warning/backfill job text and run coordinator plus job-runner tests.

### Task 2: Persist Global Cooldown State

**Files:**
- Create: `backend/src/instaloader_webui/instagram/cooldown.py`
- Create: `backend/tests/unit/test_instagram_cooldown.py`

**Interfaces:**
- Produces: `InstagramCooldownStatus(until: datetime | None, consecutive_rate_limits: int)`.
- Produces: `InstagramCooldownStore.status(now)`, `record_rate_limit(now)`, and `record_success()`.

- [ ] Add failing tests for 30-minute, 1-hour, 2-hour, 4-hour, and capped 6-hour cooldowns; reconstruction persistence; successful reset; expired status; malformed-state rejection; and `0700`/`0600` POSIX modes.
- [ ] Run the new module and confirm import/test failure before implementation.
- [ ] Implement validated JSON parsing plus same-directory temporary write, fsync, `os.replace`, and restrictive permissions at `data_root/state/instagram_cooldown.json`.
- [ ] Run the focused cooldown tests and Ruff/Mypy for the new module.

### Task 3: Activate Cooldown from Job Outcomes

**Files:**
- Modify: `backend/src/instaloader_webui/services/job_runner.py`
- Modify: `backend/tests/unit/test_job_runner_media.py`

**Interfaces:**
- Consumes: `InstagramCooldownStore`.
- Produces: safe terminal 429 text containing cooldown expiry in UTC; successful Instagram jobs reset cooldown strikes.

- [ ] Add failing tests for rate-limited `MediaItemFailure`, rate-limited safe adapter errors, non-rate failures, successful Instagram reset, and local jobs not resetting state.
- [ ] Run focused tests and confirm no cooldown state is currently written.
- [ ] Inject the cooldown store into `JobRunner`, classify only controlled safe rate-limit outcomes, record cooldown before failing the job, and reset after successful Instagram jobs.
- [ ] Run job-runner tests and confirm existing warning/error behavior remains green.

### Task 4: Skip Instagram Jobs During Cooldown

**Files:**
- Modify: `backend/src/instaloader_webui/db/library_repositories.py`
- Modify: `backend/src/instaloader_webui/worker.py`
- Modify: `backend/tests/unit/test_job_issue_repository.py`
- Modify: `backend/tests/unit/test_worker_composition.py`

**Interfaces:**
- Produces: `JobRepository.claim_next(now, excluded_types: Collection[str] = ())`.
- Consumes: active cooldown status to exclude the three Instagram job types.

- [ ] Add a failing repository test with older Instagram jobs and a newer deletion job; expect the deletion job to be claimed while Instagram jobs remain pending.
- [ ] Add a worker composition test proving one shared cooldown store is passed to `JobRunner` and used to calculate excluded claim types.
- [ ] Run the tests and confirm current FIFO claiming selects the Instagram job.
- [ ] Implement excluded-type filtering without changing schema and wire the persistent store into worker composition.
- [ ] Run repository, composition, and scheduler tests.

### Task 5: Full Verification and Pull Request

**Files:**
- Verify all files changed in Tasks 1-4 plus the approved design/plan/runbook documents.

- [ ] Run backend Pytest without coverage, targeted Ruff, and targeted Mypy.
- [ ] Run frontend Vitest, ESLint, and production build to catch integration regressions.
- [ ] Run `git diff --check`, inspect staged filenames, and verify no Cookie file is tracked.
- [ ] Request a read-only code review and fix all important findings.
- [ ] Commit, push `codex/global-instagram-cooldown`, and create a ready PR against `main`.
