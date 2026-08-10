# Resumable Profile Sync and Direct Viewer Anchor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resume large profile backfills safely while collecting recent Posts and Reels fairly, and open an anchored viewer without an initial rollback animation.

**Architecture:** Keep the current SQLite schema and use complete media records as durable checkpoints. Merge the Reel and Post iterators lazily by publication time, stop after 25 newly saved long-lived items, and treat Instagram blocking outcomes as terminal for the run. Force only the viewer's initial anchor positioning to use non-smooth scrolling.

**Tech Stack:** Python 3.12, Instaloader 4.15.3, SQLAlchemy, Pytest, React 19, TypeScript, Vitest, Testing Library.

## Global Constraints

- Do not change the SQLite schema.
- Do not commit `cookie.txt` or `cookie.txt:Zone.Identifier`.
- Preserve Story-first processing and unbounded browser pagination.
- Write and run each regression test before its production change.

---

### Task 1: Protect Cookie Files

**Files:**
- Modify: `.gitignore`

**Interfaces:**
- Consumes: root Cookie filenames supplied by the user.
- Produces: Git ignore coverage for both root files.

- [ ] Add exact root ignore patterns for `cookie.txt` and `cookie.txt:Zone.Identifier`.
- [ ] Run `git status --short` and confirm neither file appears.

### Task 2: Classify Blocking Instagram Outcomes

**Files:**
- Modify: `backend/src/instaloader_webui/instagram/media_processor.py`
- Modify: `backend/src/instaloader_webui/instagram/profile_sync.py`
- Test: `backend/tests/unit/test_media_processor.py`
- Test: `backend/tests/unit/test_profile_sync_coordinator.py`

**Interfaces:**
- Consumes: `AbortDownloadException` and `MediaItemFailure.issue.error_code`.
- Produces: safe `MediaItemFailure` classification and immediate propagation of blocking issue codes.

- [ ] Add a failing processor test where candidate resolution raises `AbortDownloadException("challenge_required")`; expect a safe `MediaItemFailure` with `challenge_required` and no raw message.
- [ ] Run that test and confirm the raw exception escapes before the fix.
- [ ] Catch `AbortDownloadException` beside `InstaloaderException` in resolve and download boundaries.
- [ ] Run the processor test and confirm it passes.
- [ ] Add a failing coordinator test where a rate-limited item precedes another item; expect the blocking failure to escape and the later item not to run.
- [ ] Run the test and confirm the coordinator currently records a warning and continues.
- [ ] Re-raise blocking issue codes while retaining warning continuation for item-local outcomes.
- [ ] Run both targeted test modules.

### Task 3: Stream and Bound Historical Backfill

**Files:**
- Modify: `backend/src/instaloader_webui/instagram/media_types.py`
- Modify: `backend/src/instaloader_webui/instagram/public_adapter.py`
- Modify: `backend/src/instaloader_webui/instagram/profile_sync.py`
- Test: `backend/tests/unit/test_profile_sync_coordinator.py`

**Interfaces:**
- Produces: `MediaCandidate.published_at_hint: datetime | None` and `ProfileSyncResult.backfill_pending: bool`.
- Consumes: `MediaProcessResult.status` to count only newly saved items against the 25-item budget.

- [ ] Add a failing test with interleaved Post/Reel timestamps; expect newest-first processing and Reel precedence for a shared shortcode.
- [ ] Add a failing test with existing and saved outcomes; expect the run to stop after 25 saved items, skip existing items in the budget, and return `backfill_pending=True`.
- [ ] Run both tests and confirm the current Reels-first, unbounded coordinator fails them.
- [ ] Add publication hints to profile Post/Reel candidates.
- [ ] Implement a lazy two-stream merge with shortcode deduplication and per-kind progress.
- [ ] Stop after 25 newly saved long-lived items and publish a backfill-pending status without limiting Stories.
- [ ] Update result construction and focused tests, then run the coordinator and job-runner test modules.

### Task 4: Jump Directly to the Viewer Anchor

**Files:**
- Modify: `frontend/src/library/MediaViewerPage.tsx`
- Test: `frontend/src/library/MediaViewerPage.test.tsx`

**Interfaces:**
- Produces: an initial scroll jump performed while the track has inline `scroll-behavior: auto`.
- Preserves: smooth `moveTo()` navigation after initialization.

- [ ] Add a failing viewer test that supplies a middle anchor, sets a nonzero viewport height before layout, and records the scroll behavior active during the initial jump.
- [ ] Run that test and confirm the current `scrollTop` assignment inherits smooth CSS behavior.
- [ ] Add a small direct-jump helper that temporarily disables smooth scrolling, positions the track, and restores its previous inline style after the jump.
- [ ] Run the anchor test and existing route-reuse/windowing tests.

### Task 5: Full Verification and NAS Cookie Import

**Files:**
- No tracked production files beyond Tasks 1-4.

**Interfaces:**
- Consumes: the ignored local Cookie and the NAS encrypted session service.
- Produces: validated NAS session metadata without exposing Cookie values.

- [ ] Run the full backend Pytest suite, Ruff, and Mypy.
- [ ] Run the full frontend Vitest suite, ESLint, and production build.
- [ ] Inspect `git diff --check`, `git status --short`, and staged content for Cookie leakage.
- [ ] Stream `cookie.txt` into `InstagramSessionService.import_netscape()` inside the NAS web container; print only configured/imported/validated metadata.
- [ ] Recheck NAS health and worker status without triggering a profile sync during an Instagram cooldown.
