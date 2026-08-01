# Migration Logging Fix Report

## Root Cause

The ini-backed Alembic path loads `backend/alembic.ini` through
`logging.config.fileConfig()`. Its default `disable_existing_loggers=True`
disabled application loggers that are not named in that configuration,
including `instaloader_webui.services.job_runner`.

Production `run_migrations()` uses a bare Alembic `Config`, so it has no
`config_file_name` and never calls `fileConfig()`; its new characterization
test confirms that it already preserves application logger state. The affected
path is therefore ini-backed in-process migration use, such as the CLI and
integration migration path.

## Regression and Fix

The ini-backed integration regression creates an enabled job-runner sentinel
logger with a warning level, disabled propagation, and a sentinel handler. It
runs a real Alembic upgrade and asserts that all of those logger-state values
remain unchanged. Before the fix it failed because `disabled` changed from
`False` to `True`.

A separate characterization uses the actual `run_migrations()` bare-Config
path and makes the same assertion. It was green before any round-two
production change because that path never loads the logging file.

`backend/migrations/env.py` calls `fileConfig()` with
`disable_existing_loggers=False`, preserving already-created application
loggers while retaining the Alembic logging configuration. This is defense in
depth for every ini-backed in-process migration use; it does not change the
already-isolated production bare-Config path.

## Verification

- RED: ini-backed focused regression failed with `disabled: False -> True`
  before the production change.
- GREEN: ini-backed focused regression passed (1 passed).
- Production bare-Config characterization: 1 passed without a production
  change in this round.
- Story migration integration suite: 4 passed before the characterization was
  added.
- Task 8 job-runner media/logging suite: 10 passed.
- Full backend suite: 240 tests, 0 failures, 0 errors, 0 skipped (fresh JUnit
  output from `pytest --no-cov -q`).
- `ruff check migrations/env.py tests/integration/test_story_media_migration.py`:
  passed.
- `git diff --check`: clean.

## Scope

Only the Alembic environment, its migration integration regressions, and this
report are included. The fix does not log configuration values or secrets.
