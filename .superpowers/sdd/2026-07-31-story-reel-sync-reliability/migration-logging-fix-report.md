# Migration Logging Fix Report

## Root Cause

Alembic loads `backend/alembic.ini` through `logging.config.fileConfig()`.
Its default `disable_existing_loggers=True` disabled application loggers that
are not named in that configuration, including
`instaloader_webui.services.job_runner`.  As a result, structured worker
warnings disappeared after a migration ran in the same process.

## Regression and Fix

The new integration regression creates an enabled job-runner sentinel logger
with a warning level, disabled propagation, and a sentinel handler.  It runs a
real Alembic upgrade and asserts that all of those logger-state values remain
unchanged.  Before the fix it failed because `disabled` changed from `False` to
`True`.

`backend/migrations/env.py` now calls `fileConfig()` with
`disable_existing_loggers=False`, preserving already-created application
loggers while retaining the Alembic logging configuration.

## Verification

- RED: focused regression failed with `disabled: False -> True` before the
  production change.
- GREEN: focused regression passed (1 passed).
- Story migration integration suite: 4 passed.
- Task 8 job-runner media/logging suite: 10 passed.
- Full backend suite: 239 tests, 0 failures, 0 errors, 0 skipped (fresh JUnit
  output from `pytest --no-cov -q`).
- `ruff check migrations/env.py tests/integration/test_story_media_migration.py`:
  passed.
- `git diff --check`: clean.

## Scope

Only the Alembic environment, its migration integration regression, and this
report are included.  The fix does not log configuration values or secrets.
