# Task 7 Report: Story-First Resilient Profile Sync

## Delivered

- Added `ProfileSyncCoordinator` and immutable `ProfileSyncResult`.
- Materializes and deduplicates the Story manifest before processing any
  Story; only after all Story outcomes does it materialize Reels and Posts.
- Deduplicates Reels and Posts by shortcode value with Reel precedence, and
  deduplicates repeated Story media IDs while preserving source order.
- Keeps totals null until both long-lived manifests finish, then reports the
  exact total. Every saved, existing, or failed item advances current once.
- Polls Stop Sync only immediately before processing a manifest item.
- Catches only `MediaItemFailure` around `processor.process()`. The injected
  issue callback is the single record/log boundary; iterator, callback,
  filesystem, database, and other processor failures remain fatal.
- Replaced the legacy Post-first facade loop with strict profile-sync
  preflight: required authenticated session, Profile metadata, usable avatar,
  metadata persistence, then Story-first coordinator execution.
- Kept Task 6 direct-media Profile reuse unchanged. Strict avatar/session
  preflight applies only to tracked Profile sync.
- Added four-argument phase progress and issue injection for Task 8 while
  retaining compatibility with existing three-argument progress callbacks.
- A completed coordinator result, including warnings or a boundary stop,
  records `last_sync_succeeded_at`; every fatal preflight/iterator failure
  records only the attempted timestamp.

## RED Evidence

Initial coordinator test:

```text
ModuleNotFoundError: No module named 'instaloader_webui.instagram.profile_sync'
1 error in 0.08s
```

After the coordinator reached green, the strict facade cases were added before
the adapter change:

```text
TypeError: PublicInstaloaderAdapter.__init__() got an unexpected keyword argument 'issue'
5 failed, 7 passed in 0.81s
```

## GREEN and Verification Evidence

Coordinator-only initial green:

```text
7 passed in 0.02s
```

Brief-required coordinator, Story adapter, and processor suite:

```text
59 passed in 7.03s
```

Fresh full backend after the final mutation-strengthening test:

```text
228 passed in 28.54s
```

Fresh scoped static verification:

```text
ruff: All checks passed!
mypy: Success: no issues found in 3 source files
git diff --check: clean
```

Every pytest command used `backend/.venv` with:

```text
TMPDIR=/var/tmp TEMP=/var/tmp TMP=/var/tmp
```

## Test and Mutation Coverage

- Scanning Reels/Posts before processing Stories breaks the exact event trace.
- Dedupe by `(kind, shortcode)` or Post-first insertion breaks the captured
  candidate kind for the shared shortcode.
- Omitting Story-ID dedupe breaks the repeated Story outcome count.
- Publishing a total during Story work or before both feed scans breaks the
  exact progress sequence.
- Advancing only downloads breaks existing-item progress; skipping the failure
  increment breaks the warning progress case.
- Catching broad processor exceptions breaks the `OSError` fatal case.
- Processing a partial iterator manifest breaks the interrupted-Reel case.
- Polling Stop Sync while scanning breaks the source event trace.
- Anonymous preflight, non-image/unreadable avatar continuation, or scanning
  before avatar persistence breaks facade tests.
- Treating warnings as fatal breaks the stored successful-sync timestamp.

## Scope and Concerns

- Task-owned production/test changes are confined to `profile_sync.py`,
  `public_adapter.py`, and `test_profile_sync_coordinator.py`.
- Full-tree `ruff check .` and `mypy src` still report pre-existing findings in
  non-owned files (`library_repositories.py`, `cookie_file.py`,
  `session_store.py`, `job_runner.py`, `profile_avatars.py`, and
  `session_service.py`). Owned-file static checks are clean; unrelated edits
  were not modified.
